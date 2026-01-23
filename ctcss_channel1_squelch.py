#!/usr/bin/env python3
"""Transmit a single-channel CTCSS tone to verify squelch operation."""

import argparse
import os
import tempfile
import time
import wave
from pathlib import Path

import numpy as np

from multich_nbfm_tx import MultiNBFMTx


def _write_silence_wav(path: Path, sample_rate: int, duration: float) -> None:
    total_frames = max(1, int(sample_rate * duration))
    silence = np.zeros(total_frames, dtype=np.int16)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(silence.tobytes())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Broadcast a continuous CTCSS tone on channel 1 using the existing "
            "multichannel transmitter pipeline. This is intended to validate "
            "that a receiver opens squelch for the provided tone."
        )
    )
    parser.add_argument("--device", choices=["hackrf", "pluto", "plutoplus", "pluto+", "plutoplussdr"], default="hackrf")
    parser.add_argument(
        "--device-args",
        type=str,
        default=None,
        help="Raw osmosdr device arguments string (e.g., 'pluto=ip:192.168.2.1')",
    )
    parser.add_argument(
        "--pluto-ip",
        type=str,
        default=None,
        help="Pluto/PlutoPlus SDR IP address (builds device args as 'pluto=ip:<addr>')",
    )
    parser.add_argument("--fc", type=float, required=True, help="Center frequency (Hz)")
    parser.add_argument("--tx-sr", type=float, default=8e6, help="Transmit sample rate (Hz)")
    parser.add_argument("--tx-gain", type=float, default=0.0, help="Transmitter gain setting")
    parser.add_argument("--deviation", type=float, default=3e3, help="FM deviation (Hz)")
    parser.add_argument("--mod-sr", type=float, default=250e3, help="Modulation sample rate (Hz)")
    parser.add_argument("--duration", type=float, default=10.0, help="Seconds to transmit; <=0 keeps transmitting")
    parser.add_argument(
        "--ctcss-tone",
        type=float,
        default=67.0,
        help="CTCSS tone frequency in Hz (default 67.0 Hz for FRS/GMRS channel 1)",
    )
    parser.add_argument(
        "--ctcss-level",
        type=float,
        default=0.35,
        help="Amplitude of the generated CTCSS tone (controls frequency deviation)",
    )
    parser.add_argument(
        "--ctcss-deviation",
        type=float,
        default=None,
        help=(
            "Desired CTCSS deviation in Hz. When provided, overrides --ctcss-level "
            "by converting the requested deviation into the appropriate amplitude."
        ),
    )
    parser.add_argument("--master-scale", type=float, default=0.8, help="Master amplitude scaling applied to the composite signal")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.ctcss_level <= 0:
        raise SystemExit("--ctcss-level must be positive")
    if args.ctcss_deviation is not None and args.ctcss_deviation <= 0:
        raise SystemExit("--ctcss-deviation must be positive")
    if args.device_args and args.pluto_ip:
        raise SystemExit("--device-args and --pluto-ip cannot be used together")
    if args.pluto_ip:
        if args.device.lower() not in {"pluto", "plutoplus", "pluto+", "plutoplussdr"}:
            raise SystemExit("--pluto-ip is only valid with --device pluto or plutoplus")
        args.device_args = f"pluto=ip:{args.pluto_ip}"

    silence_sr = 48_000

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        temp_path = Path(tmp.name)

    try:
        _write_silence_wav(temp_path, silence_sr, max(args.duration, 1.0))

        tx = MultiNBFMTx(
            device=args.device,
            center_freq=args.fc,
            file_groups=[[temp_path]],
            offsets=[0.0],
            device_args=args.device_args,
            tx_sr=args.tx_sr,
            tx_gain=args.tx_gain,
            deviation=args.deviation,
            mod_sr=args.mod_sr,
            master_scale=args.master_scale,
            loop_queue=True,
            channel_gains=[0.0],
            ctcss_tones=[args.ctcss_tone],
            ctcss_level=args.ctcss_level,
            ctcss_deviation=args.ctcss_deviation,
        )

        tx.print_configuration_summary()

        tx.start()
        print(
            "Transmitting continuous CTCSS tone on channel 1. Press Ctrl-C to stop, "
            "or wait for the requested duration."
        )

        start = time.time()
        try:
            while True:
                time.sleep(0.5)
                if args.duration > 0 and (time.time() - start) >= args.duration:
                    break
        except KeyboardInterrupt:
            pass
        finally:
            tx.stop()
            tx.wait()
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass


if __name__ == "__main__":
    main()
