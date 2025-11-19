import csv
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

if not hasattr(np, "isscalar"):
    np.isscalar = lambda obj: isinstance(obj, (int, float, bool))

from audio_activity_report import analyze_audio_file, discover_audio_files


def _write_wav(path: Path, sample_rate: int, samples: np.ndarray) -> None:
    data = np.clip(samples, -1.0, 1.0)
    pcm = (data * 32767).astype(np.int16).tobytes()
    with wave.open(str(path), "wb") as wav_writer:
        wav_writer.setnchannels(1)
        wav_writer.setsampwidth(2)
        wav_writer.setframerate(sample_rate)
        wav_writer.writeframes(pcm)


def test_analyze_audio_file_reports_duty_cycle(tmp_path):
    sr = 8000
    silence = np.zeros(sr // 2, dtype=np.float32)
    tone = np.full(sr // 2, 0.5, dtype=np.float32)
    samples = np.concatenate([silence, tone])
    wav_path = tmp_path / "half_active.wav"
    _write_wav(wav_path, sr, samples)

    summary = analyze_audio_file(wav_path, threshold=0.1, chunk_ms=10.0)

    assert summary.sample_rate == sr
    assert abs(summary.duration_seconds - 1.0) <= 1e-3
    assert abs(summary.duty_cycle_percent - 50.0) <= 0.1


def test_discover_audio_files_filters_supported_types(tmp_path):
    wav_path = tmp_path / "a.wav"
    wav_path.write_bytes(b"RIFF")
    mp3_path = tmp_path / "b.mp3"
    mp3_path.write_bytes(b"ID3")
    (tmp_path / "ignore.txt").write_text("noop")
    nested = tmp_path / "nested"
    nested.mkdir()
    nested_wav = nested / "c.wav"
    nested_wav.write_bytes(b"RIFF")

    discovered = discover_audio_files([tmp_path], recursive=True)

    assert set(discovered) == {wav_path.resolve(), mp3_path.resolve(), nested_wav.resolve()}


def test_cli_defaults_write_csv_to_default_path(tmp_path):
    script = Path(__file__).resolve().parents[1] / "audio_activity_report.py"
    wav_path = tmp_path / "clip.wav"
    sr = 8000
    samples = np.zeros(sr, dtype=np.float32)
    _write_wav(wav_path, sr, samples)

    result = subprocess.run(
        [sys.executable, str(script), str(wav_path)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout == ""
    output_path = tmp_path / "audio_duty_cycle.csv"
    assert output_path.exists()

    with output_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)

    assert rows and rows[0]["path"] == str(wav_path.resolve())
