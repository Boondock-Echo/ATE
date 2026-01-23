#!/usr/bin/env python3
"""Transmit a single-channel CTCSS tone to verify squelch operation."""

import argparse
import getpass
import os
import shutil
import tempfile
import time
import wave
from pathlib import Path
from typing import List, Optional

import numpy as np

from multich_nbfm_tx import MultiNBFMTx
from path_utils import resolve_config_file, resolve_data_file


APP_NAME = "ate"


def _format_id(value: Optional[int]) -> str:
    return "unknown" if value is None else str(value)


def _get_user_identity() -> str:
    uid = _format_id(getattr(os, "getuid", lambda: None)())
    gid = _format_id(getattr(os, "getgid", lambda: None)())
    euid = _format_id(getattr(os, "geteuid", lambda: None)())
    egid = _format_id(getattr(os, "getegid", lambda: None)())
    username = getpass.getuser()
    return f"uid={uid} gid={gid} euid={euid} egid={egid} user={username}"


def _log_startup_environment(args: argparse.Namespace) -> None:
    config_path = resolve_config_file(APP_NAME, "transmitter_settings.json")
    data_path = resolve_data_file(APP_NAME, "channel_presets.csv")
    print("Startup environment:")
    print(f"  Identity: {_get_user_identity()}")
    print(f"  CWD: {Path.cwd()}")
    print(f"  PATH: {os.environ.get('PATH', '')}")
    print(f"  Config path: {config_path}")
    print(f"  Data path: {data_path}")
    print("  CLI args:")
    print(f"    device={args.device}")
    print(f"    fc={args.fc}")
    print(f"    tx_sr={args.tx_sr}")
    print(f"    tx_gain={args.tx_gain}")
    print(f"    deviation={args.deviation}")
    print(f"    mod_sr={args.mod_sr}")
    print(f"    duration={args.duration}")
    print(f"    ctcss_tone={args.ctcss_tone}")
    print(f"    ctcss_level={args.ctcss_level}")
    print(f"    ctcss_deviation={args.ctcss_deviation}")
    print(f"    master_scale={args.master_scale}")


def _verify_dependencies(device: str) -> None:
    device_lower = device.lower()
    required_execs: List[str] = []
    if device_lower == "hackrf":
        required_execs.append("hackrf_transfer")
    elif device_lower in {"pluto", "plutoplus", "pluto+", "plutoplussdr"}:
        required_execs.append("iio_info")

    missing = [
        executable
        for executable in required_execs
        if shutil.which(executable) is None
    ]
    if missing:
        missing_list = ", ".join(missing)
        raise SystemExit(
            f"Missing required executable(s) for device '{device}': {missing_list}."
        )


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
    _verify_dependencies(args.device)
    _log_startup_environment(args)

    if args.ctcss_level <= 0:
        raise SystemExit("--ctcss-level must be positive")
    if args.ctcss_deviation is not None and args.ctcss_deviation <= 0:
        raise SystemExit("--ctcss-deviation must be positive")

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
