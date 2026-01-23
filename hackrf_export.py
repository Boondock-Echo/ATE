"""Utilities for exporting playlists to HackRF/PortaPack SD cards."""

from __future__ import annotations

import csv
import io
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from path_utils import atomic_write

@dataclass
class HackRFExportChannel:
    """Represents one channel worth of audio and metadata for export."""

    index: int
    frequency_hz: float
    gain: float
    playlist: Sequence[Path]
    ctcss_hz: Optional[float] = None
    dcs_code: Optional[str] = None


def _copy_once(src: Path, dest_dir: Path, name_map: Dict[Path, str]) -> Path:
    """Copy *src* into *dest_dir* only once and return a relative path.

    The returned path is relative to the final export root ("audio/<name>")
    so it can be stored directly in playlist manifests.
    """

    resolved = src.resolve()
    if resolved in name_map:
        return Path("audio") / name_map[resolved]

    stem = src.stem
    suffix = src.suffix
    candidate = src.name
    counter = 1
    while (dest_dir / candidate).exists():
        counter += 1
        candidate = f"{stem}_{counter}{suffix}"

    shutil.copy2(src, dest_dir / candidate)
    name_map[resolved] = candidate
    return Path("audio") / candidate


def export_hackrf_package(
    destination: Path,
    channels: Sequence[HackRFExportChannel],
    *,
    center_frequency_hz: float,
    tx_sample_rate: float,
    mod_sample_rate: float,
    deviation_hz: float,
    master_scale: float,
    loop_queue: bool,
    ctcss_level: Optional[float] = None,
    ctcss_deviation: Optional[float] = None,
    gate_open_threshold: Optional[float] = None,
    gate_close_threshold: Optional[float] = None,
    gate_attack_ms: Optional[float] = None,
    gate_release_ms: Optional[float] = None,
) -> Path:
    """Export the current session to a HackRF/PortaPack-friendly folder.

    Files are copied into ``destination`` alongside a ``hackrf_playlist.json``
    manifest and a human-readable ``hackrf_playlist.csv``. Relative paths are
    used so the folder can be dropped directly onto an SD card.
    """

    if not channels:
        raise ValueError("At least one channel is required for export")

    destination.mkdir(parents=True, exist_ok=True)
    audio_dir = destination / "audio"
    audio_dir.mkdir(exist_ok=True)

    name_map: Dict[Path, str] = {}
    manifest_channels: List[Dict[str, object]] = []

    for channel in channels:
        if not channel.playlist:
            raise ValueError(f"Channel {channel.index} has no audio files to export")
        file_entries: List[str] = []
        for src in channel.playlist:
            src_path = Path(src)
            if not src_path.exists():
                raise FileNotFoundError(
                    f"Audio file for channel {channel.index} is missing: {src_path}"
                )
            if not src_path.is_file():
                raise FileNotFoundError(
                    f"Audio path for channel {channel.index} is not a file: {src_path}"
                )
            rel_path = _copy_once(src_path, audio_dir, name_map)
            file_entries.append(str(rel_path))

        manifest_channels.append(
            {
                "index": channel.index,
                "frequency_hz": channel.frequency_hz,
                "gain": channel.gain,
                "ctcss_hz": channel.ctcss_hz,
                "dcs_code": channel.dcs_code,
                "files": file_entries,
            }
        )

    manifest = {
        "center_frequency_hz": center_frequency_hz,
        "tx_sample_rate": tx_sample_rate,
        "mod_sample_rate": mod_sample_rate,
        "deviation_hz": deviation_hz,
        "master_scale": master_scale,
        "loop_queue": bool(loop_queue),
        "ctcss_level": ctcss_level,
        "ctcss_deviation": ctcss_deviation,
        "gate_open_threshold": gate_open_threshold,
        "gate_close_threshold": gate_close_threshold,
        "gate_attack_ms": gate_attack_ms,
        "gate_release_ms": gate_release_ms,
        "channels": manifest_channels,
    }

    json_path = destination / "hackrf_playlist.json"
    atomic_write(json_path, json.dumps(manifest, indent=2))

    csv_path = destination / "hackrf_playlist.csv"
    buffer = io.StringIO(newline="")
    fieldnames = [
        "index",
        "frequency_hz",
        "gain",
        "ctcss_hz",
        "dcs_code",
        "files",
    ]
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    for entry in manifest_channels:
        writer.writerow({
            **entry,
            "files": ";".join(entry["files"]),
        })
    atomic_write(csv_path, buffer.getvalue())

    return json_path


__all__ = [
    "HackRFExportChannel",
    "export_hackrf_package",
]
