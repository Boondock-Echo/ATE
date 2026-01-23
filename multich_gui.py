#!/usr/bin/env python3
"""Lightweight GUI wrapper for the multi-channel NBFM transmitter."""

import argparse
import contextlib
import csv
import importlib
import io
import json
import math
import os
import getpass
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Dict, List, Optional, Sequence
import wave

from multich_nbfm_tx import (
    DEFAULT_GATE_ATTACK_MS,
    DEFAULT_GATE_CLOSE_THRESHOLD,
    DEFAULT_GATE_OPEN_THRESHOLD,
    DEFAULT_GATE_RELEASE_MS,
    MultiNBFMTx,
)
from hackrf_export import HackRFExportChannel, export_hackrf_package
from path_utils import (
    atomic_write,
    ensure_directory,
    resolve_config_file,
    resolve_data_file,
)


DEFAULT_TX_SAMPLE_RATE = 8_000_000
DEFAULT_MOD_SAMPLE_RATE = 250_000
DEFAULT_DEVIATION_HZ = 3_000
DEFAULT_MASTER_SCALE = 0.6
DEFAULT_CTCSS_LEVEL = 0.20
DEFAULT_TX_GAIN_OVERRIDE = 10.0


APP_NAME = "ate"
DEFAULT_PRESETS_PATH = Path(__file__).with_name("channel_presets.csv")
TRANSMITTER_SETTINGS_PATH = resolve_config_file(APP_NAME, "transmitter_settings.json")
APP_ICON_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAIAAAACACAYAAADDPmHLAAADUklEQVR4nO2WQVIkMQwE5yv7Rh6w"
    "D9zncGdYH4jgAJjollQlKzNCR7qtqhw3jwcAAAAAAAAAAAAAAMB/Xl/+PO+M+vxwEwQYDgIMBwGG"
    "gwDDQYDhIMBwEGA4CDAcBBhOZwHU7z8CZwHuns1BUnucw40SACF+wDnMLAGQ4RPOAVYIMF4G59Cq"
    "BRgpgnNYKgFGieAcklqAERI4B6Quf4QIzsGoSx8hgXMo6sJHiOAchrroERI4B6EueYQEziGoC1bv"
    "X4JzAOpyHTJIx215dZkuOZThsLi6vNESKJdWF4YED40A6pIcJHj79/f53VzJ9DLVAqjLcZFgnADq"
    "QtwEWFhIULGougxXCUYIoC5BPbt85BJkLqgO32F2+R8rgDp4p9l1IJUgYzl14Kq5WuRRArj8sjoJ"
    "sDhCgOrAq3askEB2C0wQoPJ87W6BqBKqy78iQNU5o2+BK3uWBVIVapQAUXtXCpAqwVQBss/d5jOg"
    "Ks9BgMz923wG1CWqBcjMoMVnQF2igwBZObT4DKhLnCiA1WdAXaKLAFlZIEAjATLysP8/QF0iAnwN"
    "AogEyMjE+jOgLhEBvgYBEAABVAJE54IACIAACIAACIAACIAACIAACLAv9MrfyBdFAG4ABEAABEAA"
    "BEAABEAABECAngJEnxMBEAABEAABWgjgUP4CAQYL8N3fIECyABlntL7+s5buKEBl+QgwXIDdWRCg"
    "WICs89l//zOX7yJAdflW139EAJkhZguQebYW139ECNlBZgmgKN/u+o8IoiLQSAEqztXm+o8IpDrc"
    "OwI4l79AgCQBKs/U6vqPCCf6eVECVMt4p/yFpPyIoDKe2XXa/fojysp67kmz6+BIASKefcLs8peW"
    "v8heUF2Aenb5HC9AxDu6zi4XefmLCgEi3tNtfpPJKAGi3tdldjlYlL+oFiDine7zmwxGCxD1bseJ"
    "6qUMh4XVpY0tf+G2tLpElxzKcF5cXapDBuk4L68uVr1/Cc4BqMs9vvyFcwjqgo8vf+EchLrk48tf"
    "OIehLvro4j9wDkVd+PHlL5yDUZd+dPEfOAdE+QU4h0TxBTiHRfEFOIdG6QU4B0jpBTiHSeEFdA5X"
    "/f4j6CwABIAAw0GA4SDAcBBgOAgwHAQYDgIMBwHO5B1YLeBkPKx7vwAAAABJRU5ErkJggg=="
)

TRANSMITTER_SETTING_FIELDS = [
    dict(
        key="tx_sample_rate",
        attr="tx_sr_var",
        name="TX sample rate",
        label="TX Sample Rate (sps):",
        min=1_000_000,
        max=20_000_000,
        step=100_000,
        help="1–20 Msps keeps HackRF happy.",
    ),
    dict(
        key="mod_sample_rate",
        attr="mod_sr_var",
        name="Mod sample rate",
        label="Mod Sample Rate (sps):",
        min=10_000,
        max=1_000_000,
        step=5_000,
        help="48–250 kS/s per channel works well.",
    ),
    dict(
        key="deviation_hz",
        attr="deviation_var",
        name="FM deviation",
        label="FM Deviation (Hz):",
        min=100,
        max=75_000,
        step=100,
        help="NBFM is usually ±3 kHz.",
    ),
    dict(
        key="master_scale",
        attr="master_scale_var",
        name="Master scale",
        label="Master Scale:",
        min=0.1,
        max=2.0,
        step=0.05,
        help="Scales the summed waveform before transmit.",
    ),
    dict(
        key="ctcss_level",
        attr="ctcss_level_var",
        name="CTCSS level",
        label="CTCSS Level (amplitude):",
        min=0.01,
        max=1.0,
        step=0.01,
        help="0.05–0.3 keeps tones audible without clipping.",
    ),
    dict(
        key="ctcss_deviation",
        attr="ctcss_deviation_var",
        name="CTCSS deviation",
        label="CTCSS Deviation (Hz):",
        min=10,
        max=1_000,
        allow_empty=True,
        help="Optional: overrides amplitude scaling when set.",
        widget="entry",
    ),
    dict(
        key="tx_gain_override",
        attr="tx_gain_var",
        name="TX gain override",
        label="TX Gain Override (dB):",
        min=-50,
        max=70,
        allow_empty=True,
        help="Leave blank to rely on the device default.",
        widget="entry",
    ),
    dict(
        key="gate_open_threshold",
        attr="gate_open_var",
        name="Gate open threshold",
        label="Gate Open Threshold:",
        min=0.0,
        max=0.5,
        step=0.001,
        help="Signal level that starts the tone gate.",
    ),
    dict(
        key="gate_close_threshold",
        attr="gate_close_var",
        name="Gate close threshold",
        label="Gate Close Threshold:",
        min=0.0,
        max=0.5,
        step=0.001,
        help="Must stay below the open threshold to prevent chatter.",
    ),
    dict(
        key="gate_attack_ms",
        attr="gate_attack_var",
        name="Gate attack",
        label="Gate Attack (ms):",
        min=0.0,
        max=1_000.0,
        allow_empty=True,
        help="Blank uses the default 4 ms fade-in.",
        widget="entry",
    ),
    dict(
        key="gate_release_ms",
        attr="gate_release_var",
        name="Gate release",
        label="Gate Release (ms):",
        min=0.0,
        max=5_000.0,
        allow_empty=True,
        help="Blank uses the default 200 ms tail.",
        widget="entry",
    ),
]

DEFAULT_TRANSMITTER_SETTINGS: Dict[str, Optional[float]] = {
    "tx_sample_rate": DEFAULT_TX_SAMPLE_RATE,
    "mod_sample_rate": DEFAULT_MOD_SAMPLE_RATE,
    "deviation_hz": DEFAULT_DEVIATION_HZ,
    "master_scale": DEFAULT_MASTER_SCALE,
    "ctcss_level": DEFAULT_CTCSS_LEVEL,
    "ctcss_deviation": None,
    "tx_gain_override": DEFAULT_TX_GAIN_OVERRIDE,
    "gate_open_threshold": DEFAULT_GATE_OPEN_THRESHOLD,
    "gate_close_threshold": DEFAULT_GATE_CLOSE_THRESHOLD,
    "gate_attack_ms": DEFAULT_GATE_ATTACK_MS,
    "gate_release_ms": DEFAULT_GATE_RELEASE_MS,
}


def _format_id(value: Optional[int]) -> str:
    return "unknown" if value is None else str(value)


def _get_user_identity() -> str:
    uid = _format_id(getattr(os, "getuid", lambda: None)())
    gid = _format_id(getattr(os, "getgid", lambda: None)())
    euid = _format_id(getattr(os, "geteuid", lambda: None)())
    egid = _format_id(getattr(os, "getegid", lambda: None)())
    username = getpass.getuser()
    return f"uid={uid} gid={gid} euid={euid} egid={egid} user={username}"


def _format_setting_value(value: Optional[float]) -> str:
    return "" if value is None else f"{float(value):g}"


def load_transmitter_settings(path: Path = TRANSMITTER_SETTINGS_PATH) -> Dict[str, Optional[float]]:
    """Load saved transmitter defaults from disk."""

    settings = dict(DEFAULT_TRANSMITTER_SETTINGS)
    if not path.exists():
        return settings
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Unable to load transmitter settings from {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Transmitter settings in {path} must be a JSON object")

    allow_empty_keys = {
        field["key"]
        for field in TRANSMITTER_SETTING_FIELDS
        if field.get("allow_empty")
    }
    for key in settings.keys():
        if key not in data:
            continue
        raw_value = data[key]
        if raw_value is None:
            if key in allow_empty_keys:
                settings[key] = None
                continue
            raise ValueError(f"Transmitter setting '{key}' cannot be null")
        try:
            settings[key] = float(raw_value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid value for '{key}' in {path}: {raw_value!r}") from exc
    return settings


def save_transmitter_settings(
    settings: Dict[str, Optional[float]], path: Path = TRANSMITTER_SETTINGS_PATH
) -> None:
    """Persist transmitter defaults to disk."""

    serializable: Dict[str, Optional[float]] = {}
    for key in DEFAULT_TRANSMITTER_SETTINGS.keys():
        serializable[key] = settings.get(key)
    atomic_write(path, json.dumps(serializable, indent=2))


@dataclass(frozen=True)
class ChannelPreset:
    """Represents a selectable preset channel."""

    key: str
    label: str
    frequency_hz: float
    ctcss_hz: Optional[float] = None
    dcs_code: Optional[str] = None


@dataclass
class PlaylistEntry:
    path: Path
    duration: Optional[float] = None
    sample_rate: Optional[int] = None


_MP3_CLASS = None
_MP3_UNAVAILABLE = False


def _get_mp3_loader():
    """Lazily import the MP3 metadata reader from mutagen if available."""

    global _MP3_CLASS, _MP3_UNAVAILABLE
    if _MP3_UNAVAILABLE:
        return None
    if _MP3_CLASS is not None:
        return _MP3_CLASS
    spec = importlib.util.find_spec("mutagen.mp3")
    if spec is None:
        _MP3_UNAVAILABLE = True
        return None
    module = importlib.import_module("mutagen.mp3")
    mp3_class = getattr(module, "MP3", None)
    if mp3_class is None:
        _MP3_UNAVAILABLE = True
        return None
    _MP3_CLASS = mp3_class
    return _MP3_CLASS


def load_channel_presets(
    presets_path: Optional[Path] = None,
    data_dir: Optional[Path] = None,
) -> List[ChannelPreset]:
    """Load channel presets from a resolved CSV location."""

    resolved_path = resolve_data_file(
        APP_NAME,
        "channel_presets.csv",
        cli_path=presets_path,
        base_dir=data_dir,
        bundle_path=DEFAULT_PRESETS_PATH,
    )
    if resolved_path != DEFAULT_PRESETS_PATH:
        ensure_directory(resolved_path.parent)
    return load_presets_from_csv(resolved_path)


def load_presets_from_csv(presets_path: Path) -> List[ChannelPreset]:
    """Read channel presets from a CSV file at an arbitrary location."""

    if not presets_path.exists():
        raise FileNotFoundError(
            f"Missing preset file: {presets_path}. Ensure it is packaged with the GUI."
        )

    presets: List[ChannelPreset] = []
    with presets_path.open(newline="", encoding="utf-8") as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            try:
                label = row.get("display_name") or row.get("channel_id")
                frequency = float(row["frequency_hz"]) if row.get("frequency_hz") else None
                ctcss_val = (
                    float(row["ctcss_hz"]) if row.get("ctcss_hz") else None
                )
                dcs_val_raw = row.get("dcs_code")
                dcs_val = dcs_val_raw.strip() if dcs_val_raw and dcs_val_raw.strip() else None
            except (KeyError, ValueError) as exc:
                raise ValueError(
                    f"Invalid row in {presets_path}: {row!r}"  # pragma: no cover - configuration issue
                ) from exc

            if label is None or frequency is None:
                raise ValueError(
                    f"Incomplete preset definition in {presets_path}: {row!r}"  # pragma: no cover - configuration issue
                )

            key = row.get("channel_id", label)
            presets.append(
                ChannelPreset(
                    key=str(key),
                    label=str(label),
                    frequency_hz=frequency,
                    ctcss_hz=ctcss_val,
                    dcs_code=dcs_val,
                )
            )

    if not presets:
        raise ValueError(
            f"No channel presets were loaded from {presets_path}"  # pragma: no cover - configuration issue
        )

    return presets


def presets_to_rows(presets: Sequence[ChannelPreset]) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    for preset in presets:
        rows.append(
            {
                "channel_id": preset.key,
                "display_name": preset.label,
                "frequency_hz": f"{preset.frequency_hz}",
                "ctcss_hz": "" if preset.ctcss_hz is None else f"{preset.ctcss_hz}",
                "dcs_code": preset.dcs_code or "",
            }
        )
    return rows


def save_presets_to_csv(presets: Sequence[ChannelPreset], path: Path) -> None:
    rows = presets_to_rows(presets)
    buffer = io.StringIO(newline="")
    fieldnames = ["channel_id", "display_name", "frequency_hz", "ctcss_hz", "dcs_code"]
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    atomic_write(path, buffer.getvalue())


def rows_to_presets(rows: Sequence[Dict[str, str]]) -> List[ChannelPreset]:
    presets: List[ChannelPreset] = []
    for row in rows:
        try:
            label = row.get("display_name") or row.get("channel_id")
            freq_text = row.get("frequency_hz")
            if label is None or freq_text is None:
                continue
            frequency = float(freq_text)
            ctcss_text = row.get("ctcss_hz") or ""
            ctcss = float(ctcss_text) if ctcss_text else None
            dcs_code = row.get("dcs_code") or None
        except (ValueError, TypeError):
            continue
        key = row.get("channel_id") or str(label)
        presets.append(
            ChannelPreset(
                key=str(key),
                label=str(label),
                frequency_hz=frequency,
                ctcss_hz=ctcss,
                dcs_code=dcs_code,
            )
        )
    return presets


class ChannelRow(ttk.Frame):
    """Widget that captures per-channel configuration."""

    _last_directory: Optional[Path] = None

    def __init__(self, master, presets: List[ChannelPreset], controller):
        super().__init__(master, style="ChannelRow.TFrame", padding=8)
        self.controller = controller
        self.preset_var = tk.StringVar()
        self.gain_var = tk.StringVar(value="1.0")
        self.playlist: List[PlaylistEntry] = []
        self._labels = [preset.label for preset in presets]
        self._preset_map: Dict[str, ChannelPreset] = {
            preset.label: preset for preset in presets
        }
        self._base_style = "ChannelRow.TFrame"
        self._error_style = "ChannelRowError.TFrame"

        self.header = ttk.Label(self, text="Channel")
        self.header.grid(row=0, column=0, padx=4, pady=2, sticky="w")

        ttk.Label(self, text="FRS/GMRS Channel:").grid(
            row=1, column=0, padx=4, pady=2, sticky="w"
        )
        self.channel_combo = ttk.Combobox(
            self,
            textvariable=self.preset_var,
            values=self._labels,
            state="readonly",
            width=35,
        )
        self.channel_combo.grid(row=1, column=1, padx=4, pady=2, sticky="we")
        if self._labels:
            self.preset_var.set(self._labels[0])

        ttk.Label(self, text="Gain (linear):").grid(
            row=2, column=0, padx=4, pady=2, sticky="w"
        )
        self.gain_entry = ttk.Entry(self, textvariable=self.gain_var, width=10)
        self.gain_entry.grid(row=2, column=1, padx=4, pady=2, sticky="we")

        self.ctcss_mode = tk.StringVar(value="off")
        self.ctcss_custom_var = tk.StringVar()
        self.dcs_mode = tk.StringVar(value="off")
        self.dcs_custom_var = tk.StringVar()
        self._ctcss_user_override = False
        self._dcs_user_override = False
        self._ctcss_value: Optional[float] = None
        self._dcs_value: Optional[str] = None

        ttk.Label(self, text="CTCSS Tone:").grid(
            row=3, column=0, padx=4, pady=2, sticky="nw"
        )
        ctcss_frame = ttk.Frame(self)
        ctcss_frame.grid(row=3, column=1, columnspan=2, sticky="we", padx=4, pady=2)
        self.ctcss_off = ttk.Radiobutton(
            ctcss_frame,
            text="Off",
            variable=self.ctcss_mode,
            value="off",
            command=self._on_ctcss_mode_change,
        )
        self.ctcss_off.grid(row=0, column=0, sticky="w")
        self.ctcss_preset_radio = ttk.Radiobutton(
            ctcss_frame,
            text="Preset",
            variable=self.ctcss_mode,
            value="preset",
            command=self._on_ctcss_mode_change,
        )
        self.ctcss_preset_radio.grid(row=0, column=1, sticky="w", padx=(8, 0))
        self.ctcss_custom_radio = ttk.Radiobutton(
            ctcss_frame,
            text="Custom:",
            variable=self.ctcss_mode,
            value="custom",
            command=self._on_ctcss_mode_change,
        )
        self.ctcss_custom_radio.grid(row=0, column=2, sticky="w", padx=(8, 0))
        self.ctcss_entry = ttk.Entry(ctcss_frame, textvariable=self.ctcss_custom_var, width=8)
        self.ctcss_entry.grid(row=0, column=3, sticky="w", padx=(2, 0))

        ttk.Label(self, text="DCS Code:").grid(
            row=4, column=0, padx=4, pady=2, sticky="nw"
        )
        dcs_frame = ttk.Frame(self)
        dcs_frame.grid(row=4, column=1, columnspan=2, sticky="we", padx=4, pady=2)
        self.dcs_off = ttk.Radiobutton(
            dcs_frame,
            text="Off",
            variable=self.dcs_mode,
            value="off",
            command=self._on_dcs_mode_change,
        )
        self.dcs_off.grid(row=0, column=0, sticky="w")
        self.dcs_preset_radio = ttk.Radiobutton(
            dcs_frame,
            text="Preset",
            variable=self.dcs_mode,
            value="preset",
            command=self._on_dcs_mode_change,
        )
        self.dcs_preset_radio.grid(row=0, column=1, sticky="w", padx=(8, 0))
        self.dcs_custom_radio = ttk.Radiobutton(
            dcs_frame,
            text="Custom:",
            variable=self.dcs_mode,
            value="custom",
            command=self._on_dcs_mode_change,
        )
        self.dcs_custom_radio.grid(row=0, column=2, sticky="w", padx=(8, 0))
        self.dcs_entry = ttk.Entry(dcs_frame, textvariable=self.dcs_custom_var, width=8)
        self.dcs_entry.grid(row=0, column=3, sticky="w", padx=(2, 0))

        self.tone_status = ttk.Label(self, text="CTCSS off · DCS off", font=("", 9))
        self.tone_status.grid(row=5, column=1, columnspan=2, padx=4, pady=(2, 2), sticky="w")

        ttk.Label(self, text="Playlist:").grid(
            row=6, column=0, padx=4, pady=(6, 2), sticky="nw"
        )
        playlist_frame = ttk.Frame(self)
        playlist_frame.grid(
            row=7, column=0, columnspan=2, padx=4, pady=2, sticky="nsew"
        )
        columns = ("duration", "rate")
        self.file_listbox = ttk.Treeview(
            playlist_frame,
            columns=columns,
            show="tree headings",
            selectmode="browse",
            height=6,
        )
        self.file_listbox.heading("#0", text="File")
        self.file_listbox.heading("duration", text="Duration (s)")
        self.file_listbox.heading("rate", text="Sample Rate")
        self.file_listbox.column("#0", width=220, stretch=True)
        self.file_listbox.column("duration", width=110, anchor="center")
        self.file_listbox.column("rate", width=110, anchor="center")
        self.file_listbox.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(
            playlist_frame, orient="vertical", command=self.file_listbox.yview
        )
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.file_listbox.configure(yscrollcommand=scrollbar.set)
        playlist_frame.columnconfigure(0, weight=1)

        self.playlist_summary_var = tk.StringVar(value="No files queued")
        summary_label = ttk.Label(self, textvariable=self.playlist_summary_var, font=("", 9))
        summary_label.grid(row=8, column=0, columnspan=2, padx=4, pady=(0, 4), sticky="w")

        controls = ttk.Frame(self)
        controls.grid(row=7, column=2, rowspan=1, padx=4, pady=2, sticky="n")
        controls.columnconfigure(0, weight=1)
        ttk.Button(controls, text="Add…", command=self.add_files).grid(
            row=0, column=0, pady=2, sticky="we"
        )
        ttk.Button(controls, text="Remove", command=self.remove_selected_files).grid(
            row=1, column=0, pady=2, sticky="we"
        )
        ttk.Button(controls, text="Move Up", command=lambda: self.move_selected(-1)).grid(
            row=2, column=0, pady=2, sticky="we"
        )
        ttk.Button(controls, text="Move Down", command=lambda: self.move_selected(1)).grid(
            row=3, column=0, pady=2, sticky="we"
        )
        ttk.Button(controls, text="Clear All", command=self.clear_playlist).grid(
            row=4, column=0, pady=2, sticky="we"
        )

        channel_controls = ttk.Frame(self)
        channel_controls.grid(row=9, column=0, columnspan=3, padx=4, pady=4, sticky="e")
        ttk.Button(channel_controls, text="Duplicate", command=self.duplicate_channel).grid(
            row=0, column=0, padx=2
        )
        ttk.Button(channel_controls, text="Move ▲", command=lambda: self.move_channel(-1)).grid(
            row=0, column=1, padx=2
        )
        ttk.Button(channel_controls, text="Move ▼", command=lambda: self.move_channel(1)).grid(
            row=0, column=2, padx=2
        )
        ttk.Button(channel_controls, text="Remove", command=self.remove).grid(row=0, column=3, padx=2)

        self.error_var = tk.StringVar(value="")
        self.error_label = ttk.Label(self, textvariable=self.error_var, foreground="#a40000")
        self.error_label.grid(row=10, column=0, columnspan=2, padx=4, pady=2, sticky="w")

        self.channel_combo.bind("<<ComboboxSelected>>", self._on_preset_changed)
        self._update_tone_controls()

        self.columnconfigure(1, weight=1)
        self.rowconfigure(7, weight=1)

    def set_index(self, index: int) -> None:
        self.header.config(text=f"Channel {index}")


    def get_frequency(self) -> float:
        label = self.preset_var.get().strip()
        if not label:
            raise ValueError("Each channel requires a preset selection")
        preset = self._preset_map.get(label)
        if preset is None:
            raise ValueError(f"Unknown preset selected: {label}")
        return preset.frequency_hz

    def get_ctcss_tone(self) -> Optional[float]:
        mode = self.ctcss_mode.get()
        if mode == "preset":
            if self._ctcss_value is None:
                raise ValueError("No preset CTCSS tone is available for this channel")
            return float(self._ctcss_value)
        if mode == "custom":
            value = self.ctcss_custom_var.get().strip()
            if not value:
                raise ValueError("Enter a custom CTCSS tone or turn it off")
            try:
                tone = float(value)
            except ValueError as exc:
                raise ValueError("Custom CTCSS tone must be numeric") from exc
            if tone <= 0:
                raise ValueError("Custom CTCSS tone must be positive")
            return tone
        return None

    def get_dcs_code(self) -> Optional[str]:
        mode = self.dcs_mode.get()
        if mode == "preset":
            if not self._dcs_value:
                raise ValueError("No preset DCS code is available for this channel")
            return str(self._dcs_value)
        if mode == "custom":
            code = self.dcs_custom_var.get().strip().upper()
            if not code:
                raise ValueError("Enter a custom DCS code or turn it off")
            if not code.isdigit():
                raise ValueError("Custom DCS codes must be numeric")
            return code
        return None

    def add_files(self) -> None:
        self.clear_error()
        dialog_kwargs = dict(
            title="Select audio files",
            filetypes=[
                ("Audio files", "*.wav *.mp3"),
                ("WAV files", "*.wav"),
                ("MP3 files", "*.mp3"),
                ("All files", "*.*"),
            ],
        )
        if self._last_directory is not None:
            dialog_kwargs["initialdir"] = str(self._last_directory)
        filenames = filedialog.askopenfilenames(**dialog_kwargs)
        if filenames:
            for name in filenames:
                path = Path(name).expanduser()
                self.playlist.append(self._create_entry(path))
                self.__class__._last_directory = path.parent
            self._refresh_playlist()

    def clear_playlist(self) -> None:
        """Remove every queued file with a single action."""

        if not self.playlist:
            return
        self.clear_error()
        self.playlist.clear()
        self._refresh_playlist()

    def remove_selected_files(self) -> None:
        self.clear_error()
        selections = self.file_listbox.selection()
        if not selections:
            return
        indices = sorted([self.file_listbox.index(item) for item in selections], reverse=True)
        for idx in indices:
            if 0 <= idx < len(self.playlist):
                self.playlist.pop(idx)
        self._refresh_playlist()

    def move_selected(self, direction: int) -> None:
        if direction not in (-1, 1):
            return
        self.clear_error()
        selection = self.file_listbox.selection()
        if not selection:
            return
        index = self.file_listbox.index(selection[0])
        new_index = index + direction
        if not (0 <= new_index < len(self.playlist)):
            return
        self.playlist[index], self.playlist[new_index] = (
            self.playlist[new_index],
            self.playlist[index],
        )
        self._refresh_playlist()
        item_id = self.file_listbox.get_children()[new_index]
        self.file_listbox.selection_set(item_id)

    def _refresh_playlist(self) -> None:
        for item in self.file_listbox.get_children():
            self.file_listbox.delete(item)
        total_duration = 0.0
        for entry in self.playlist:
            duration_text = "–"
            rate_text = "–"
            if entry.duration is not None:
                total_duration += entry.duration
                duration_text = f"{entry.duration:.2f}"
            if entry.sample_rate is not None:
                rate_text = f"{entry.sample_rate:,d} Hz"
            self.file_listbox.insert(
                "",
                tk.END,
                text=entry.path.name,
                values=(duration_text, rate_text),
            )
        if self.playlist:
            self.playlist_summary_var.set(
                f"{len(self.playlist)} file(s) • Total duration: {total_duration:.2f}s"
            )
        else:
            self.playlist_summary_var.set("No files queued")

    def remove(self) -> None:
        self.controller.remove_channel(self)

    def duplicate_channel(self) -> None:
        self.controller.duplicate_channel(self)

    def move_channel(self, direction: int) -> None:
        self.controller.move_channel(self, direction)

    def _on_preset_changed(self, _event=None) -> None:
        self.clear_error()
        self._ctcss_user_override = False
        self._dcs_user_override = False
        self.ctcss_custom_var.set("")
        self.dcs_custom_var.set("")
        self._update_tone_controls()

    def _create_entry(self, path: Path) -> PlaylistEntry:
        duration: Optional[float] = None
        sample_rate: Optional[int] = None
        if path.suffix.lower() == ".wav":
            try:
                with contextlib.closing(wave.open(str(path), "rb")) as wav_file:
                    frames = wav_file.getnframes()
                    rate = wav_file.getframerate()
                    sample_rate = rate
                    duration = frames / rate if rate else None
            except Exception:
                duration = None
                sample_rate = None
        elif path.suffix.lower() == ".mp3":
            mp3_loader = _get_mp3_loader()
            if mp3_loader is not None:
                try:
                    audio = mp3_loader(str(path))
                    info = getattr(audio, "info", None)
                    if info is not None:
                        duration = getattr(info, "length", None)
                        sample_rate = getattr(info, "sample_rate", None)
                        if duration is not None:
                            duration = float(duration)
                        if sample_rate is not None:
                            sample_rate = int(sample_rate)
                except Exception:
                    duration = None
                    sample_rate = None
        return PlaylistEntry(path=path, duration=duration, sample_rate=sample_rate)

    def get_playlist_paths(self) -> List[Path]:
        return [entry.path for entry in self.playlist]

    def _update_tone_controls(self) -> None:
        label = self.preset_var.get().strip()
        preset = self._preset_map.get(label)
        self._ctcss_value = preset.ctcss_hz if preset else None
        self._dcs_value = preset.dcs_code if preset else None

        ctcss_available = self._ctcss_value is not None
        if ctcss_available:
            self.ctcss_preset_radio.state(["!disabled"])
            self.ctcss_preset_radio.config(
                text=f"Preset ({self._ctcss_value:.1f} Hz)"
            )
            if not self._ctcss_user_override:
                self.ctcss_mode.set("preset")
        else:
            self.ctcss_preset_radio.state(["disabled"])
            if not self._ctcss_user_override or self.ctcss_mode.get() == "preset":
                self.ctcss_mode.set("off")

        dcs_available = self._dcs_value is not None
        if dcs_available:
            self.dcs_preset_radio.state(["!disabled"])
            self.dcs_preset_radio.config(text=f"Preset ({self._dcs_value})")
            if not self._dcs_user_override and self.ctcss_mode.get() == "off":
                self.dcs_mode.set("preset")
        else:
            self.dcs_preset_radio.state(["disabled"])
            if not self._dcs_user_override or self.dcs_mode.get() == "preset":
                self.dcs_mode.set("off")

        # If a preset tone was automatically applied, ensure the opposite
        # signalling mode stays disabled to avoid conflicting defaults.
        if self.ctcss_mode.get() != "off" and self.dcs_mode.get() != "off":
            if ctcss_available:
                self.dcs_mode.set("off")
            else:
                self.ctcss_mode.set("off")

    def _on_ctcss_mode_change(self) -> None:
        self._ctcss_user_override = True
        if self.ctcss_mode.get() != "off" and self.dcs_mode.get() != "off":
            self.dcs_mode.set("off")
        self._refresh_tone_status()

    def _on_dcs_mode_change(self) -> None:
        self._dcs_user_override = True
        if self.dcs_mode.get() != "off" and self.ctcss_mode.get() != "off":
            self.ctcss_mode.set("off")
        self._refresh_tone_status()

    def _refresh_tone_status(self) -> None:
        self.clear_error()
        if self.ctcss_mode.get() == "custom":
            self.ctcss_entry.state(["!disabled"])
        else:
            self.ctcss_entry.state(["disabled"])

        if self.dcs_mode.get() == "custom":
            self.dcs_entry.state(["!disabled"])
        else:
            self.dcs_entry.state(["disabled"])

        status_bits = []
        if self.ctcss_mode.get() == "off":
            status_bits.append("CTCSS off")
        elif self.ctcss_mode.get() == "preset":
            status_bits.append("CTCSS preset")
        else:
            status_bits.append("CTCSS custom")

        if self.dcs_mode.get() == "off":
            status_bits.append("DCS off")
        elif self.dcs_mode.get() == "preset":
            status_bits.append("DCS preset")
        else:
            status_bits.append("DCS custom")

        self.tone_status.config(text=" · ".join(status_bits))

    def clear_error(self) -> None:
        self.error_var.set("")
        self.configure(style=self._base_style)

    def show_error(self, message: str) -> None:
        self.error_var.set(message)
        self.configure(style=self._error_style)

    def update_presets(self, presets: List[ChannelPreset]) -> None:
        """Refresh the available preset list while preserving the selection when possible."""

        current_label = self.preset_var.get()
        self._labels = [preset.label for preset in presets]
        self._preset_map = {preset.label: preset for preset in presets}
        self.channel_combo.configure(values=self._labels)
        if current_label in self._preset_map:
            self.preset_var.set(current_label)
        elif self._labels:
            self.preset_var.set(self._labels[0])
        else:
            self.preset_var.set("")
        self._update_tone_controls()

    def set_playlist(self, paths: Sequence[Path]) -> None:
        self.playlist = [self._create_entry(path) for path in paths]
        self._refresh_playlist()

    def serialize_state(self) -> Dict[str, object]:
        return {
            "preset_label": self.preset_var.get(),
            "gain": self.gain_var.get(),
            "ctcss_mode": self.ctcss_mode.get(),
            "ctcss_custom": self.ctcss_custom_var.get(),
            "dcs_mode": self.dcs_mode.get(),
            "dcs_custom": self.dcs_custom_var.get(),
            "playlist": [str(entry.path) for entry in self.playlist],
        }

    def apply_state(self, data: Dict[str, object]) -> None:
        preset_label = data.get("preset_label")
        if preset_label and preset_label in self._preset_map:
            self.preset_var.set(preset_label)
        elif self._labels:
            self.preset_var.set(self._labels[0])
        self.gain_var.set(data.get("gain", "1.0"))
        self.ctcss_mode.set(data.get("ctcss_mode", "off"))
        self.ctcss_custom_var.set(data.get("ctcss_custom", ""))
        self.dcs_mode.set(data.get("dcs_mode", "off"))
        self.dcs_custom_var.set(data.get("dcs_custom", ""))
        playlist_paths = [Path(p) for p in data.get("playlist", [])]
        self.set_playlist(playlist_paths)
        self._refresh_tone_status()


class PresetEditorDialog(simpledialog.Dialog):
    """Modal dialog that edits/creates a preset entry."""

    def __init__(self, master, *, existing_keys: Sequence[str], preset: Optional[ChannelPreset] = None):
        self._existing_keys = {key for key in existing_keys}
        self._preset = preset
        if preset is not None:
            self._existing_keys.discard(preset.key)
        self.result: Optional[ChannelPreset] = None
        super().__init__(master, title="Edit Preset" if preset else "Add Preset")

    def body(self, master):
        ttk.Label(master, text="Channel ID:").grid(row=0, column=0, sticky="w", padx=4, pady=4)
        ttk.Label(master, text="Display Name:").grid(row=1, column=0, sticky="w", padx=4, pady=4)
        ttk.Label(master, text="Frequency (Hz):").grid(row=2, column=0, sticky="w", padx=4, pady=4)
        ttk.Label(master, text="CTCSS (Hz):").grid(row=3, column=0, sticky="w", padx=4, pady=4)
        ttk.Label(master, text="DCS Code:").grid(row=4, column=0, sticky="w", padx=4, pady=4)

        self.key_var = tk.StringVar(value=self._preset.key if self._preset else "")
        self.label_var = tk.StringVar(value=self._preset.label if self._preset else "")
        self.freq_var = tk.StringVar(
            value=f"{self._preset.frequency_hz}" if self._preset else ""
        )
        self.ctcss_var = tk.StringVar(
            value="" if not self._preset or self._preset.ctcss_hz is None else f"{self._preset.ctcss_hz}"
        )
        self.dcs_var = tk.StringVar(value=self._preset.dcs_code if self._preset else "")

        self.key_entry = ttk.Entry(master, textvariable=self.key_var, width=30)
        self.key_entry.grid(row=0, column=1, padx=4, pady=4)
        self.label_entry = ttk.Entry(master, textvariable=self.label_var, width=30)
        self.label_entry.grid(row=1, column=1, padx=4, pady=4)
        self.freq_entry = ttk.Entry(master, textvariable=self.freq_var, width=30)
        self.freq_entry.grid(row=2, column=1, padx=4, pady=4)
        self.ctcss_entry = ttk.Entry(master, textvariable=self.ctcss_var, width=30)
        self.ctcss_entry.grid(row=3, column=1, padx=4, pady=4)
        self.dcs_entry = ttk.Entry(master, textvariable=self.dcs_var, width=30)
        self.dcs_entry.grid(row=4, column=1, padx=4, pady=4)

        self.label_var.trace_add("write", self._suggest_key)
        return self.label_entry

    def validate(self) -> bool:  # pragma: no cover - modal UI
        label = self.label_var.get().strip()
        key = self.key_var.get().strip() or label
        if not label:
            messagebox.showerror("Missing information", "Display name is required.", parent=self)
            return False
        if not key:
            messagebox.showerror("Missing information", "Channel ID is required.", parent=self)
            return False
        freq_text = self.freq_var.get().strip()
        try:
            freq = float(freq_text)
        except ValueError:
            messagebox.showerror("Invalid frequency", "Frequency must be numeric.", parent=self)
            return False
        if freq <= 0:
            messagebox.showerror(
                "Invalid frequency", "Frequency must be positive.", parent=self
            )
            return False
        ctcss_text = self.ctcss_var.get().strip()
        ctcss = None
        if ctcss_text:
            try:
                ctcss = float(ctcss_text)
            except ValueError:
                messagebox.showerror(
                    "Invalid CTCSS", "CTCSS tone must be numeric.", parent=self
                )
                return False
        dcs = self.dcs_var.get().strip() or None
        if key in self._existing_keys:
            messagebox.showerror("Duplicate ID", "Channel ID already exists.", parent=self)
            return False
        self.result = ChannelPreset(key=key, label=label, frequency_hz=freq, ctcss_hz=ctcss, dcs_code=dcs)
        return True

    def apply(self):  # pragma: no cover - modal UI
        pass

    def _suggest_key(self, *_):
        if self._preset:
            return
        if self.key_var.get().strip():
            return
        label = self.label_var.get().strip()
        if not label:
            return
        sanitized = "".join(ch for ch in label if ch.isalnum() or ch in ("_", "-"))
        sanitized = sanitized or label.replace(" ", "_")
        self.key_var.set(sanitized)


class PresetManagerDialog(tk.Toplevel):
    """Dialog that lets the operator manage preset CSV entries."""

    def __init__(self, master, presets: Sequence[ChannelPreset]):
        super().__init__(master)
        self.title("Preset Manager")
        self.transient(master)
        self.resizable(True, True)
        self.presets: List[ChannelPreset] = list(presets)
        self.result: Optional[List[ChannelPreset]] = None

        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        ttk.Label(self, text="Available presets").grid(row=0, column=0, padx=10, pady=(10, 0), sticky="w")
        columns = ("label", "frequency", "ctcss", "dcs")
        self.tree = ttk.Treeview(
            self,
            columns=columns,
            show="headings",
            selectmode="browse",
            height=12,
        )
        self.tree.heading("label", text="Display Name")
        self.tree.heading("frequency", text="Frequency (Hz)")
        self.tree.heading("ctcss", text="CTCSS")
        self.tree.heading("dcs", text="DCS")
        self.tree.column("label", width=220)
        self.tree.column("frequency", width=120, anchor="center")
        self.tree.column("ctcss", width=100, anchor="center")
        self.tree.column("dcs", width=80, anchor="center")
        self.tree.grid(row=1, column=0, padx=10, pady=5, sticky="nsew")

        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        scrollbar.grid(row=1, column=1, sticky="ns", pady=5)
        self.tree.configure(yscrollcommand=scrollbar.set)

        btn_frame = ttk.Frame(self)
        btn_frame.grid(row=2, column=0, columnspan=2, padx=10, pady=5, sticky="e")
        ttk.Button(btn_frame, text="Add", command=self.add_preset).grid(row=0, column=0, padx=4)
        ttk.Button(btn_frame, text="Edit", command=self.edit_selected).grid(row=0, column=1, padx=4)
        ttk.Button(btn_frame, text="Delete", command=self.delete_selected).grid(row=0, column=2, padx=4)
        ttk.Button(btn_frame, text="Import…", command=self.import_presets).grid(row=0, column=3, padx=4)
        ttk.Button(btn_frame, text="Export…", command=self.export_presets).grid(row=0, column=4, padx=4)

        action_frame = ttk.Frame(self)
        action_frame.grid(row=3, column=0, columnspan=2, padx=10, pady=(0, 10), sticky="e")
        ttk.Button(action_frame, text="Save Changes", command=self.save_changes).grid(row=0, column=0, padx=4)
        ttk.Button(action_frame, text="Cancel", command=self._on_cancel).grid(row=0, column=1, padx=4)

        self.status_var = tk.StringVar(value="")
        ttk.Label(self, textvariable=self.status_var, font=("", 9, "italic")).grid(
            row=4, column=0, columnspan=2, sticky="w", padx=10, pady=(0, 10)
        )

        self.protocol("WM_DELETE_WINDOW", self._on_cancel)
        self._refresh_tree()
        self.grab_set()

    def _refresh_tree(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        for preset in self.presets:
            ctcss = "–" if preset.ctcss_hz is None else f"{preset.ctcss_hz:g}"
            dcs = preset.dcs_code or "–"
            self.tree.insert(
                "",
                tk.END,
                values=(preset.label, f"{preset.frequency_hz:g}", ctcss, dcs),
            )

    def _selected_index(self) -> Optional[int]:
        selection = self.tree.selection()
        if not selection:
            return None
        return self.tree.index(selection[0])

    def add_preset(self):  # pragma: no cover - modal UI
        dialog = PresetEditorDialog(self, existing_keys=[preset.key for preset in self.presets])
        if dialog.result is not None:
            self.presets.append(dialog.result)
            self._refresh_tree()
            self.status_var.set(f"Added preset '{dialog.result.label}'.")

    def edit_selected(self):  # pragma: no cover - modal UI
        idx = self._selected_index()
        if idx is None:
            return
        dialog = PresetEditorDialog(
            self,
            existing_keys=[preset.key for preset in self.presets],
            preset=self.presets[idx],
        )
        if dialog.result is not None:
            self.presets[idx] = dialog.result
            self._refresh_tree()
            self.status_var.set(f"Updated preset '{dialog.result.label}'.")

    def delete_selected(self):  # pragma: no cover - modal UI
        idx = self._selected_index()
        if idx is None:
            return
        preset = self.presets.pop(idx)
        self._refresh_tree()
        self.status_var.set(f"Deleted preset '{preset.label}'.")

    def import_presets(self):  # pragma: no cover - modal UI
        filename = filedialog.askopenfilename(
            parent=self,
            title="Import preset CSV",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not filename:
            return
        try:
            imported = load_presets_from_csv(Path(filename))
        except Exception as exc:
            messagebox.showerror("Import failed", str(exc), parent=self)
            return
        self.presets = list(imported)
        self._refresh_tree()
        self.status_var.set(f"Loaded {len(imported)} presets from {Path(filename).name}.")

    def export_presets(self):  # pragma: no cover - modal UI
        if not self.presets:
            messagebox.showinfo("No presets", "There are no presets to export.", parent=self)
            return
        filename = filedialog.asksaveasfilename(
            parent=self,
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            title="Export presets",
        )
        if not filename:
            return
        try:
            save_presets_to_csv(self.presets, Path(filename))
        except Exception as exc:
            messagebox.showerror("Export failed", str(exc), parent=self)
            return
        self.status_var.set(f"Exported {len(self.presets)} presets to {Path(filename).name}.")

    def save_changes(self):  # pragma: no cover - modal UI
        self.result = list(self.presets)
        self.destroy()


class TransmitterSettingsDialog(simpledialog.Dialog):
    """Modal dialog used to edit the persisted transmitter defaults."""

    def __init__(self, master, settings: Dict[str, Optional[float]]):
        self._settings = dict(settings)
        self.result: Optional[Dict[str, Optional[float]]] = None
        self._vars: Dict[str, tk.StringVar] = {}
        super().__init__(master, title="Transmitter Settings")

    def body(self, master):  # pragma: no cover - modal UI
        first_entry = None
        for idx, field in enumerate(TRANSMITTER_SETTING_FIELDS):
            row = idx * 2
            ttk.Label(master, text=field["label"]).grid(
                row=row, column=0, sticky="w", padx=6, pady=(8 if idx == 0 else 4, 0)
            )
            key = field["key"]
            var = tk.StringVar(value=_format_setting_value(self._settings.get(key)))
            entry = ttk.Entry(master, textvariable=var)
            entry.grid(row=row, column=1, sticky="we", padx=6, pady=(8 if idx == 0 else 4, 0))
            if first_entry is None:
                first_entry = entry
            self._vars[key] = var
            help_text = field.get("help")
            if help_text:
                ttk.Label(master, text=help_text, font=("", 9, "italic"), wraplength=360).grid(
                    row=row + 1,
                    column=0,
                    columnspan=2,
                    sticky="w",
                    padx=6,
                    pady=(0, 2),
                )
        master.columnconfigure(1, weight=1)
        return first_entry

    def buttonbox(self):  # pragma: no cover - modal UI
        box = ttk.Frame(self)
        restore = ttk.Button(box, text="Restore Defaults", command=self._restore_defaults)
        restore.grid(row=0, column=0, padx=5, pady=5)
        ok_btn = ttk.Button(box, text="Save", command=self.ok)
        ok_btn.grid(row=0, column=1, padx=5, pady=5)
        cancel_btn = ttk.Button(box, text="Cancel", command=self.cancel)
        cancel_btn.grid(row=0, column=2, padx=5, pady=5)
        self.bind("<Return>", self.ok)
        self.bind("<Escape>", self.cancel)
        box.pack(anchor="e", padx=6, pady=6)

    def validate(self) -> bool:  # pragma: no cover - modal UI
        try:
            new_settings = self._collect_settings()
        except ValueError as exc:
            messagebox.showerror("Invalid setting", str(exc), parent=self)
            return False
        self.result = new_settings
        return True

    def _collect_settings(self) -> Dict[str, Optional[float]]:
        collected: Dict[str, Optional[float]] = {}
        for field in TRANSMITTER_SETTING_FIELDS:
            key = field["key"]
            text = self._vars[key].get().strip()
            if not text:
                if field.get("allow_empty"):
                    collected[key] = None
                    continue
                raise ValueError(f"{field['name']} is required.")
            try:
                value = float(text)
            except ValueError as exc:
                raise ValueError(f"{field['name']} must be numeric.") from exc
            collected[key] = value
        open_val = collected["gate_open_threshold"]
        close_val = collected["gate_close_threshold"]
        if open_val is not None and close_val is not None and close_val >= open_val:
            raise ValueError("Gate close threshold must be lower than the open threshold.")
        return collected

    def _restore_defaults(self) -> None:
        for field in TRANSMITTER_SETTING_FIELDS:
            key = field["key"]
            default_value = DEFAULT_TRANSMITTER_SETTINGS.get(key)
            self._vars[key].set(_format_setting_value(default_value))


    def _on_cancel(self):  # pragma: no cover - modal UI
        self.result = None
        self.destroy()

class ChannelValidationError(ValueError):
    """Raised when a specific channel row fails validation."""

    def __init__(self, channel_index: int, message: str):
        super().__init__(message)
        self.channel_index = channel_index


class CollapsibleSection(ttk.Frame):
    """A simple collapsible container with a toggle button header."""

    def __init__(self, master, title: str, *, collapsed: bool = False):
        super().__init__(master)
        self._title = title
        self._collapsed = bool(collapsed)

        self.columnconfigure(0, weight=1)

        header = ttk.Frame(self)
        header.grid(row=0, column=0, sticky="we")

        self._toggle_btn = ttk.Button(
            header,
            command=self.toggle,
            style="Toolbutton",
            padding=2,
        )
        self._toggle_btn.grid(row=0, column=0, sticky="w")

        self._title_label = ttk.Label(header, text=title, font=("", 11, "bold"))
        self._title_label.grid(row=0, column=1, sticky="w", padx=(4, 0))

        header.columnconfigure(1, weight=1)

        self.content_frame = ttk.Frame(self)
        self.content_frame.grid(row=1, column=0, sticky="we")
        if collapsed:
            self.content_frame.grid_remove()

        self._refresh_toggle_text()

    def set_title(self, title: str) -> None:
        self._title = title
        self._title_label.config(text=title)
        self._refresh_toggle_text()

    def toggle(self) -> None:
        self._collapsed = not self._collapsed
        if self._collapsed:
            self.content_frame.grid_remove()
        else:
            self.content_frame.grid(row=1, column=0, sticky="we")
        self._refresh_toggle_text()

    def set_collapsed(self, collapsed: bool) -> None:
        """Force the collapse state without the user clicking."""

        if self._collapsed == collapsed:
            return
        self._collapsed = collapsed
        if self._collapsed:
            self.content_frame.grid_remove()
        else:
            self.content_frame.grid(row=1, column=0, sticky="we")
        self._refresh_toggle_text()

    def _refresh_toggle_text(self) -> None:
        symbol = "►" if self._collapsed else "▼"
        self._toggle_btn.config(text=symbol)


class MultiChannelApp(tk.Tk):
    TX_GAIN_DEFAULTS = {
        "hackrf": 0.0,
        "pluto": -10.0,
        "plutoplus": -10.0,
        "plutoplussdr": -10.0,
    }

    def __init__(
        self,
        tx_sample_rate: Optional[float] = None,
        mod_sample_rate: Optional[float] = None,
        deviation_hz: Optional[float] = None,
        master_scale: Optional[float] = None,
        ctcss_level: Optional[float] = None,
        ctcss_deviation: Optional[float] = None,
        tx_gain_override: Optional[float] = None,
        gate_open_threshold: Optional[float] = None,
        gate_close_threshold: Optional[float] = None,
        gate_attack_ms: Optional[float] = None,
        gate_release_ms: Optional[float] = None,
        *,
        settings_path: Optional[Path] = None,
        presets_path: Optional[Path] = None,
        data_dir: Optional[Path] = None,
    ):
        super().__init__()

        self.title("Multi-channel NBFM TX")
        self.resizable(True, True)

        try:
            self.presets = load_channel_presets(
                presets_path=presets_path,
                data_dir=data_dir,
            )
        except Exception as exc:  # pragma: no cover - UI feedback
            messagebox.showerror("Preset load failure", str(exc))
            raise

        self._settings_path = settings_path or TRANSMITTER_SETTINGS_PATH
        try:
            self._persisted_settings = load_transmitter_settings(self._settings_path)
        except Exception as exc:  # pragma: no cover - UI feedback
            messagebox.showerror("Transmitter defaults", str(exc))
            raise
        self._cli_overrides: Dict[str, Optional[float]] = {}
        self._active_settings = self._compose_active_settings(
            tx_sample_rate=tx_sample_rate,
            mod_sample_rate=mod_sample_rate,
            deviation_hz=deviation_hz,
            master_scale=master_scale,
            ctcss_level=ctcss_level,
            ctcss_deviation=ctcss_deviation,
            tx_gain_override=tx_gain_override,
            gate_open_threshold=gate_open_threshold,
            gate_close_threshold=gate_close_threshold,
            gate_attack_ms=gate_attack_ms,
            gate_release_ms=gate_release_ms,
        )

        self._icon_image: Optional[tk.PhotoImage] = None
        try:
            icon_source = tk.PhotoImage(data=APP_ICON_BASE64)
            max_dim = max(icon_source.width(), icon_source.height())
            if max_dim > 64:
                factor = math.ceil(max_dim / 64)
                icon_source = icon_source.subsample(factor, factor)
            self._icon_image = icon_source
            self.iconphoto(True, self._icon_image)
        except tk.TclError:
            self._icon_image = None

        self.device_var = tk.StringVar(value="hackrf")
        self.loop_var = tk.BooleanVar(value=True)
        self.tx_sr_var = tk.StringVar()
        self.mod_sr_var = tk.StringVar()
        self.deviation_var = tk.StringVar()
        self.master_scale_var = tk.StringVar()
        self.ctcss_level_var = tk.StringVar()
        self.ctcss_deviation_var = tk.StringVar()
        self.tx_gain_var = tk.StringVar()
        self.gate_open_var = tk.StringVar()
        self.gate_close_var = tk.StringVar()
        self.gate_attack_var = tk.StringVar()
        self.gate_release_var = tk.StringVar()
        self._apply_transmitter_settings_to_vars(self._active_settings)

        self.channel_rows: List[ChannelRow] = []
        self._channel_sections: Dict[ChannelRow, CollapsibleSection] = {}
        self.tb: Optional[MultiNBFMTx] = None
        self.tb_thread: Optional[threading.Thread] = None
        self._run_error: Optional[Exception] = None
        self.running = False
        self._setting_states: Dict[str, Dict[str, str]] = {}
        self._setting_errors: Dict[str, str] = {}
        self._setting_error_sources: Dict[str, str] = {}
        self.settings_status_var = tk.StringVar(value="All transmitter settings look valid.")
        self.log_messages: List[str] = []
        self.session_path: Optional[Path] = None

        self._build_menu()
        self._build_layout()
        self.add_channel()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self._log("Application initialized.")
        self._log_environment_details("startup")

    def _compose_active_settings(self, **overrides: Optional[float]) -> Dict[str, Optional[float]]:
        settings = dict(DEFAULT_TRANSMITTER_SETTINGS)
        settings.update(self._persisted_settings)
        for key, value in overrides.items():
            if value is not None:
                self._cli_overrides[key] = value
                settings[key] = value
        self._active_settings = settings
        return settings

    def _apply_transmitter_settings_to_vars(
        self, settings: Dict[str, Optional[float]]
    ) -> None:
        self.tx_sr_var.set(_format_setting_value(settings["tx_sample_rate"]))
        self.mod_sr_var.set(_format_setting_value(settings["mod_sample_rate"]))
        self.deviation_var.set(_format_setting_value(settings["deviation_hz"]))
        self.master_scale_var.set(_format_setting_value(settings["master_scale"]))
        self.ctcss_level_var.set(_format_setting_value(settings["ctcss_level"]))
        self.ctcss_deviation_var.set(
            _format_setting_value(settings.get("ctcss_deviation"))
        )
        self.tx_gain_var.set(_format_setting_value(settings.get("tx_gain_override")))
        self.gate_open_var.set(_format_setting_value(settings["gate_open_threshold"]))
        self.gate_close_var.set(_format_setting_value(settings["gate_close_threshold"]))
        self.gate_attack_var.set(_format_setting_value(settings.get("gate_attack_ms")))
        self.gate_release_var.set(_format_setting_value(settings.get("gate_release_ms")))

    def _refresh_active_settings_from_persisted(self) -> None:
        active = dict(DEFAULT_TRANSMITTER_SETTINGS)
        active.update(self._persisted_settings)
        for key, value in self._cli_overrides.items():
            if value is not None:
                active[key] = value
        self._active_settings = active
        self._apply_transmitter_settings_to_vars(active)

    def _build_menu(self) -> None:
        menubar = tk.Menu(self)
        file_menu = tk.Menu(menubar, tearoff=False)
        file_menu.add_command(label="Save Session…", command=self.save_session)
        file_menu.add_command(label="Load Session…", command=self.load_session)
        file_menu.add_command(
            label="Export for HackRF…", command=self.export_hackrf_bundle
        )
        file_menu.add_separator()
        file_menu.add_command(label="Quit", command=self.on_close)
        menubar.add_cascade(label="File", menu=file_menu)

        presets_menu = tk.Menu(menubar, tearoff=False)
        presets_menu.add_command(label="Manage Presets…", command=self.open_preset_manager)
        presets_menu.add_command(label="Import Presets…", command=self.import_presets_from_file)
        presets_menu.add_command(label="Export Presets…", command=self.export_presets_to_file)
        menubar.add_cascade(label="Presets", menu=presets_menu)

        settings_menu = tk.Menu(menubar, tearoff=False)
        settings_menu.add_command(
            label="Manage Transmitter Settings…",
            command=self.open_transmitter_settings_manager,
        )
        menubar.add_cascade(label="Settings", menu=settings_menu)

        self.config(menu=menubar)

    def _build_layout(self) -> None:
        style = ttk.Style(self)
        style.configure("ChannelRow.TFrame", borderwidth=1, relief="groove")
        style.configure(
            "ChannelRowError.TFrame",
            borderwidth=2,
            relief="solid",
            background="#ffecec",
        )
        style.configure("Invalid.TEntry", fieldbackground="#ffecec")
        style.configure("Invalid.TSpinbox", fieldbackground="#ffecec")
        padding = dict(padx=10, pady=5)
        main = ttk.Frame(self)
        main.pack(fill="both", expand=True, padx=10, pady=10)
        main.columnconfigure(1, weight=1)
        main.columnconfigure(2, weight=1)

        ttk.Label(main, text="Device:").grid(row=0, column=0, sticky="w", **padding)
        device_combo = ttk.Combobox(
            main,
            textvariable=self.device_var,
            values=["hackrf", "pluto", "plutoplus", "plutoplussdr"],
            state="readonly",
        )
        device_combo.grid(row=0, column=1, sticky="we", **padding)

        ttk.Button(main, text="Manage Presets…", command=self.open_preset_manager).grid(
            row=0, column=2, sticky="e", **padding
        )

        ttk.Checkbutton(
            main,
            text="Loop queued audio",
            variable=self.loop_var,
        ).grid(row=1, column=0, columnspan=2, sticky="w", **padding)

        ttk.Separator(main).grid(row=2, column=0, columnspan=3, sticky="we", pady=(10, 5))

        channels_header = ttk.Frame(main)
        channels_header.grid(row=3, column=0, columnspan=3, sticky="we", **padding)
        ttk.Label(channels_header, text="Channels", font=("", 11, "bold")).pack(
            side="left"
        )
        toolbar = ttk.Frame(channels_header)
        toolbar.pack(side="right")
        ttk.Button(toolbar, text="Expand all", command=self.expand_all_channels).pack(
            side="right", padx=2
        )
        ttk.Button(toolbar, text="Collapse all", command=self.collapse_all_channels).pack(
            side="right", padx=2
        )

        self.channels_container = ttk.Frame(main)
        self.channels_container.grid(row=4, column=0, columnspan=3, sticky="nsew")
        main.rowconfigure(4, weight=1)

        add_btn = ttk.Button(main, text="Add Channel", command=self.add_channel)
        add_btn.grid(row=5, column=0, sticky="w", **padding)

        self.status_var = tk.StringVar(value="Idle")
        ttk.Label(main, textvariable=self.status_var).grid(
            row=5, column=1, sticky="e", **padding
        )

        session_controls = ttk.Frame(main)
        session_controls.grid(row=6, column=2, sticky="e", **padding)
        ttk.Button(session_controls, text="Save Session…", command=self.save_session).grid(
            row=0, column=0, padx=4
        )
        ttk.Button(session_controls, text="Load Session…", command=self.load_session).grid(
            row=0, column=1, padx=4
        )
        ttk.Button(
            session_controls,
            text="Export for HackRF…",
            command=self.export_hackrf_bundle,
        ).grid(row=0, column=2, padx=4)

        button_frame = ttk.Frame(main)
        button_frame.grid(row=7, column=0, columnspan=3, sticky="e", pady=(10, 0))
        self.start_button = ttk.Button(button_frame, text="Start", command=self.start_transmission)
        self.start_button.grid(row=0, column=0, padx=5)
        self.stop_button = ttk.Button(
            button_frame, text="Stop", command=self.stop_transmission, state="disabled"
        )
        self.stop_button.grid(row=0, column=1, padx=5)
        self.tx_progress = ttk.Progressbar(button_frame, mode="indeterminate", length=180)
        self.tx_progress.grid(row=0, column=2, padx=5)

        ttk.Separator(main).grid(row=8, column=0, columnspan=3, sticky="we", pady=(10, 5))

        log_section = ttk.LabelFrame(main, text="Transmission Log")
        log_section.grid(row=9, column=0, columnspan=3, sticky="nsew", **padding)
        log_section.columnconfigure(0, weight=1)
        log_section.rowconfigure(0, weight=1)
        self.log_text = tk.Text(log_section, height=10, wrap="word", state="disabled")
        log_scroll = ttk.Scrollbar(log_section, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        log_scroll.grid(row=0, column=1, sticky="ns")

        main.columnconfigure(1, weight=1)
        main.columnconfigure(0, weight=0)
        main.columnconfigure(2, weight=0)
        main.rowconfigure(5, weight=2)
        main.rowconfigure(9, weight=1)

    def add_channel(self, state: Optional[Dict[str, str]] = None) -> ChannelRow:
        section = CollapsibleSection(self.channels_container, title="")
        section.grid(row=len(self.channel_rows), column=0, sticky="we", pady=4)
        row = ChannelRow(section.content_frame, self.presets, controller=self)
        row.grid(row=0, column=0, sticky="we")
        self.channel_rows.append(row)
        self._channel_sections[row] = section
        self.channels_container.columnconfigure(0, weight=1)
        if state:
            row.apply_state(state)
        self._refresh_channel_positions()
        self._log(f"Added channel {len(self.channel_rows)}.")
        return row

    def remove_channel(self, row: ChannelRow) -> None:
        if row in self.channel_rows:
            if len(self.channel_rows) == 1:
                messagebox.showwarning("Cannot remove", "At least one channel is required")
                return
            self.channel_rows.remove(row)
            section = getattr(self, "_channel_sections", {}).pop(row, None)
            if section is not None:
                section.destroy()
            else:
                row.destroy()
            self._refresh_channel_positions()
            self._log("Removed a channel.")

    def duplicate_channel(self, row: ChannelRow) -> None:
        if row not in self.channel_rows:
            return
        state = row.serialize_state()
        new_row = self.add_channel(state=state)
        # Collapse duplicated section to reduce clutter
        section = self._channel_sections.get(new_row)
        if section:
            section.set_collapsed(False)
        self._log("Duplicated channel configuration.")

    def move_channel(self, row: ChannelRow, direction: int) -> None:
        if row not in self.channel_rows or direction not in (-1, 1):
            return
        idx = self.channel_rows.index(row)
        new_idx = idx + direction
        if not (0 <= new_idx < len(self.channel_rows)):
            return
        self.channel_rows[idx], self.channel_rows[new_idx] = (
            self.channel_rows[new_idx],
            self.channel_rows[idx],
        )
        self._refresh_channel_positions()
        self._log(f"Moved channel to position {new_idx + 1}.")

    def expand_all_channels(self) -> None:
        if not self._channel_sections:
            return
        for section in self._channel_sections.values():
            if section is not None:
                section.set_collapsed(False)
        self._log("Expanded all channel panels.")

    def collapse_all_channels(self) -> None:
        if not self._channel_sections:
            return
        for section in self._channel_sections.values():
            if section is not None:
                section.set_collapsed(True)
        self._log("Collapsed all channel panels.")

    def _refresh_channel_positions(self) -> None:
        for idx, channel in enumerate(self.channel_rows):
            section = getattr(self, "_channel_sections", {}).get(channel)
            if section is not None:
                section.grid_configure(row=idx)
        self._refresh_channel_indices()

    def _clear_all_channels(self) -> None:
        for row in list(self.channel_rows):
            section = self._channel_sections.pop(row, None)
            if section is not None:
                section.destroy()
            else:
                row.destroy()
        self.channel_rows.clear()
        self._channel_sections.clear()

    def _broadcast_preset_update(self) -> None:
        for row in self.channel_rows:
            row.update_presets(self.presets)

    def _refresh_channel_indices(self) -> None:
        for idx, channel in enumerate(self.channel_rows, start=1):
            channel.set_index(idx)
            section = getattr(self, "_channel_sections", {}).get(channel)
            if section is not None:
                section.set_title(f"Channel {idx}")

    def _clear_channel_errors(self) -> None:
        for row in self.channel_rows:
            row.clear_error()

    def _collect_channel_data(self):
        file_groups: List[List[Path]] = []
        freqs: List[float] = []
        gains: List[float] = []
        ctcss_tones: List[Optional[float]] = []
        dcs_codes: List[Optional[str]] = []

        for idx, row in enumerate(self.channel_rows, start=1):
            try:
                freq = row.get_frequency()
                files = row.get_playlist_paths()
                if not files:
                    raise ValueError("Add at least one audio file to the playlist")
                gain_str = row.gain_var.get().strip()
                gain = float(gain_str) if gain_str else 1.0
            except ValueError as exc:
                raise ChannelValidationError(idx, str(exc)) from exc

            try:
                tone = row.get_ctcss_tone()
                dcs = row.get_dcs_code()
            except ValueError as exc:
                raise ChannelValidationError(idx, str(exc)) from exc

            if tone is not None and dcs is not None:
                raise ChannelValidationError(
                    idx, "CTCSS and DCS cannot be enabled at the same time"
                )

            file_groups.append(list(files))
            freqs.append(freq)
            gains.append(gain)
            ctcss_tones.append(tone)
            dcs_codes.append(dcs)

        min_freq = min(freqs)
        max_freq = max(freqs)
        center_freq = (min_freq + max_freq) / 2.0
        frequency_offsets = [freq - center_freq for freq in freqs]

        return center_freq, file_groups, frequency_offsets, gains, ctcss_tones, dcs_codes

    def _register_numeric_validator(
        self,
        *,
        field_name: str,
        display_name: str,
        var: tk.StringVar,
        widget,
        allow_empty: bool = False,
        minimum: Optional[float] = None,
        maximum: Optional[float] = None,
    ) -> None:
        default_style = widget.cget("style") or widget.winfo_class()
        error_style = f"Invalid.{widget.winfo_class()}"
        self._setting_states[field_name] = {
            "widget": widget,
            "default_style": default_style,
            "error_style": error_style,
        }

        def _validate(*_ignored):
            text = var.get().strip()
            if not text:
                if allow_empty:
                    self._clear_setting_error(field_name)
                    return
                self._mark_setting_error(field_name, f"{display_name} is required.")
                return
            try:
                value = float(text)
            except ValueError:
                self._mark_setting_error(field_name, f"{display_name} must be numeric.")
                return
            if minimum is not None and value < minimum:
                self._mark_setting_error(
                    field_name,
                    f"{display_name} must be ≥ {minimum:g}.",
                )
                return
            if maximum is not None and value > maximum:
                self._mark_setting_error(
                    field_name,
                    f"{display_name} must be ≤ {maximum:g}.",
                )
                return
            self._clear_setting_error(field_name)

        var.trace_add("write", _validate)
        _validate()

    def _mark_setting_error(self, field_name: str, message: str, *, source: str = "base") -> None:
        state = self._setting_states.get(field_name)
        if state:
            state["widget"].configure(style=state["error_style"])
        self._setting_errors[field_name] = message
        self._setting_error_sources[field_name] = source
        self._update_settings_status()

    def open_preset_manager(self) -> None:
        dialog = PresetManagerDialog(self, self.presets)
        self.wait_window(dialog)
        if dialog.result is not None:
            self.presets = dialog.result
            self._broadcast_preset_update()
            self._log(f"Preset library updated ({len(self.presets)} entries).")

    def open_transmitter_settings_manager(self) -> None:
        dialog = TransmitterSettingsDialog(self, self._persisted_settings)
        if dialog.result is None:
            return
        updated_settings = dict(dialog.result)
        try:
            save_transmitter_settings(updated_settings, self._settings_path)
        except Exception as exc:  # pragma: no cover - UI feedback
            messagebox.showerror("Save failed", str(exc))
            return
        self._persisted_settings = updated_settings
        self._refresh_active_settings_from_persisted()
        self._log("Transmitter defaults updated.")

    def import_presets_from_file(self) -> None:
        filename = filedialog.askopenfilename(
            title="Import preset CSV",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not filename:
            return
        try:
            presets = load_presets_from_csv(Path(filename))
        except Exception as exc:
            messagebox.showerror("Import failed", str(exc))
            return
        self.presets = presets
        self._broadcast_preset_update()
        self._log(f"Loaded presets from {Path(filename).name}.")

    def export_presets_to_file(self) -> None:
        if not self.presets:
            messagebox.showinfo("No presets", "There are no presets to export.")
            return
        filename = filedialog.asksaveasfilename(
            title="Export preset CSV",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not filename:
            return
        try:
            save_presets_to_csv(self.presets, Path(filename))
        except Exception as exc:
            messagebox.showerror("Export failed", str(exc))
            return
        self._log(f"Exported presets to {Path(filename).name}.")

    def save_session(self) -> None:
        filename = filedialog.asksaveasfilename(
            title="Save session",
            defaultextension=".json",
            filetypes=[("Session files", "*.json"), ("All files", "*.*")],
        )
        if not filename:
            return
        data = self._serialize_session()
        try:
            atomic_write(Path(filename), json.dumps(data, indent=2))
        except OSError as exc:
            messagebox.showerror("Save failed", str(exc))
            return
        self.session_path = Path(filename)
        self._log(f"Saved session to {self.session_path.name}.")

    def load_session(self) -> None:
        filename = filedialog.askopenfilename(
            title="Load session",
            filetypes=[("Session files", "*.json"), ("All files", "*.*")],
        )
        if not filename:
            return
        try:
            with open(filename, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except OSError as exc:
            messagebox.showerror("Load failed", str(exc))
            return
        except json.JSONDecodeError as exc:
            messagebox.showerror("Load failed", f"Invalid JSON: {exc}")
            return
        try:
            self._apply_session(data)
        except Exception as exc:
            messagebox.showerror("Load failed", str(exc))
            return
        self.session_path = Path(filename)
        self._log(f"Loaded session from {self.session_path.name}.")

    def export_hackrf_bundle(self) -> None:
        self._clear_channel_errors()
        try:
            (
                center_freq,
                file_groups,
                offsets,
                gains,
                ctcss_tones,
                dcs_codes,
            ) = self._collect_channel_data()
        except ChannelValidationError as exc:
            if 0 < exc.channel_index <= len(self.channel_rows):
                self.channel_rows[exc.channel_index - 1].show_error(str(exc))
            self.status_var.set(f"Channel {exc.channel_index}: {exc}")
            self.bell()
            return
        except ValueError as exc:
            messagebox.showerror("Invalid configuration", str(exc))
            return

        try:
            (
                tx_sr,
                mod_sr,
                deviation,
                master_scale,
                ctcss_level,
                ctcss_deviation,
                tx_gain_override,
                gate_open,
                gate_close,
                gate_attack,
                gate_release,
            ) = self._parse_transmitter_settings()
        except ValueError as exc:
            messagebox.showerror("Invalid configuration", str(exc))
            return

        destination = filedialog.askdirectory(
            title="Choose export folder for HackRF SD card"
        )
        if not destination:
            return

        channels: List[HackRFExportChannel] = []
        for idx, (files, offset, gain, ctcss, dcs) in enumerate(
            zip(file_groups, offsets, gains, ctcss_tones, dcs_codes), start=1
        ):
            channels.append(
                HackRFExportChannel(
                    index=idx,
                    frequency_hz=center_freq + offset,
                    gain=gain,
                    playlist=files,
                    ctcss_hz=ctcss,
                    dcs_code=dcs,
                )
            )

        try:
            manifest_path = export_hackrf_package(
                Path(destination),
                channels,
                center_frequency_hz=center_freq,
                tx_sample_rate=tx_sr,
                mod_sample_rate=mod_sr,
                deviation_hz=deviation,
                master_scale=master_scale,
                loop_queue=self.loop_var.get(),
                ctcss_level=ctcss_level,
                ctcss_deviation=ctcss_deviation,
                gate_open_threshold=gate_open,
                gate_close_threshold=gate_close,
                gate_attack_ms=gate_attack,
                gate_release_ms=gate_release,
            )
        except Exception as exc:
            messagebox.showerror("Export failed", str(exc))
            return

        self._log(f"Exported HackRF package to {Path(destination).name}.")
        messagebox.showinfo(
            "Export complete",
            f"HackRF playlist and audio exported to {manifest_path.parent}.",
        )

    def _serialize_session(self) -> Dict[str, object]:
        return {
            "version": 1,
            "settings": self._gather_settings(),
            "channels": [row.serialize_state() for row in self.channel_rows],
            "presets": presets_to_rows(self.presets),
        }

    def _gather_settings(self) -> Dict[str, object]:
        return {
            "device": self.device_var.get(),
            "loop": bool(self.loop_var.get()),
            "tx_sr": self.tx_sr_var.get(),
            "mod_sr": self.mod_sr_var.get(),
            "deviation": self.deviation_var.get(),
            "master_scale": self.master_scale_var.get(),
            "ctcss_level": self.ctcss_level_var.get(),
            "ctcss_deviation": self.ctcss_deviation_var.get(),
            "tx_gain": self.tx_gain_var.get(),
            "gate_open": self.gate_open_var.get(),
            "gate_close": self.gate_close_var.get(),
            "gate_attack": self.gate_attack_var.get(),
            "gate_release": self.gate_release_var.get(),
        }

    def _apply_session(self, data: Dict[str, object]) -> None:
        presets_data = data.get("presets")
        if isinstance(presets_data, list):
            loaded_presets = rows_to_presets(presets_data)
            if loaded_presets:
                self.presets = loaded_presets
                self._broadcast_preset_update()
        channels = data.get("channels")
        if isinstance(channels, list) and channels:
            self._clear_all_channels()
            for channel_state in channels:
                if isinstance(channel_state, dict):
                    self.add_channel(channel_state)
        else:
            if not self.channel_rows:
                self.add_channel()
        settings = data.get("settings")
        if isinstance(settings, dict):
            self._apply_settings(settings)

    def _apply_settings(self, settings: Dict[str, object]) -> None:
        device = settings.get("device")
        if isinstance(device, str):
            self.device_var.set(device)
        loop_value = settings.get("loop")
        if loop_value is not None:
            if isinstance(loop_value, str):
                loop_bool = loop_value.lower() in {"1", "true", "yes", "on"}
            else:
                loop_bool = bool(loop_value)
            self.loop_var.set(loop_bool)
        for key, var in [
            ("tx_sr", self.tx_sr_var),
            ("mod_sr", self.mod_sr_var),
            ("deviation", self.deviation_var),
            ("master_scale", self.master_scale_var),
            ("ctcss_level", self.ctcss_level_var),
            ("ctcss_deviation", self.ctcss_deviation_var),
            ("tx_gain", self.tx_gain_var),
            ("gate_open", self.gate_open_var),
            ("gate_close", self.gate_close_var),
            ("gate_attack", self.gate_attack_var),
            ("gate_release", self.gate_release_var),
        ]:
            value = settings.get(key)
            if value is not None:
                var.set(str(value))

    def _clear_setting_error(self, field_name: str) -> None:
        state = self._setting_states.get(field_name)
        if state:
            state["widget"].configure(style=state["default_style"])
        self._setting_errors.pop(field_name, None)
        self._setting_error_sources.pop(field_name, None)
        self._update_settings_status()

    def _update_settings_status(self) -> None:
        if self._setting_errors:
            message = next(iter(self._setting_errors.values()))
            self.settings_status_var.set(message)
            if hasattr(self, "settings_status_label"):
                self.settings_status_label.config(foreground="#a40000")
        else:
            self.settings_status_var.set("All transmitter settings look valid.")
            if hasattr(self, "settings_status_label"):
                self.settings_status_label.config(foreground="#1f6f00")

    def _safe_float(self, text: str) -> Optional[float]:
        stripped = text.strip()
        if not stripped:
            return None
        try:
            return float(stripped)
        except ValueError:
            return None

    def _validate_gate_relationship(self, *_ignored) -> None:
        if self._setting_error_sources.get("Gate open threshold") == "base":
            return
        if self._setting_error_sources.get("Gate close threshold") == "base":
            return
        open_val = self._safe_float(self.gate_open_var.get())
        close_val = self._safe_float(self.gate_close_var.get())
        if open_val is None or close_val is None:
            return
        if close_val >= open_val:
            self._mark_setting_error(
                "Gate close threshold",
                "Gate close threshold must be lower than the open threshold.",
                source="cross",
            )
        else:
            self._clear_setting_error("Gate close threshold")

    def _parse_float_entry(
        self,
        var: tk.StringVar,
        field_name: str,
        *,
        positive: bool = False,
        optional: bool = False,
    ) -> Optional[float]:
        text = var.get().strip()
        if not text:
            if optional:
                return None
            raise ValueError(f"{field_name} is required")
        try:
            value = float(text)
        except ValueError as exc:
            raise ValueError(f"{field_name} must be a valid number") from exc
        if positive and value <= 0:
            raise ValueError(f"{field_name} must be positive")
        return value

    def _parse_transmitter_settings(self):
        tx_sr = self._parse_float_entry(self.tx_sr_var, "TX sample rate", positive=True)
        mod_sr = self._parse_float_entry(self.mod_sr_var, "Mod sample rate", positive=True)
        deviation = self._parse_float_entry(self.deviation_var, "FM deviation", positive=True)
        master_scale = self._parse_float_entry(
            self.master_scale_var, "Master scale", positive=True
        )
        ctcss_level = self._parse_float_entry(
            self.ctcss_level_var, "CTCSS level", positive=True
        )
        ctcss_deviation = self._parse_float_entry(
            self.ctcss_deviation_var,
            "CTCSS deviation",
            positive=True,
            optional=True,
        )
        tx_gain_override = self._parse_float_entry(
            self.tx_gain_var, "TX gain override", optional=True
        )
        gate_open = self._parse_float_entry(
            self.gate_open_var, "Gate open threshold", positive=True
        )
        gate_close = self._parse_float_entry(
            self.gate_close_var, "Gate close threshold", positive=True
        )
        if gate_close >= gate_open:
            raise ValueError("Gate close threshold must be lower than the open threshold")
        gate_attack = self._parse_float_entry(
            self.gate_attack_var, "Gate attack (ms)", optional=True
        )
        gate_release = self._parse_float_entry(
            self.gate_release_var, "Gate release (ms)", optional=True
        )
        if gate_attack is None:
            gate_attack = DEFAULT_GATE_ATTACK_MS
        if gate_attack < 0:
            raise ValueError("Gate attack must be non-negative")
        if gate_release is None:
            gate_release = DEFAULT_GATE_RELEASE_MS
        if gate_release < 0:
            raise ValueError("Gate release must be non-negative")
        return (
            tx_sr,
            mod_sr,
            deviation,
            master_scale,
            ctcss_level,
            ctcss_deviation,
            tx_gain_override,
            gate_open,
            gate_close,
            gate_attack,
            gate_release,
        )

    def start_transmission(self) -> None:
        if self.running:
            return
        self._clear_channel_errors()
        self.status_var.set("Validating configuration…")
        if self._setting_errors:
            first_error = next(iter(self._setting_errors.values()))
            self.status_var.set(first_error)
            self.bell()
            return
        try:
            (
                center_freq,
                file_groups,
                offsets,
                gains,
                ctcss_tones,
                dcs_codes,
            ) = self._collect_channel_data()
        except ChannelValidationError as exc:
            if 0 < exc.channel_index <= len(self.channel_rows):
                self.channel_rows[exc.channel_index - 1].show_error(str(exc))
            self.status_var.set(f"Channel {exc.channel_index}: {exc}")
            self.bell()
            return
        except ValueError as exc:
            self.status_var.set(str(exc))
            messagebox.showerror("Invalid configuration", str(exc))
            return

        try:
            (
                tx_sr,
                mod_sr,
                deviation,
                master_scale,
                ctcss_level,
                ctcss_deviation,
                tx_gain_override,
                gate_open,
                gate_close,
                gate_attack,
                gate_release,
            ) = self._parse_transmitter_settings()
        except ValueError as exc:
            self.status_var.set(str(exc))
            messagebox.showerror("Invalid configuration", str(exc))
            return

        audio_sr = None
        if tx_gain_override is not None:
            tx_gain = float(tx_gain_override)
        else:
            tx_gain = self.TX_GAIN_DEFAULTS.get(self.device_var.get(), 0.0)

        self.tb = MultiNBFMTx(
            device=self.device_var.get(),
            center_freq=center_freq,
            file_groups=file_groups,
            offsets=offsets,
            tx_sr=tx_sr,
            tx_gain=tx_gain,
            deviation=deviation,
            mod_sr=mod_sr,
            audio_sr=audio_sr,
            master_scale=master_scale,
            loop_queue=self.loop_var.get(),
            channel_gains=gains,
            ctcss_tones=ctcss_tones,
            ctcss_level=ctcss_level,
            ctcss_deviation=ctcss_deviation,
            dcs_codes=dcs_codes,
            gate_open_threshold=gate_open,
            gate_close_threshold=gate_close,
            gate_attack_ms=gate_attack,
            gate_release_ms=gate_release,
        )

        self.running = True
        self._run_error = None
        self.status_var.set(f"Transmitting @ {center_freq/1e6:.4f} MHz")
        self.start_button.config(state="disabled")
        self.stop_button.config(state="normal")
        self.tx_progress.start(60)
        self._log(f"Transmission started @ {center_freq/1e6:.4f} MHz.")
        self._log_environment_details(
            "transmission start",
            extra_details={
                "device": self.device_var.get(),
                "center_freq_hz": f"{center_freq}",
                "tx_sr": f"{tx_sr}",
                "mod_sr": f"{mod_sr}",
                "deviation_hz": f"{deviation}",
                "master_scale": f"{master_scale}",
            },
        )

        def _run():
            try:
                self.tb.start()
                self.tb.wait()
            except Exception as exc:  # pragma: no cover - UI feedback
                self._run_error = exc
            finally:
                self.after(0, self._on_transmission_complete)

        self.tb_thread = threading.Thread(target=_run, daemon=True)
        self.tb_thread.start()

    def stop_transmission(self) -> None:
        if not self.running or self.tb is None:
            return
        self.stop_button.config(state="disabled")
        self._log("Stop requested.")
        try:
            self.tb.stop()
        except Exception as exc:  # pragma: no cover - UI feedback
            self._run_error = exc
        self._await_thread_shutdown()

    def _await_thread_shutdown(self) -> None:
        thread = self.tb_thread
        if thread is None:
            return
        if thread.is_alive():
            self.after(100, self._await_thread_shutdown)
            return
        thread.join()
        self.tb_thread = None

    def _on_transmission_complete(self) -> None:
        self._await_thread_shutdown()
        self.running = False
        self.start_button.config(state="normal")
        self.stop_button.config(state="disabled")
        self.status_var.set("Idle")
        self.tx_progress.stop()
        if self._run_error is not None:
            messagebox.showerror("Transmission error", str(self._run_error))
            self._log(f"Transmission stopped with error: {self._run_error}")
            self._run_error = None
        else:
            self._log("Transmission finished.")
        self.tb = None

    def on_close(self) -> None:
        if self.running and self.tb is not None:
            if not messagebox.askyesno(
                "Quit", "Transmission is active. Do you want to stop and exit?"
            ):
                return
            self.stop_transmission()
            self.after(200, self._close_when_idle)
        else:
            self.destroy()

    def _close_when_idle(self) -> None:
        if self.running:
            self.after(200, self._close_when_idle)
        else:
            self.destroy()

    def _log_environment_details(
        self, context: str, *, extra_details: Optional[Dict[str, str]] = None
    ) -> None:
        data_path = resolve_data_file(APP_NAME, "channel_presets.csv")
        self._log(f"Environment details ({context}):")
        self._log(f"  Identity: {_get_user_identity()}")
        self._log(f"  CWD: {Path.cwd()}")
        self._log(f"  PATH: {os.environ.get('PATH', '')}")
        self._log(f"  Config path: {TRANSMITTER_SETTINGS_PATH}")
        self._log(f"  Data path: {data_path}")
        if extra_details:
            for key, value in extra_details.items():
                self._log(f"  {key}: {value}")

    def _log(self, message: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        entry = f"[{timestamp}] {message}"
        self.log_messages.append(entry)
        if hasattr(self, "log_text"):
            self.log_text.configure(state="normal")
            self.log_text.insert(tk.END, entry + "\n")
            self.log_text.see(tk.END)
            self.log_text.configure(state="disabled")


def main() -> None:
    args = parse_args()
    settings_path = resolve_config_file(
        APP_NAME,
        "transmitter_settings.json",
        cli_path=args.config,
    )
    presets_path = resolve_data_file(
        APP_NAME,
        "channel_presets.csv",
        cli_path=args.presets,
        base_dir=args.data_dir,
        bundle_path=DEFAULT_PRESETS_PATH,
    )
    app = MultiChannelApp(
        tx_sample_rate=args.tx_sr,
        mod_sample_rate=args.mod_sr,
        deviation_hz=args.deviation,
        master_scale=args.master_scale,
        ctcss_level=args.ctcss_level,
        ctcss_deviation=args.ctcss_deviation,
        tx_gain_override=args.tx_gain,
        gate_open_threshold=args.gate_open,
        gate_close_threshold=args.gate_close,
        gate_attack_ms=args.gate_attack_ms,
        gate_release_ms=args.gate_release_ms,
        settings_path=settings_path,
        presets_path=presets_path,
        data_dir=args.data_dir,
    )
    app.mainloop()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Tkinter front-end for the multi-channel NBFM transmitter."
            " Use these options to override the default transmitter settings"
            " exposed in the GUI."
        )
    )
    parser.add_argument(
        "--tx-sr",
        type=float,
        default=None,
        help="Override the saved TX sample rate (sps). Uses transmitter_settings.json by default.",
    )
    parser.add_argument(
        "--mod-sr",
        type=float,
        default=None,
        help="Override the saved per-channel modulation sample rate (sps).",
    )
    parser.add_argument(
        "--deviation",
        type=float,
        default=None,
        help="Override the saved per-channel FM deviation (Hz).",
    )
    parser.add_argument(
        "--master-scale",
        type=float,
        default=None,
        help="Override the saved master amplitude scale applied to the summed waveform.",
    )
    parser.add_argument(
        "--ctcss-level",
        type=float,
        default=None,
        help="Override the saved CTCSS amplitude used when a channel enables tone transmit.",
    )
    parser.add_argument(
        "--ctcss-deviation",
        type=float,
        default=None,
        help="Default CTCSS deviation target (Hz). Overrides the level when set.",
    )
    parser.add_argument(
        "--tx-gain",
        type=float,
        default=None,
        help="Override the saved TX gain override (dB). Leave unset to use the saved value.",
    )
    parser.add_argument(
        "--gate-open",
        type=float,
        default=None,
        help="Override the saved gate open threshold (absolute amplitude).",
    )
    parser.add_argument(
        "--gate-close",
        type=float,
        default=None,
        help="Override the saved gate close threshold (absolute amplitude).",
    )
    parser.add_argument(
        "--gate-attack-ms",
        type=float,
        default=None,
        help="Override the saved gate attack in milliseconds.",
    )
    parser.add_argument(
        "--gate-release-ms",
        type=float,
        default=None,
        help="Override the saved gate release in milliseconds.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Override the transmitter settings JSON path.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Override the data directory used for presets.",
    )
    parser.add_argument(
        "--presets",
        type=Path,
        default=None,
        help="Override the channel presets CSV path.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
