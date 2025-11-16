#!/usr/bin/env python3
"""Lightweight GUI wrapper for the multi-channel NBFM transmitter."""

import argparse
import csv
import threading
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Dict, List, Optional

from multich_nbfm_tx import (
    DEFAULT_GATE_ATTACK_MS,
    DEFAULT_GATE_CLOSE_THRESHOLD,
    DEFAULT_GATE_OPEN_THRESHOLD,
    DEFAULT_GATE_RELEASE_MS,
    MultiNBFMTx,
)


DEFAULT_TX_SAMPLE_RATE = 8_000_000
DEFAULT_MOD_SAMPLE_RATE = 250_000
DEFAULT_DEVIATION_HZ = 3_000
DEFAULT_MASTER_SCALE = 0.6
DEFAULT_CTCSS_LEVEL = 0.20
DEFAULT_TX_GAIN_OVERRIDE = 10.0


@dataclass(frozen=True)
class ChannelPreset:
    """Represents a selectable preset channel."""

    key: str
    label: str
    frequency_hz: float
    ctcss_hz: Optional[float] = None
    dcs_code: Optional[str] = None


def load_channel_presets() -> List[ChannelPreset]:
    """Load channel presets from the packaged CSV file."""

    presets_path = Path(__file__).with_name("channel_presets.csv")
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


class ChannelRow(ttk.Frame):
    """Widget that captures per-channel configuration."""

    def __init__(self, master, presets: List[ChannelPreset], remove_callback):
        super().__init__(master, style="ChannelRow.TFrame", padding=8)
        self.remove_callback = remove_callback
        self.preset_var = tk.StringVar()
        self.gain_var = tk.StringVar(value="1.0")
        self.files: List[Path] = []
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
        self.file_listbox = tk.Listbox(
            playlist_frame,
            height=5,
            width=45,
            exportselection=False,
            selectmode=tk.BROWSE,
        )
        self.file_listbox.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(playlist_frame, orient="vertical", command=self.file_listbox.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.file_listbox.configure(yscrollcommand=scrollbar.set)
        playlist_frame.columnconfigure(0, weight=1)

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

        remove_btn = ttk.Button(self, text="Remove Channel", command=self.remove)
        remove_btn.grid(row=8, column=2, padx=4, pady=4, sticky="e")

        self.error_var = tk.StringVar(value="")
        self.error_label = ttk.Label(self, textvariable=self.error_var, foreground="#a40000")
        self.error_label.grid(row=8, column=0, columnspan=2, padx=4, pady=2, sticky="w")

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
        filenames = filedialog.askopenfilenames(
            title="Select audio files",
            filetypes=[
                ("Audio files", "*.wav *.mp3"),
                ("WAV files", "*.wav"),
                ("MP3 files", "*.mp3"),
                ("All files", "*.*"),
            ],
        )
        if filenames:
            self.files.extend(Path(name).expanduser() for name in filenames)
            self._refresh_playlist()

    def remove_selected_files(self) -> None:
        self.clear_error()
        selections = sorted(self.file_listbox.curselection(), reverse=True)
        for idx in selections:
            if 0 <= idx < len(self.files):
                self.files.pop(idx)
        self._refresh_playlist()

    def move_selected(self, direction: int) -> None:
        if direction not in (-1, 1):
            return
        self.clear_error()
        selection = self.file_listbox.curselection()
        if not selection:
            return
        index = selection[0]
        new_index = index + direction
        if not (0 <= new_index < len(self.files)):
            return
        self.files[index], self.files[new_index] = self.files[new_index], self.files[index]
        self._refresh_playlist()
        self.file_listbox.selection_set(new_index)

    def _refresh_playlist(self) -> None:
        self.file_listbox.delete(0, tk.END)
        for path in self.files:
            self.file_listbox.insert(tk.END, path.name)

    def remove(self) -> None:
        self.remove_callback(self)

    def _on_preset_changed(self, _event=None) -> None:
        self.clear_error()
        self._ctcss_user_override = False
        self._dcs_user_override = False
        self.ctcss_custom_var.set("")
        self.dcs_custom_var.set("")
        self._update_tone_controls()

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

        self._refresh_tone_status()

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
        tx_sample_rate: float = DEFAULT_TX_SAMPLE_RATE,
        mod_sample_rate: float = DEFAULT_MOD_SAMPLE_RATE,
        deviation_hz: float = DEFAULT_DEVIATION_HZ,
        master_scale: float = DEFAULT_MASTER_SCALE,
        ctcss_level: float = DEFAULT_CTCSS_LEVEL,
        ctcss_deviation: Optional[float] = None,
        tx_gain_override: Optional[float] = DEFAULT_TX_GAIN_OVERRIDE,
        gate_open_threshold: float = DEFAULT_GATE_OPEN_THRESHOLD,
        gate_close_threshold: float = DEFAULT_GATE_CLOSE_THRESHOLD,
        gate_attack_ms: float = DEFAULT_GATE_ATTACK_MS,
        gate_release_ms: float = DEFAULT_GATE_RELEASE_MS,
    ):
        super().__init__()

        self.title("Multi-channel NBFM TX")
        self.resizable(True, True)

        try:
            self.presets = load_channel_presets()
        except Exception as exc:  # pragma: no cover - UI feedback
            messagebox.showerror("Preset load failure", str(exc))
            raise

        self.device_var = tk.StringVar(value="hackrf")
        self.loop_var = tk.BooleanVar(value=True)
        self.tx_sr_var = tk.StringVar(value=f"{float(tx_sample_rate)}")
        self.mod_sr_var = tk.StringVar(value=f"{float(mod_sample_rate)}")
        self.deviation_var = tk.StringVar(value=f"{float(deviation_hz)}")
        self.master_scale_var = tk.StringVar(value=f"{float(master_scale)}")
        self.ctcss_level_var = tk.StringVar(value=f"{float(ctcss_level)}")
        self.ctcss_deviation_var = tk.StringVar(
            value="" if ctcss_deviation is None else f"{float(ctcss_deviation)}"
        )
        self.tx_gain_var = tk.StringVar(
            value="" if tx_gain_override is None else f"{float(tx_gain_override)}"
        )
        self.gate_open_var = tk.StringVar(value=f"{float(gate_open_threshold)}")
        self.gate_close_var = tk.StringVar(value=f"{float(gate_close_threshold)}")
        self.gate_attack_var = tk.StringVar(value=f"{float(gate_attack_ms)}")
        self.gate_release_var = tk.StringVar(value=f"{float(gate_release_ms)}")

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

        self._build_layout()
        self.add_channel()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

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

        ttk.Label(main, text="Device:").grid(row=0, column=0, sticky="w", **padding)
        device_combo = ttk.Combobox(
            main,
            textvariable=self.device_var,
            values=["hackrf", "pluto", "plutoplus", "plutoplussdr"],
            state="readonly",
        )
        device_combo.grid(row=0, column=1, sticky="we", **padding)

        ttk.Checkbutton(
            main,
            text="Loop queued audio",
            variable=self.loop_var,
        ).grid(row=1, column=0, columnspan=2, sticky="w", **padding)

        settings_section = CollapsibleSection(main, title="Transmitter Settings")
        settings_section.grid(row=2, column=0, columnspan=3, sticky="we", **padding)
        settings = settings_section.content_frame
        subpad = dict(padx=4, pady=2)
        settings.columnconfigure(2, weight=1)
        settings_fields = [
            dict(
                name="TX sample rate",
                label="TX Sample Rate (sps):",
                var=self.tx_sr_var,
                min=1_000_000,
                max=20_000_000,
                step=100_000,
                help="1–20 Msps keeps HackRF happy.",
            ),
            dict(
                name="Mod sample rate",
                label="Mod Sample Rate (sps):",
                var=self.mod_sr_var,
                min=10_000,
                max=1_000_000,
                step=5_000,
                help="48–250 kS/s per channel works well.",
            ),
            dict(
                name="FM deviation",
                label="FM Deviation (Hz):",
                var=self.deviation_var,
                min=100,
                max=75_000,
                step=100,
                help="NBFM is usually ±3 kHz.",
            ),
            dict(
                name="Master scale",
                label="Master Scale:",
                var=self.master_scale_var,
                min=0.1,
                max=2.0,
                step=0.05,
                help="Scales the summed waveform before transmit.",
            ),
            dict(
                name="CTCSS level",
                label="CTCSS Level (amplitude):",
                var=self.ctcss_level_var,
                min=0.01,
                max=1.0,
                step=0.01,
                help="0.05–0.3 keeps tones audible without clipping.",
            ),
            dict(
                name="CTCSS deviation",
                label="CTCSS Deviation (Hz):",
                var=self.ctcss_deviation_var,
                min=10,
                max=1_000,
                allow_empty=True,
                help="Optional: overrides amplitude scaling when set.",
                widget="entry",
            ),
            dict(
                name="TX gain override",
                label="TX Gain Override (dB):",
                var=self.tx_gain_var,
                min=-50,
                max=70,
                allow_empty=True,
                help="Leave blank to rely on the device default.",
                widget="entry",
            ),
            dict(
                name="Gate open threshold",
                label="Gate Open Threshold:",
                var=self.gate_open_var,
                min=0.0,
                max=0.5,
                step=0.001,
                help="Signal level that starts the tone gate.",
            ),
            dict(
                name="Gate close threshold",
                label="Gate Close Threshold:",
                var=self.gate_close_var,
                min=0.0,
                max=0.5,
                step=0.001,
                help="Must stay below the open threshold to prevent chatter.",
            ),
            dict(
                name="Gate attack",
                label="Gate Attack (ms):",
                var=self.gate_attack_var,
                min=0.0,
                max=1_000.0,
                allow_empty=True,
                help="Blank uses the default 4 ms fade-in.",
                widget="entry",
            ),
            dict(
                name="Gate release",
                label="Gate Release (ms):",
                var=self.gate_release_var,
                min=0.0,
                max=5_000.0,
                allow_empty=True,
                help="Blank uses the default 200 ms tail.",
                widget="entry",
            ),
        ]

        for idx, field in enumerate(settings_fields):
            ttk.Label(settings, text=field["label"]).grid(
                row=idx, column=0, sticky="w", **subpad
            )
            if field.get("widget", "spinbox") == "spinbox":
                widget = ttk.Spinbox(
                    settings,
                    textvariable=field["var"],
                    from_=field.get("min", 0.0),
                    to=field.get("max", 0.0),
                    increment=field.get("step", 1.0),
                    width=18,
                )
            else:
                widget = ttk.Entry(settings, textvariable=field["var"], width=18)
            widget.grid(row=idx, column=1, sticky="we", **subpad)
            helper = field.get("help")
            if helper:
                ttk.Label(settings, text=helper, font=("", 9)).grid(
                    row=idx, column=2, sticky="w", **subpad
                )
            self._register_numeric_validator(
                field_name=field["name"],
                display_name=field.get("display_name", field["label"].rstrip(":")),
                var=field["var"],
                widget=widget,
                allow_empty=field.get("allow_empty", False),
                minimum=field.get("min"),
                maximum=field.get("max"),
            )

        self.settings_status_label = ttk.Label(
            settings,
            textvariable=self.settings_status_var,
            font=("", 9, "italic"),
            foreground="#1f6f00",
        )
        self.settings_status_label.grid(
            row=len(settings_fields),
            column=0,
            columnspan=3,
            sticky="w",
            **subpad,
        )

        self.gate_open_var.trace_add("write", self._validate_gate_relationship)
        self.gate_close_var.trace_add("write", self._validate_gate_relationship)
        self._validate_gate_relationship()

        ttk.Separator(main).grid(row=3, column=0, columnspan=3, sticky="we", pady=(10, 5))

        self.channels_container = ttk.Frame(main)
        self.channels_container.grid(row=4, column=0, columnspan=3, sticky="nsew")

        add_btn = ttk.Button(main, text="Add Channel", command=self.add_channel)
        add_btn.grid(row=5, column=0, sticky="w", **padding)

        self.status_var = tk.StringVar(value="Idle")
        ttk.Label(main, textvariable=self.status_var).grid(
            row=5, column=1, sticky="e", **padding
        )

        button_frame = ttk.Frame(main)
        button_frame.grid(row=6, column=0, columnspan=3, sticky="e", pady=(10, 0))
        self.start_button = ttk.Button(button_frame, text="Start", command=self.start_transmission)
        self.start_button.grid(row=0, column=0, padx=5)
        self.stop_button = ttk.Button(
            button_frame, text="Stop", command=self.stop_transmission, state="disabled"
        )
        self.stop_button.grid(row=0, column=1, padx=5)

        main.columnconfigure(1, weight=1)
        main.rowconfigure(4, weight=1)

    def add_channel(self) -> None:
        section = CollapsibleSection(self.channels_container, title="")
        section.grid(row=len(self.channel_rows), column=0, sticky="we", pady=4)
        row = ChannelRow(section.content_frame, self.presets, self.remove_channel)
        row.grid(row=0, column=0, sticky="we")
        self.channel_rows.append(row)
        self._channel_sections[row] = section
        self.channels_container.columnconfigure(0, weight=1)
        self._refresh_channel_indices()

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
            self._refresh_channel_indices()

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
                if not row.files:
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

            file_groups.append(list(row.files))
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
        if self._run_error is not None:
            messagebox.showerror("Transmission error", str(self._run_error))
            self._run_error = None
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


def main() -> None:
    args = parse_args()
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
    parser.add_argument("--tx-sr", type=float, default=DEFAULT_TX_SAMPLE_RATE, help="Default TX sample rate (sps)")
    parser.add_argument(
        "--mod-sr",
        type=float,
        default=DEFAULT_MOD_SAMPLE_RATE,
        help="Default per-channel modulation sample rate (sps)",
    )
    parser.add_argument(
        "--deviation",
        type=float,
        default=DEFAULT_DEVIATION_HZ,
        help="Default per-channel FM deviation (Hz)",
    )
    parser.add_argument(
        "--master-scale",
        type=float,
        default=DEFAULT_MASTER_SCALE,
        help="Default master amplitude scale applied to the summed waveform",
    )
    parser.add_argument(
        "--ctcss-level",
        type=float,
        default=DEFAULT_CTCSS_LEVEL,
        help="Default CTCSS amplitude used when a channel enables tone transmit",
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
        default=DEFAULT_TX_GAIN_OVERRIDE,
        help="Optional default TX gain override (dB) applied regardless of device",
    )
    parser.add_argument(
        "--gate-open",
        type=float,
        default=DEFAULT_GATE_OPEN_THRESHOLD,
        help="Default gate open threshold (absolute amplitude)",
    )
    parser.add_argument(
        "--gate-close",
        type=float,
        default=DEFAULT_GATE_CLOSE_THRESHOLD,
        help="Default gate close threshold (absolute amplitude)",
    )
    parser.add_argument(
        "--gate-attack-ms",
        type=float,
        default=DEFAULT_GATE_ATTACK_MS,
        help="Default gate attack in milliseconds",
    )
    parser.add_argument(
        "--gate-release-ms",
        type=float,
        default=DEFAULT_GATE_RELEASE_MS,
        help="Default gate release in milliseconds",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
