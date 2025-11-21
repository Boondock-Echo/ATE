import json
import csv
from pathlib import Path

from hackrf_export import HackRFExportChannel, export_hackrf_package


def _touch_file(path: Path, content: bytes = b"test") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_export_copies_audio_once_and_builds_manifests(tmp_path: Path):
    sources = tmp_path / "sources"
    dest = tmp_path / "export"

    first = _touch_file(sources / "voice1.wav")
    duplicate_name = _touch_file(sources / "alt" / "voice1.wav", b"different")
    second = _touch_file(sources / "voice2.wav")

    channels = [
        HackRFExportChannel(
            index=1,
            frequency_hz=462_562_500.0,
            gain=1.0,
            playlist=[first, second],
            ctcss_hz=67.0,
        ),
        HackRFExportChannel(
            index=2,
            frequency_hz=462_612_500.0,
            gain=0.8,
            playlist=[duplicate_name, first],
            dcs_code="023N",
        ),
    ]

    manifest_path = export_hackrf_package(
        dest,
        channels,
        center_frequency_hz=462_587_500.0,
        tx_sample_rate=8_000_000,
        mod_sample_rate=250_000,
        deviation_hz=3_000,
        master_scale=0.6,
        loop_queue=True,
        ctcss_level=0.2,
        ctcss_deviation=None,
        gate_open_threshold=0.015,
        gate_close_threshold=0.014,
        gate_attack_ms=4.0,
        gate_release_ms=200.0,
    )

    audio_dir = dest / "audio"
    assert audio_dir.is_dir()
    copied_audio = sorted(p.name for p in audio_dir.iterdir())
    # The duplicate filename should be disambiguated while reusing identical paths once.
    assert copied_audio[0].startswith("voice1")
    assert copied_audio[1].startswith("voice1")
    assert "voice2.wav" in copied_audio

    manifest = json.loads(manifest_path.read_text())
    assert manifest["center_frequency_hz"] == 462_587_500.0
    assert manifest["loop_queue"] is True
    assert len(manifest["channels"]) == 2
    assert manifest["channels"][0]["files"][0].startswith("audio/")

    csv_path = dest / "hackrf_playlist.csv"
    rows = list(csv.DictReader(csv_path.open()))
    assert len(rows) == 2
    assert "voice2.wav" in rows[0]["files"]
    assert rows[1]["dcs_code"] == "023N"
