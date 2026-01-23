#!/usr/bin/env python3
"""Summarize the audio duty cycle for WAV/MP3 files."""

from __future__ import annotations

import argparse
import csv
import io
import sys
import math
from array import array
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, List, Sequence, Tuple

from path_utils import atomic_write, ensure_directory, resolve_log_file

try:  # pragma: no cover - optional dependency for MP3 files
    import audioread
except ImportError:  # pragma: no cover - handled lazily at runtime
    audioread = None  # type: ignore[assignment]


SUPPORTED_SUFFIXES = {".wav", ".mp3"}
APP_NAME = "ate"
DEFAULT_OUTPUT_NAME = "audio_duty_cycle.csv"


@dataclass
class AudioActivitySummary:
    path: Path
    sample_rate: int
    duration_seconds: float
    active_seconds: float
    duty_cycle_percent: float


def discover_audio_files(paths: Sequence[Path], recursive: bool = False) -> List[Path]:
    """Return supported audio files from the provided paths."""

    discovered: List[Path] = []
    for root in paths:
        root = root.expanduser().resolve()
        if root.is_file() and root.suffix.lower() in SUPPORTED_SUFFIXES:
            discovered.append(root)
            continue
        if root.is_dir():
            iterator: Iterable[Path]
            if recursive:
                iterator = root.rglob("*")
            else:
                iterator = root.glob("*")
            for candidate in iterator:
                if candidate.is_file() and candidate.suffix.lower() in SUPPORTED_SUFFIXES:
                    discovered.append(candidate.resolve())
    return sorted(discovered)


def _normalized_rms(chunk: bytes, sample_width: int) -> float:
    if not chunk:
        return 0.0

    usable = len(chunk) - (len(chunk) % sample_width)
    if usable <= 0:
        return 0.0

    if sample_width != 2:
        raise ValueError("Only 16-bit PCM is supported")

    samples = array("h")
    samples.frombytes(chunk[:usable])
    if sys.byteorder == "big":
        samples.byteswap()

    if not samples:
        return 0.0

    peak = float(1 << (sample_width * 8 - 1))
    sum_squares = 0.0
    for sample in samples:
        sum_squares += float(sample * sample)
    rms = math.sqrt(sum_squares / len(samples)) if sum_squares else 0.0
    return rms / peak


def _measure_activity(
    chunks: Iterable[bytes],
    sample_width: int,
    sample_rate: int,
    threshold: float,
    chunk_samples: int,
) -> Tuple[float, float, float]:
    total_samples = 0
    active_samples = 0
    chunk_bytes = max(sample_width, chunk_samples * sample_width)
    pending = bytearray()

    for raw in chunks:
        if not raw:
            continue
        pending.extend(raw)
        while len(pending) >= chunk_bytes:
            block = bytes(pending[:chunk_bytes])
            del pending[:chunk_bytes]
            samples = len(block) // sample_width
            total_samples += samples
            if _normalized_rms(block, sample_width) >= threshold:
                active_samples += samples

    if pending:
        block = bytes(pending)
        samples = len(block) // sample_width
        total_samples += samples
        if _normalized_rms(block, sample_width) >= threshold:
            active_samples += samples

    if sample_rate <= 0:
        raise ValueError("Sample rate must be positive")

    duration = total_samples / sample_rate if total_samples else 0.0
    active = active_samples / sample_rate if active_samples else 0.0
    duty = (active_samples / total_samples * 100.0) if total_samples else 0.0

    return duration, active, duty


def analyze_audio_file(
    path: Path, threshold: float = 0.1, chunk_ms: float = 1000.0
) -> AudioActivitySummary:
    path = path.expanduser().resolve()
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError(f"Unsupported audio format for {path}")
    if threshold <= 0:
        raise ValueError("threshold must be positive")
    if chunk_ms <= 0:
        raise ValueError("chunk_ms must be positive")

    sample_width = 2

    if suffix == ".wav":
        import wave

        with wave.open(str(path), "rb") as wav_reader:
            sample_rate = wav_reader.getframerate()
            nchannels = wav_reader.getnchannels()
            sampwidth = wav_reader.getsampwidth()
            if nchannels != 1:
                raise ValueError(f"{path} must be mono but has {nchannels} channels")
            if sampwidth != sample_width:
                raise ValueError(
                    f"{path} must be 16-bit PCM; got sample width {sampwidth}"
                )
            if sample_rate <= 0:
                raise ValueError(f"Invalid sample rate {sample_rate} for {path}")

            chunk_samples = max(1, int(round(sample_rate * chunk_ms / 1000.0)))

            def wav_iter() -> Iterator[bytes]:
                wav_reader.rewind()
                while True:
                    raw = wav_reader.readframes(chunk_samples)
                    if not raw:
                        break
                    yield raw

            duration, active, duty = _measure_activity(
                wav_iter(), sample_width, sample_rate, threshold, chunk_samples
            )
    else:
        if audioread is None:  # pragma: no cover - optional dependency
            raise ImportError(
                "MP3 analysis requires the 'audioread' package. Install it with 'pip install audioread'."
            )
        reader = audioread.audio_open(str(path))
        try:
            sample_rate = reader.samplerate
            if reader.channels != 1:
                raise ValueError(
                    f"{path} must be mono but has {reader.channels} channels"
                )
            if sample_rate <= 0:
                raise ValueError(f"Invalid sample rate {sample_rate} for {path}")

            chunk_samples = max(1, int(round(sample_rate * chunk_ms / 1000.0)))
            chunk_bytes = chunk_samples * sample_width

            def mp3_iter() -> Iterator[bytes]:
                for raw in reader.read_data(chunk_bytes):
                    yield raw

            duration, active, duty = _measure_activity(
                mp3_iter(), sample_width, sample_rate, threshold, chunk_samples
            )
        finally:
            reader.close()

    return AudioActivitySummary(
        path=path,
        sample_rate=sample_rate,
        duration_seconds=duration,
        active_seconds=active,
        duty_cycle_percent=duty,
    )


def generate_report(
    paths: Sequence[Path], threshold: float, chunk_ms: float, recursive: bool
) -> List[AudioActivitySummary]:
    audio_files = discover_audio_files(paths, recursive=recursive)
    return [analyze_audio_file(path, threshold=threshold, chunk_ms=chunk_ms) for path in audio_files]


def _write_csv(rows: Sequence[AudioActivitySummary], output: Path | None) -> None:
    fieldnames = [
        "path",
        "sample_rate_hz",
        "duration_seconds",
        "active_seconds",
        "duty_cycle_percent",
    ]

    if output is None:
        writer = csv.DictWriter(sys.stdout, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "path": str(row.path),
                    "sample_rate_hz": row.sample_rate,
                    "duration_seconds": f"{row.duration_seconds:.3f}",
                    "active_seconds": f"{row.active_seconds:.3f}",
                    "duty_cycle_percent": f"{row.duty_cycle_percent:.2f}",
                }
            )
        return

    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {
                "path": str(row.path),
                "sample_rate_hz": row.sample_rate,
                "duration_seconds": f"{row.duration_seconds:.3f}",
                "active_seconds": f"{row.active_seconds:.3f}",
                "duty_cycle_percent": f"{row.duty_cycle_percent:.2f}",
            }
        )
    atomic_write(output, buffer.getvalue())


def main() -> None:  # pragma: no cover - CLI wrapper
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path, help="Audio files or directories to scan")
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.1,
        help="Normalized RMS threshold (0-1) that counts a chunk as active.",
    )
    parser.add_argument(
        "--chunk-ms",
        type=float,
        default=1000.0,
        help="Chunk duration, in milliseconds, to evaluate for activity.",
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="Search directories recursively for audio files.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional CSV output path. Defaults to a standard data/log directory.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Optional config path (reserved for future settings).",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Override the default data/log directory for CSV output.",
    )
    parser.add_argument(
        "--presets",
        type=Path,
        default=None,
        help="Optional presets path (reserved for future settings).",
    )

    args = parser.parse_args()

    output = args.output
    if output is None:
        output = resolve_log_file(APP_NAME, DEFAULT_OUTPUT_NAME, base_dir=args.data_dir)
        ensure_directory(output.parent)
    rows = generate_report(args.paths, args.threshold, args.chunk_ms, recursive=args.recursive)
    if not rows:
        raise SystemExit("No supported audio files were found.")

    _write_csv(rows, output)


if __name__ == "__main__":  # pragma: no cover
    main()
