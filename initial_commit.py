#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Multi-channel NBFM voice TX for HackRF/Pluto via osmosdr
# GNU Radio 3.9/3.10 compatible.
#
# sox input1.mp3 -r 48000 -c 1 -b 16 ch1.wav gain -n -3
# sox input2.mp3 -r 48000 -c 1 -b 16 ch2.wav gain -n -3
# sox input3.mp3 -r 48000 -c 1 -b 16 ch3.wav gain -n -3
# sox input4.mp3 -r 48000 -c 1 -b 16 ch4.wav gain -n -3
# 
# FRS Frequencies 462.5625 462.5875 462.6125 462.6375 462.6625 462.6875
#
# Usage examples:
#   python3 multich_nbfm_tx.py --device hackrf --fc 462.6e6 \
#       --files ch1.wav ch2.wav ch3.wav ch4.wav \
#       --offsets -375e2 -125e2 125e2 375e2 --deviation 3e3 --tx-sr 8e6 --tx-gain 0
#
#   python3 multich_nbfm_tx.py --device pluto --fc 462.6e6 \
#       --files ch1.wav ch2.wav --offsets -125e2 125e2 --tx-sr 4e6 --tx-gain -20
#
import argparse
import math
import osmosdr
import time
from gnuradio import gr, blocks, analog, filter
from gnuradio.filter import firdes, window

class NBFMChannel(gr.hier_block2):
    """
    One audio -> NBFM mod -> freq-shifted complex stream at TX sample rate.
    """
    def __init__(self, wav_path, audio_sr=48000, deviation=3e3,
                 mod_sr=250e3,  # per-channel complex mod rate
                 tx_sr=8e6,     # SDR sink rate
                 freq_offset=0.0, # Hz, relative to SDR center
                 audio_gain=1.0):
        gr.hier_block2.__init__(
            self, "NBFMChannel",
            gr.io_signature(0, 0, 0),
            gr.io_signature(1, 1, gr.sizeof_gr_complex)
        )

        # --- Blocks ---
        # Source (float, 48k mono). WAV source outputs floats in [-1,1].
        self.src = blocks.wavfile_source(wav_path, True)  # repeat = True
        # Gain on audio (if you need per-channel loudness trim)
        self.a_gain = blocks.multiply_const_ff(audio_gain)

        # (Optional) simple audio band-limit (speech)
        # Keep ~300â€“3000 Hz; gentle filter to reduce wideband noise
        t_aps = 101
        self.a_lpf = filter.fir_filter_fff(
            1, firdes.low_pass(1.0, audio_sr, 3400, 800, window.WIN_HAMMING)
        )

        # Complex baseband LPF (~20 kHz)
        self.bb_lpf = filter.fir_filter_ccf(
            1, firdes.low_pass(1.0, mod_sr, 20000, 5000, window.WIN_HAMMING)
        )

        # Frequency modulator sensitivity:
        # sensitivity [rad/sample] = 2*pi*deviation / mod_sr
        sensitivity = 2.0*math.pi*deviation/float(mod_sr)

        # Resample audio to mod rate if needed
        # Here we go audio_sr -> mod_sr using a rational resampler
        # Keep integer-friendly ratios for stability:
        # For audio_sr=48k and mod_sr=250k: interp=125, decim=24  ( ~5.2083 )
        from math import gcd
        num = int(mod_sr)
        den = audio_sr
        g = gcd(num, den)
        interp = num // g
        decim  = den // g
        self.a_resamp = filter.rational_resampler_fff(interp, decim)

        self.fm = analog.frequency_modulator_fc(sensitivity)

        # (Optional) baseband LPF post-mod (keeps occupied BW tight)
        # For NBFM (Â±3â€“5 kHz dev, ~3 kHz audio), Carson â‰ˆ 2*(fd+fa) ~ 12â€“16 kHz.
        # Keep a little margin; 15â€“20 kHz cutoff:
        self.bb_lpf = filter.fir_filter_ccf(
            1, firdes.low_pass(1.0, mod_sr, 20000, 5000, window.WIN_HAMMING)
        )

        # Shift to desired offset at mod_sr
        # rotator angle per sample = 2*pi*freq_offset/mod_sr
        self.rot = blocks.rotator_cc(2.0*math.pi*freq_offset/float(mod_sr))

        # Resample complex mod stream up to tx_sr
        # Choose integer-ish ratio; mod_sr=250k -> tx_sr=8e6 => 32x
        rs_interp = int(tx_sr // math.gcd(int(tx_sr), int(mod_sr)))
        rs_decim  = int(mod_sr // math.gcd(int(tx_sr), int(mod_sr)))
        # However, to guarantee exact target rate, just set interp/decim explicitly:
        # For default mod_sr=250k and tx_sr=8e6: interp=32, decim=1
        if int(mod_sr) == 250000 and int(tx_sr) == 8000000:
            rs_interp, rs_decim = 32, 1

        self.c_resamp = filter.rational_resampler_ccc(
            interpolation=rs_interp,
            decimation=rs_decim,
            taps=[],
            fractional_bw=0.45
        )

        # --- Connections ---
        self.connect(self.src, self.a_gain, self.a_lpf, self.a_resamp, self.fm,
                     self.bb_lpf, self.rot, self.c_resamp, self)

class MultiNBFMTx(gr.top_block):
    def __init__(self, device, center_freq, files, offsets,
                 tx_sr=8e6, tx_gain=0.0, deviation=3e3,
                 mod_sr=250e3, audio_sr=48000, master_scale=0.8):
        gr.top_block.__init__(self, "MultiNBFM TX")

        assert len(files) == len(offsets), "files and offsets must match in length"
        self.tx_sr = tx_sr

        # Per-channel builders
        self.channels = []
        for i, (wav, off) in enumerate(zip(files, offsets)):
            ch = NBFMChannel(wav_path=wav,
                             audio_sr=audio_sr,
                             deviation=deviation,
                             mod_sr=mod_sr,
                             tx_sr=tx_sr,
                             freq_offset=off,
                             audio_gain=1.0)
            self.channels.append(ch)

        # Sum channels
        self.adder = blocks.add_cc()
        # Chain adders for 3â€“4 inputs
        if len(self.channels) == 1:
            self.summer = self.channels[0]
        elif len(self.channels) == 2:
            self.summer = blocks.add_cc()
            self.connect(self.channels[0], (self.summer, 0))
            self.connect(self.channels[1], (self.summer, 1))
        else:
            # For N>2, daisy-chain adders
            self.summer = None
            last = None
            for idx, ch in enumerate(self.channels):
                if idx == 0:
                    last = ch
                else:
                    adder = blocks.add_cc()
                    self.connect(last, (adder, 0))
                    self.connect(ch,   (adder, 1))
                    last = adder
            self.summer = last

        # Master scaling so composite never clips SDR
        self.scale = blocks.multiply_const_cc(master_scale / max(1, len(self.channels)))

        # SDR sink
        self.sink = osmosdr.sink()
        if device.lower() == "hackrf":
            # HackRF typical stable rates 8â€“10 Msps for this use
            self.sink.set_sample_rate(tx_sr)
            self.sink.set_center_freq(center_freq)
            self.sink.set_gain(tx_gain)      # overall TX gain (0..47 for HackRF)
            self.sink.set_if_gain(0)
            self.sink.set_bb_gain(0)
            self.sink.set_antenna("")        # default
            self.sink.set_bandwidth(0)       # 0 = auto
        elif device.lower() == "pluto":
            # Pluto through osmosdr; keep rates modest (e.g., 3â€“4 Msps) over USB2
            self.sink.set_sample_rate(tx_sr)
            self.sink.set_center_freq(center_freq)
            self.sink.set_gain(tx_gain)      # try -40..0 dB range; adjust as needed
            self.sink.set_if_gain(0)
            self.sink.set_bb_gain(0)
            self.sink.set_antenna("A")       # or default
            self.sink.set_bandwidth(0)
        else:
            raise RuntimeError("Unknown device: choose 'hackrf' or 'pluto'")

        # Connect graph
        self.connect(self.summer, self.scale, self.sink)

def parse_args():
    p = argparse.ArgumentParser(description="Multi-channel NBFM transmitter for HackRF/Pluto")
    p.add_argument("--device", type=str, default="hackrf", choices=["hackrf", "pluto"],
                   help="SDR to use via osmosdr")
    p.add_argument("--fc", type=float, required=True, help="Center frequency (Hz)")
    p.add_argument("--tx-sr", type=float, default=8e6, help="TX sample rate (Hz)")
    p.add_argument("--tx-gain", type=float, default=0.0, help="TX gain (dB-ish; HackRF 0..47; Pluto negative dB)")
    p.add_argument("--deviation", type=float, default=3e3, help="FM deviation per channel (Hz)")
    p.add_argument("--mod-sr", type=float, default=250e3, help="Per-channel FM mod sample rate (complex)")
    p.add_argument("--audio-sr", type=float, default=48000, help="Audio sample rate (Hz)")
    p.add_argument("--files", nargs="+", required=True, help="WAV files (mono, 48k)")
    p.add_argument("--offsets", nargs="+", required=True, type=float, help="Baseband offsets (Hz)")
    p.add_argument("--master-scale", type=float, default=0.8, help="Composite amplitude scale (safety)")
    return p.parse_args()

if __name__ == "__main__":
    args = parse_args()
    tb = MultiNBFMTx(
        device=args.device,
        center_freq=args.fc,
        files=args.files,
        offsets=args.offsets,
        tx_sr=args.tx_sr,
        tx_gain=args.tx_gain,
        deviation=args.deviation,
        mod_sr=args.mod_sr,
        audio_sr=args.audio_sr,
        master_scale=args.master_scale
    )
    try:
        tb.start()
        print("Transmitting... Press Ctrl-C to stop or wait 60 seconds.")
        while True:
            time.sleep(10)
    except KeyboardInterrupt:
        tb.stop()
        tb.wait()
