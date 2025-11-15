#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Multi-channel NBFM voice TX for HackRF/Pluto via osmosdr
# GNU Radio 3.9/3.10 compatible.
#
# MP3 files can now be queued directly (requires `pip install audioread`).
# WAV conversions via sox (if needed):
#   sox input1.mp3 -r 48000 -c 1 -b 16 ch1.wav gain -n -3
#   sox input2.mp3 -r 48000 -c 1 -b 16 ch2.wav gain -n -3
# 
# FRS Frequencies 462.5625 462.5875 462.6125 462.6375 462.6625 462.6875
#
# Usage examples:
#   python3 multich_nbfm_tx.py --device hackrf --fc 462.6e6 \
#       --files ch1.wav ch2.wav ch3.wav,ch3b.wav ch4.wav \
#       --freqs 462.5625e6 462.5875e6 462.6125e6 462.6375e6 \
#       --deviation 3e3 --tx-sr 8e6 --tx-gain 0
#
#   python3 multich_nbfm_tx.py --device plutoplussdr --fc 462.6e6 \
#       --files patrol.wav announcements.wav \
#       --offsets -125e2 125e2 --tx-sr 4e6 --tx-gain -10 --no-loop-queue
#
import argparse
import audioop
import math
import time
import wave
from fractions import Fraction
from pathlib import Path
from typing import Any, Iterator, List, Optional, Sequence

try:
    import audioread
except ImportError:  # pragma: no cover - optional dependency
    audioread = None

import numpy as np
import osmosdr
from gnuradio import analog, blocks, filter, gr
from gnuradio.filter import firdes, window


class QueuedAudioSource(gr.sync_block):
    """Source block that streams a queue of audio files sequentially."""

    _CHUNK_FRAMES = 4096

    def __init__(
        self,
        wav_paths: Sequence[Path],
        repeat: bool = True,
        target_sample_rate: Optional[int] = None,
    ):
        if not wav_paths:
            raise ValueError("At least one audio file must be provided for playback")

        gr.sync_block.__init__(
            self,
            name="QueuedAudioSource",
            in_sig=None,
            out_sig=[np.float32],
        )

        self._paths: List[Path] = [Path(p) for p in wav_paths]
        self._repeat = repeat
        self._queue_index = 0
        self._current_wave: Optional[wave.Wave_read] = None
        self._current_reader: Optional[Any] = None
        self._reader_iter: Optional[Iterator[bytes]] = None
        self._reader_buffer = bytearray()
        self._reader_kind: Optional[str] = None
        self._file_sample_rate: Optional[int] = None
        self._ratecv_state = None
        self._pending: np.ndarray = np.empty(0, dtype=np.float32)
        self.sample_rate: Optional[int] = (
            int(target_sample_rate) if target_sample_rate is not None else None
        )

        self._prepare_next_file(initial=True)

    def _close_current(self) -> None:
        if self._current_wave is not None:
            self._current_wave.close()
            self._current_wave = None
        if self._current_reader is not None:
            try:
                self._current_reader.close()
            except Exception:
                pass
            self._current_reader = None
        self._reader_iter = None
        self._reader_buffer.clear()
        self._reader_kind = None

    def _prepare_next_file(self, initial: bool = False) -> bool:
        self._close_current()

        if self._queue_index >= len(self._paths):
            if self._repeat:
                self._queue_index = 0
            else:
                return False

        path = self._paths[self._queue_index]
        self._queue_index += 1

        if not path.exists():
            raise FileNotFoundError(f"Audio file not found: {path}")

        suffix = path.suffix.lower()

        if suffix == ".wav":
            wav_reader = wave.open(str(path), "rb")
            nchannels = wav_reader.getnchannels()
            sampwidth = wav_reader.getsampwidth()
            framerate = wav_reader.getframerate()
            nframes = wav_reader.getnframes()

            if nchannels != 1:
                wav_reader.close()
                raise ValueError(f"{path} must be mono but has {nchannels} channels")
            if sampwidth != 2:
                wav_reader.close()
                raise ValueError(
                    f"{path} must be 16-bit PCM; got sample width {sampwidth} bytes"
                )
            if nframes == 0:
                wav_reader.close()
                if initial:
                    return self._prepare_next_file(initial=False)
                if self._queue_index >= len(self._paths) and not self._repeat:
                    return False
                return self._prepare_next_file(initial=False)

            self._current_wave = wav_reader
            self._reader_kind = "wav"
            framerate = int(framerate)
        elif suffix == ".mp3":
            if audioread is None:
                raise ImportError(
                    "MP3 support requires the 'audioread' package. Install it with "
                    "'pip install audioread'."
                )
            reader = audioread.audio_open(str(path))
            if reader.channels != 1:
                reader.close()
                raise ValueError(f"{path} must be mono but has {reader.channels} channels")
            framerate = int(reader.samplerate)
            if framerate <= 0:
                reader.close()
                raise ValueError(f"Unable to determine sample rate for {path}")

            self._current_reader = reader
            self._reader_iter = reader.read_data(self._CHUNK_FRAMES * 2)
            self._reader_buffer.clear()
            self._reader_kind = "mp3"
        else:
            raise ValueError(f"Unsupported audio format for {path}; use WAV or MP3")

        if self.sample_rate is None:
            self.sample_rate = int(framerate)
        target_sr = int(self.sample_rate)
        if target_sr <= 0:
            raise ValueError(f"Invalid target sample rate {target_sr} Hz")

        self._file_sample_rate = int(framerate)
        self._ratecv_state = None
        self._pending = np.empty(0, dtype=np.float32)
        self.sample_rate = target_sr

        return True

    def _has_active_source(self) -> bool:
        if self._reader_kind == "wav":
            return self._current_wave is not None
        if self._reader_kind == "mp3":
            return self._current_reader is not None or bool(self._reader_buffer)
        return False

    def _read_chunk(self) -> np.ndarray:
        if self._file_sample_rate is None:
            return np.empty(0, dtype=np.float32)

        if self._reader_kind == "wav":
            if self._current_wave is None:
                return np.empty(0, dtype=np.float32)
            raw = self._current_wave.readframes(self._CHUNK_FRAMES)
            if len(raw) == 0:
                self._close_current()
                self._file_sample_rate = None
                self._ratecv_state = None
                return np.empty(0, dtype=np.float32)
        elif self._reader_kind == "mp3":
            target_bytes = self._CHUNK_FRAMES * 2
            while len(self._reader_buffer) < target_bytes:
                if self._reader_iter is None:
                    break
                try:
                    chunk = next(self._reader_iter)
                except StopIteration:
                    self._reader_iter = None
                    break
                if not isinstance(chunk, (bytes, bytearray)):
                    chunk = bytes(chunk)
                if chunk:
                    self._reader_buffer.extend(chunk)
            if not self._reader_buffer:
                self._close_current()
                self._file_sample_rate = None
                self._ratecv_state = None
                return np.empty(0, dtype=np.float32)
            take = min(len(self._reader_buffer), target_bytes)
            if take % 2:
                take -= 1
            if take <= 0:
                if self._reader_iter is None:
                    self._reader_buffer.clear()
                    self._close_current()
                    self._file_sample_rate = None
                    self._ratecv_state = None
                return np.empty(0, dtype=np.float32)
            raw = bytes(self._reader_buffer[:take])
            del self._reader_buffer[:take]
        else:
            return np.empty(0, dtype=np.float32)

        target_sr = int(self.sample_rate) if self.sample_rate is not None else None
        if target_sr is None:
            raise ValueError("Target sample rate not established before reading audio")

        if self._file_sample_rate != target_sr:
            raw, self._ratecv_state = audioop.ratecv(
                raw,
                2,
                1,
                int(self._file_sample_rate),
                target_sr,
                self._ratecv_state,
            )

        data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        return data

    def work(self, input_items, output_items):
        out = output_items[0]
        noutput = len(out)

        produced = 0
        while produced < noutput:
            if self._pending.size == 0:
                if not self._has_active_source():
                    if not self._prepare_next_file():
                        out[produced:noutput] = 0.0
                        return noutput
                chunk = self._read_chunk()
                if chunk.size == 0:
                    if not self._has_active_source():
                        # File exhausted; move to next entry.
                        continue
                else:
                    self._pending = chunk

            if self._pending.size == 0:
                # No data available even after attempting to read; fill remainder with silence.
                out[produced:noutput] = 0.0
                return noutput

            take = min(self._pending.size, noutput - produced)
            out[produced : produced + take] = self._pending[:take]
            produced += take
            if take == self._pending.size:
                self._pending = np.empty(0, dtype=np.float32)
            else:
                self._pending = self._pending[take:]

        return noutput


class DCSGenerator(gr.sync_block):
    """Generate a repeating pseudo-DCS squelch waveform."""

    _BIT_RATE = 134.4
    _MARK_FREQ = 134.4
    _SPACE_FREQ = 114.3

    # Systematic Golay(23, 12) parity rows used by the CDCSS specification.
    _GOLAY_PARITY_ROWS: Sequence[int] = (
        0b11110000101,
        0b01111000011,
        0b00111100011,
        0b10011110001,
        0b11001111000,
        0b11100111100,
        0b01110011110,
        0b00111001111,
        0b10001100111,
        0b11000110011,
        0b11100011001,
        0b11110001100,
    )

    def __init__(
        self,
        code: str,
        sample_rate: int,
        amplitude: float = 0.2,
    ):
        raw_code = (code or "").strip()
        if not raw_code:
            raise ValueError("A DCS code string is required when enabling DCS")
        if sample_rate <= 0:
            raise ValueError("Sample rate must be positive for DCS generation")

        parsed_code, invert = self._parse_code(raw_code)
        pattern = self._build_pattern(parsed_code, invert)

        gr.sync_block.__init__(
            self,
            name="DCSGenerator",
            in_sig=None,
            out_sig=[np.float32],
        )

        self._pattern = pattern
        self._sample_rate = float(sample_rate)
        self._amplitude = float(amplitude)
        self._samples_per_bit = self._sample_rate / self._BIT_RATE
        self._bit_index = 0
        self._bit_sample_acc = 0.0
        self._phase = 0.0

    @staticmethod
    def _parse_code(code: str) -> tuple[str, bool]:
        """Normalise a user provided DCS code string.

        Accepts optional leading "D" and trailing "N"/"I" suffixes that appear
        in many radio programming guides. Returns the zero-padded octal code and a
        boolean indicating whether the *inverted* form was requested.
        """

        text = code.strip().upper()
        if text.startswith("D"):
            text = text[1:]
        invert = False
        if text.endswith("N") or text.endswith("I"):
            invert = text.endswith("I")
            text = text[:-1]
        if not text:
            raise ValueError("A DCS code string must include octal digits")
        if any(ch not in "01234567" for ch in text):
            raise ValueError(f"Invalid DCS code '{code}'; expected octal digits")
        if len(text) > 3:
            raise ValueError(
                f"Invalid DCS code '{code}'; expected at most three octal digits"
            )
        return text.zfill(3), invert

    @classmethod
    def _build_pattern(cls, code: str, invert: bool) -> List[int]:
        """Construct the 23-bit repeating CDCSS pattern for ``code``."""

        try:
            value = int(code, 8)
        except ValueError as exc:  # pragma: no cover - validated earlier
            raise ValueError(f"Invalid DCS code '{code}'; expected octal digits") from exc

        # Data bits A–L (MSB first within each digit) followed by Golay parity.
        # Expand the user facing octal string so the first digit (hundreds
        # place) maps to bits A–C, the second to D–F, and the last to G–I.  The
        # CDCSS standard enumerates bits within each digit from MSB→LSB.
        digits = [int(ch, 8) for ch in code]
        data_bits: List[int] = []
        for digit in digits:
            data_bits.extend([(digit >> bit) & 0x1 for bit in (2, 1, 0)])

        if len(data_bits) != 9:
            raise AssertionError("Unexpected DCS digit expansion")

        a, b, c, d, e, f, g, h, i = data_bits
        data_bits.extend(
            [
                (a ^ d ^ e ^ g) & 0x1,
                (b ^ e ^ f ^ h) & 0x1,
                (c ^ f ^ g ^ i) & 0x1,
            ]
        )

        if len(data_bits) != 12:
            raise AssertionError("Unexpected DCS data bit count")

        parity = 0
        for bit, row in zip(data_bits, cls._GOLAY_PARITY_ROWS):
            if bit:
                parity ^= row

        pattern = list(data_bits)
        pattern.extend((parity >> idx) & 0x1 for idx in range(11))

        if invert:
            pattern = [1 - b for b in pattern]

        return pattern

    def work(self, input_items, output_items):  # pragma: no cover - realtime stream
        out = output_items[0]
        for idx in range(len(out)):
            bit = self._pattern[self._bit_index]
            freq = self._MARK_FREQ if bit else self._SPACE_FREQ
            phase_step = 2.0 * math.pi * freq / self._sample_rate
            out[idx] = self._amplitude * math.sin(self._phase)
            self._phase += phase_step
            if self._phase >= 2.0 * math.pi:
                self._phase -= 2.0 * math.pi
            self._bit_sample_acc += 1.0
            if self._bit_sample_acc >= self._samples_per_bit:
                self._bit_sample_acc -= self._samples_per_bit
                self._bit_index = (self._bit_index + 1) % len(self._pattern)
        return len(out)


class NBFMChannel(gr.hier_block2):
    """One audio -> NBFM mod -> freq-shifted complex stream at TX sample rate."""

    def __init__(
        self,
        wav_paths: Sequence[Path],
        deviation: float = 3e3,
        mod_sr: float = 250e3,
        tx_sr: float = 8e6,
        freq_offset: float = 0.0,
        audio_gain: float = 1.0,
        loop_queue: bool = True,
        expected_audio_sr: Optional[float] = None,
        ctcss_hz: Optional[float] = None,
        dcs_code: Optional[str] = None,
    ):
        gr.hier_block2.__init__(
            self, "NBFMChannel",
            gr.io_signature(0, 0, 0),
            gr.io_signature(1, 1, gr.sizeof_gr_complex)
        )

        # --- Blocks ---
        # Source (float, 48k mono). Audio source outputs floats in [-1,1].
        target_audio_sr = int(expected_audio_sr) if expected_audio_sr is not None else None
        self.src = QueuedAudioSource(
            wav_paths, repeat=loop_queue, target_sample_rate=target_audio_sr
        )
        if self.src.sample_rate is None:
            raise ValueError("Failed to determine audio sample rate from the playlist")
        audio_sr = int(self.src.sample_rate)
        # Gain on audio (if you need per-channel loudness trim)
        self.program_gain = blocks.multiply_const_ff(audio_gain)

        # (Optional) simple audio band-limit (speech). Keep ~300–3000 Hz with a
        # gentle filter to reduce wideband noise while still passing the
        # sub-audible tone generators without noticeable attenuation.
        self.a_lpf = filter.fir_filter_fff(
            1, firdes.low_pass(1.0, audio_sr, 3400, 800, window.WIN_HAMMING)
        )

        self.connect(self.src, self.program_gain, self.a_lpf)

        mix_sources: List[gr.basic_block] = [self.a_lpf]
        self._mix_adders: List[blocks.add_ff] = []

        self.ctcss_src = None
        if ctcss_hz is not None:
            if ctcss_hz <= 0:
                raise ValueError("CTCSS frequency must be positive when enabled")
            self.ctcss_src = analog.sig_source_f(
                audio_sr,
                analog.GR_SIN_WAVE,
                float(ctcss_hz),
                0.25,
                0.0,
            )
            mix_sources.append(self.ctcss_src)

        self.dcs_src = None
        if dcs_code is not None and str(dcs_code).strip():
            self.dcs_src = DCSGenerator(str(dcs_code), audio_sr, amplitude=0.25)
            mix_sources.append(self.dcs_src)

        if len(mix_sources) == 1:
            mixed_audio = mix_sources[0]
        else:
            current = mix_sources[0]
            for tone_src in mix_sources[1:]:
                adder = blocks.add_ff()
                self._mix_adders.append(adder)
                self.connect(current, (adder, 0))
                self.connect(tone_src, (adder, 1))
                current = adder
            mixed_audio = current

        # Frequency modulator sensitivity:
        # sensitivity [rad/sample] = 2*pi*deviation / mod_sr
        sensitivity = 2.0*math.pi*deviation/float(mod_sr)

        # Resample audio to mod rate if needed
        # Here we go audio_sr -> mod_sr using a rational resampler
        # Keep integer-friendly ratios for stability:
        # For audio_sr=48k and mod_sr=250k: interp=125, decim=24  ( ~5.2083 )
        ratio = Fraction(mod_sr / audio_sr).limit_denominator(1_000_000)
        interp = ratio.numerator
        decim = ratio.denominator
        actual_mod_sr = audio_sr * interp / decim
        if abs(actual_mod_sr - mod_sr) / mod_sr > 1e-6:
            raise ValueError(
                "Unable to derive an accurate rational ratio from audio sample rate "
                f"{audio_sr} Hz to modulation rate {mod_sr} Hz"
            )
        self.a_resamp = filter.rational_resampler_fff(interp, decim)

        self.fm = analog.frequency_modulator_fc(sensitivity)

        # (Optional) baseband LPF post-mod (keeps occupied BW tight)
        # For NBFM (±3–5 kHz dev, ~3 kHz audio), Carson ≈ 2*(fd+fa) ~ 12–16 kHz.
        # Keep a little margin; 15–20 kHz cutoff:
        self.bb_lpf = filter.fir_filter_ccf(
            1, firdes.low_pass(1.0, mod_sr, 20000, 5000, window.WIN_HAMMING)
        )

        # Shift to desired offset at mod_sr
        # rotator angle per sample = 2*pi*freq_offset/mod_sr
        self.rot = blocks.rotator_cc(2.0*math.pi*freq_offset/float(mod_sr))

        # Resample complex mod stream up to tx_sr
        # Choose integer-ish ratio; mod_sr=250k -> tx_sr=8e6 => 32x
        tx_ratio = Fraction(tx_sr / mod_sr).limit_denominator(1_000_000)
        rs_interp = tx_ratio.numerator
        rs_decim = tx_ratio.denominator
        actual_tx_sr = mod_sr * rs_interp / rs_decim
        if abs(actual_tx_sr - tx_sr) / tx_sr > 1e-6:
            raise ValueError(
                "Unable to derive an accurate rational ratio from modulation rate "
                f"{mod_sr} Hz to TX rate {tx_sr} Hz"
            )

        self.c_resamp = filter.rational_resampler_ccc(
            interpolation=rs_interp,
            decimation=rs_decim,
            taps=[],
            fractional_bw=0.45
        )

        # --- Connections ---
        self.connect(
            mixed_audio,
            self.a_resamp,
            self.fm,
            self.bb_lpf,
            self.rot,
            self.c_resamp,
            self,
        )

class MultiNBFMTx(gr.top_block):
    def __init__(
        self,
        device: str,
        center_freq: float,
        file_groups: Sequence[Sequence[Path]],
        offsets: Sequence[float],
        tx_sr: float = 8e6,
        tx_gain: float = 0.0,
        deviation: float = 3e3,
        mod_sr: float = 250e3,
        audio_sr: Optional[float] = None,
        master_scale: float = 0.8,
        loop_queue: bool = True,
        channel_gains: Optional[Sequence[float]] = None,
        ctcss_tones: Optional[Sequence[Optional[float]]] = None,
        dcs_codes: Optional[Sequence[Optional[str]]] = None,
    ):
        gr.top_block.__init__(self, "MultiNBFM TX")

        if len(file_groups) != len(offsets):
            raise ValueError("The number of file queues must match the number of offsets")

        if channel_gains is not None and len(channel_gains) != len(file_groups):
            raise ValueError("--channel-gains must include one value per channel")
        gains_list = list(channel_gains) if channel_gains is not None else None

        num_channels = len(file_groups)
        if ctcss_tones is None:
            ctcss_list: List[Optional[float]] = [None] * num_channels
        else:
            tones_seq = list(ctcss_tones)
            if len(tones_seq) == 1 and num_channels > 1:
                tones_seq.extend([None] * (num_channels - 1))
            elif len(tones_seq) != num_channels:
                raise ValueError("--ctcss-tones must include one value per channel")

            ctcss_list = []
            for tone in tones_seq:
                if tone is None:
                    ctcss_list.append(None)
                else:
                    ctcss_list.append(float(tone))

        if any(ctcss is not None for ctcss in ctcss_list[1:]):
            raise ValueError("Only channel 1 may enable CTCSS at this time")

        if dcs_codes is None:
            dcs_list: List[Optional[str]] = [None] * num_channels
        else:
            if len(dcs_codes) != num_channels:
                raise ValueError("--dcs-codes must include one value per channel")
            dcs_list = [str(code).strip() or None if code is not None else None for code in dcs_codes]

        self.tx_sr = tx_sr

        # Per-channel builders
        self.channels: List[gr.hier_block2] = []
        self._adders: List[blocks.add_cc] = []
        for idx, (wavs, off) in enumerate(zip(file_groups, offsets)):
            gain = gains_list[idx] if gains_list is not None else 1.0
            ctcss = ctcss_list[idx]
            dcs = dcs_list[idx]
            if ctcss is not None and dcs is not None:
                raise ValueError(
                    f"Channel {idx + 1} cannot enable both CTCSS and DCS simultaneously"
                )
            ch = NBFMChannel(
                wav_paths=wavs,
                deviation=deviation,
                mod_sr=mod_sr,
                tx_sr=tx_sr,
                freq_offset=off,
                audio_gain=gain,
                loop_queue=loop_queue,
                expected_audio_sr=audio_sr,
                ctcss_hz=ctcss,
                dcs_code=dcs,
            )
            self.channels.append(ch)

        # Sum channels
        if not self.channels:
            raise ValueError("At least one channel must be specified")

        if len(self.channels) == 1:
            self.summer = self.channels[0]
        else:
            last = self.channels[0]
            for ch in self.channels[1:]:
                adder = blocks.add_cc()
                self._adders.append(adder)
                self.connect(last, (adder, 0))
                self.connect(ch, (adder, 1))
                last = adder
            self.summer = last

        # Master scaling so composite never clips SDR
        if gains_list is None:
            total_gain = float(len(self.channels))
        else:
            total_gain = float(sum(abs(g) for g in gains_list))
            if total_gain <= 0:
                total_gain = float(len(self.channels))
        self.scale = blocks.multiply_const_cc(master_scale / max(1.0, total_gain))

        # SDR sink
        self.sink = osmosdr.sink()
        device_lower = device.lower()
        if device_lower == "hackrf":
            # HackRF typical stable rates 8–10 Msps for this use
            self.sink.set_sample_rate(tx_sr)
            self.sink.set_center_freq(center_freq)
            self.sink.set_gain(tx_gain)  # overall TX gain (0..47 for HackRF)
            self.sink.set_if_gain(0)
            self.sink.set_bb_gain(0)
            self.sink.set_antenna("")  # default
            self.sink.set_bandwidth(0)  # 0 = auto
        elif device_lower == "pluto":
            # Pluto through osmosdr; keep rates modest (e.g., 3–4 Msps) over USB2
            self.sink.set_sample_rate(tx_sr)
            self.sink.set_center_freq(center_freq)
            self.sink.set_gain(tx_gain)  # try -40..0 dB range; adjust as needed
            self.sink.set_if_gain(0)
            self.sink.set_bb_gain(0)
            self.sink.set_antenna("A")
            self.sink.set_bandwidth(0)
        elif device_lower in {"plutoplus", "pluto+", "plutoplussdr"}:
            # PlutoPlus SDR exposes a wider instantaneous bandwidth; treat similarly
            # to the Pluto but keep gain handling explicit.
            self.sink.set_sample_rate(tx_sr)
            self.sink.set_center_freq(center_freq)
            self.sink.set_gain(tx_gain)
            self.sink.set_if_gain(0)
            self.sink.set_bb_gain(0)
            self.sink.set_antenna("A")
            self.sink.set_bandwidth(0)
        else:
            raise RuntimeError("Unknown device: choose 'hackrf', 'pluto', or 'plutoplus'")

        # Connect graph
        self.connect(self.summer, self.scale, self.sink)

def _parse_file_groups(file_args: Sequence[str]) -> List[List[Path]]:
    groups: List[List[Path]] = []
    for entry in file_args:
        paths = [Path(part).expanduser() for part in entry.split(",") if part.strip()]
        if not paths:
            raise ValueError("Each channel must include at least one file path")
        groups.append(paths)
    return groups


def parse_args():
    p = argparse.ArgumentParser(
        description="Multi-channel NBFM transmitter for HackRF, Pluto, and PlutoPlus"
    )
    p.add_argument(
        "--device",
        type=str,
        default="hackrf",
        choices=["hackrf", "pluto", "plutoplus", "pluto+", "plutoplussdr"],
        help="SDR to use via osmosdr",
    )
    p.add_argument("--fc", type=float, required=True, help="Center frequency (Hz)")
    p.add_argument("--tx-sr", type=float, default=8e6, help="TX sample rate (Hz)")
    p.add_argument(
        "--tx-gain",
        type=float,
        default=0.0,
        help="TX gain (HackRF 0..47 dB, Pluto/PlutoPlus typically negative dB)",
    )
    p.add_argument("--deviation", type=float, default=3e3, help="FM deviation per channel (Hz)")
    p.add_argument("--mod-sr", type=float, default=250e3, help="Per-channel FM mod sample rate (complex)")
    p.add_argument(
        "--audio-sr",
        type=float,
        default=None,
        help="Expected audio sample rate (Hz); defaults to the WAV metadata",
    )
    p.add_argument(
        "--files",
        nargs="+",
        required=True,
        help=(
            "Channel file queues. Provide a space-separated entry per channel; "
            "use commas within an entry to queue multiple files (WAV or MP3) for that channel"
        ),
    )
    p.add_argument(
        "--channel-gains",
        nargs="+",
        type=float,
        help="Optional per-channel audio gains (linear scale)",
    )
    p.add_argument(
        "--ctcss-tones",
        nargs="+",
        help=(
            "Optional per-channel CTCSS tone frequencies in Hz; use 'none' to disable "
            "for a specific channel"
        ),
    )
    p.add_argument(
        "--dcs-codes",
        nargs="+",
        help=(
            "Optional per-channel DCS codes (3-digit octal); use 'none' to disable "
            "for a specific channel"
        ),
    )
    p.add_argument(
        "--offsets",
        nargs="+",
        type=float,
        help="Baseband offsets (Hz) relative to the center frequency",
    )
    p.add_argument(
        "--freqs",
        nargs="+",
        type=float,
        help="Absolute transmit frequencies for each channel (Hz)",
    )
    p.add_argument(
        "--master-scale",
        type=float,
        default=0.8,
        help="Composite amplitude scale (safety)",
    )
    p.add_argument(
        "--loop-queue",
        dest="loop_queue",
        action="store_true",
        default=True,
        help="Continuously loop the queued files (default)",
    )
    p.add_argument(
        "--no-loop-queue",
        dest="loop_queue",
        action="store_false",
        help="Play the queued files once and then output silence",
    )

    args = p.parse_args()

    try:
        args.file_groups = _parse_file_groups(args.files)
    except ValueError as exc:
        p.error(str(exc))

    num_channels = len(args.file_groups)

    if args.offsets is not None and args.freqs is not None:
        p.error("Specify either --offsets or --freqs, not both")
    if args.offsets is None and args.freqs is None:
        p.error("Either --offsets or --freqs must be provided")

    if args.offsets is not None and len(args.offsets) != num_channels:
        p.error("--offsets count must match the number of channels")
    if args.freqs is not None and len(args.freqs) != num_channels:
        p.error("--freqs count must match the number of channels")

    if args.freqs is not None:
        args.offsets = [freq - args.fc for freq in args.freqs]
    # Ensure offsets is a list of floats for downstream usage
    args.offsets = list(args.offsets)

    if args.channel_gains is not None:
        if len(args.channel_gains) != num_channels:
            p.error("--channel-gains must include one value per channel")
        args.channel_gains = list(args.channel_gains)

    if args.ctcss_tones is not None:
        parsed_ctcss: List[Optional[float]] = []
        for entry in args.ctcss_tones:
            text = str(entry).strip().lower()
            if text in {"", "none", "off", "disable"}:
                parsed_ctcss.append(None)
                continue
            try:
                freq = float(text)
            except ValueError as exc:
                p.error(f"Invalid CTCSS tone value '{entry}': {exc}")
            if freq <= 0:
                p.error("CTCSS tones must be positive frequencies in Hz")
            parsed_ctcss.append(freq)

        if len(parsed_ctcss) == 1 and num_channels > 1:
            parsed_ctcss.extend([None] * (num_channels - 1))
        elif len(parsed_ctcss) != num_channels:
            p.error("--ctcss-tones must include one value per channel or a single value for channel 1")

        args.ctcss_tones = parsed_ctcss

    if args.dcs_codes is not None:
        if len(args.dcs_codes) != num_channels:
            p.error("--dcs-codes must include one value per channel")
        parsed_dcs: List[Optional[str]] = []
        for entry in args.dcs_codes:
            text = str(entry).strip()
            if not text or text.lower() in {"none", "off", "disable"}:
                parsed_dcs.append(None)
            else:
                parsed_dcs.append(text)
        args.dcs_codes = parsed_dcs

    if args.ctcss_tones is not None and args.dcs_codes is not None:
        for idx, (ctcss, dcs) in enumerate(zip(args.ctcss_tones, args.dcs_codes), start=1):
            if ctcss is not None and dcs is not None:
                p.error(f"Channel {idx} cannot enable both CTCSS and DCS simultaneously")

    return args

if __name__ == "__main__":
    args = parse_args()
    tb = MultiNBFMTx(
        device=args.device,
        center_freq=args.fc,
        file_groups=args.file_groups,
        offsets=args.offsets,
        tx_sr=args.tx_sr,
        tx_gain=args.tx_gain,
        deviation=args.deviation,
        mod_sr=args.mod_sr,
        audio_sr=args.audio_sr,
        master_scale=args.master_scale,
        loop_queue=args.loop_queue,
        channel_gains=args.channel_gains,
        ctcss_tones=args.ctcss_tones,
        dcs_codes=args.dcs_codes,
    )
    try:
        tb.start()
        print("Transmitting... Press Ctrl-C to stop.")
        while True:
            time.sleep(10)
    except KeyboardInterrupt:
        tb.stop()
        tb.wait()
