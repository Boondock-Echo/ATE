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
        super().__init__(master)
        self.remove_callback = remove_callback
        self.preset_var = tk.StringVar()
        self.gain_var = tk.StringVar(value="1.0")
        self.files: List[Path] = []
        self._labels = [preset.label for preset in presets]
        self._preset_map: Dict[str, ChannelPreset] = {
            preset.label: preset for preset in presets
        }

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

        self.ctcss_var = tk.BooleanVar(value=False)
        self.dcs_var = tk.BooleanVar(value=False)
        self._ctcss_value: Optional[float] = None
        self._dcs_value: Optional[str] = None

        ttk.Label(self, text="CTCSS Tone:").grid(
            row=3, column=0, padx=4, pady=2, sticky="w"
        )
        self.ctcss_check = ttk.Checkbutton(
            self,
            text="Enable",
            variable=self.ctcss_var,
            command=self._on_ctcss_toggle,
        )
        self.ctcss_check.grid(row=3, column=1, padx=4, pady=2, sticky="w")
        self.ctcss_info = ttk.Label(self, text="Not available")
        self.ctcss_info.grid(row=3, column=2, padx=4, pady=2, sticky="w")

        ttk.Label(self, text="DCS Code:").grid(
            row=4, column=0, padx=4, pady=2, sticky="w"
        )
        self.dcs_check = ttk.Checkbutton(
            self,
            text="Enable",
            variable=self.dcs_var,
            command=self._on_dcs_toggle,
        )
        self.dcs_check.grid(row=4, column=1, padx=4, pady=2, sticky="w")
        self.dcs_info = ttk.Label(self, text="Not available")
        self.dcs_info.grid(row=4, column=2, padx=4, pady=2, sticky="w")

        self.files_label = ttk.Label(self, text="No files selected", width=40)
        self.files_label.grid(row=5, column=0, columnspan=2, padx=4, pady=2, sticky="we")

        select_btn = ttk.Button(self, text="Choose Files", command=self.select_files)
        select_btn.grid(row=1, column=2, padx=4, pady=2)

        remove_btn = ttk.Button(self, text="Remove", command=self.remove)
        remove_btn.grid(row=5, column=2, padx=4, pady=2)

        self.channel_combo.bind("<<ComboboxSelected>>", self._on_preset_changed)
        self._update_tone_controls()

        self.columnconfigure(1, weight=1)

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
        if self.ctcss_var.get() and self._ctcss_value is not None:
            return float(self._ctcss_value)
        return None

    def get_dcs_code(self) -> Optional[str]:
        if self.dcs_var.get() and self._dcs_value:
            return str(self._dcs_value)
        return None

    def select_files(self) -> None:
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
            self.files = [Path(name).expanduser() for name in filenames]
            if len(self.files) == 1:
                display = self.files[0].name
            else:
                display = f"{len(self.files)} files selected"
            self.files_label.config(text=display)

    def remove(self) -> None:
        self.remove_callback(self)

    def _on_preset_changed(self, _event=None) -> None:
        self._update_tone_controls()

    def _update_tone_controls(self) -> None:
        label = self.preset_var.get().strip()
        preset = self._preset_map.get(label)
        self._ctcss_value = preset.ctcss_hz if preset else None
        self._dcs_value = preset.dcs_code if preset else None

        if self._ctcss_value is not None:
            self.ctcss_check.state(["!disabled"])
        else:
            self.ctcss_var.set(False)
            self.ctcss_check.state(["disabled"])

        if self._dcs_value is not None:
            self.dcs_check.state(["!disabled"])
        else:
            self.dcs_var.set(False)
            self.dcs_check.state(["disabled"])

        self._refresh_tone_status()

    def _refresh_tone_status(self) -> None:
        if self._ctcss_value is not None:
            status = "Enabled" if self.ctcss_var.get() else "Available"
            self.ctcss_info.config(text=f"{self._ctcss_value:.1f} Hz ({status})")
        else:
            self.ctcss_info.config(text="Not available")

        if self._dcs_value is not None:
            status = "Enabled" if self.dcs_var.get() else "Available"
            self.dcs_info.config(text=f"Code {self._dcs_value} ({status})")
        else:
            self.dcs_info.config(text="Not available")

    def _on_ctcss_toggle(self) -> None:
        if self._ctcss_value is None:
            self.ctcss_var.set(False)
            return
        if self.ctcss_var.get() and self.dcs_var.get():
            self.dcs_var.set(False)
        self._refresh_tone_status()

    def _on_dcs_toggle(self) -> None:
        if self._dcs_value is None:
            self.dcs_var.set(False)
            return
        if self.dcs_var.get() and self.ctcss_var.get():
            self.ctcss_var.set(False)
        self._refresh_tone_status()


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
        self.tb: Optional[MultiNBFMTx] = None
        self.tb_thread: Optional[threading.Thread] = None
        self._run_error: Optional[Exception] = None
        self.running = False

        self._build_layout()
        self.add_channel()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_layout(self) -> None:
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

        settings = ttk.LabelFrame(main, text="Transmitter Settings")
        settings.grid(row=2, column=0, columnspan=3, sticky="we", **padding)
        subpad = dict(padx=4, pady=2)
        entries = [
            ("TX Sample Rate (sps):", self.tx_sr_var),
            ("Mod Sample Rate (sps):", self.mod_sr_var),
            ("FM Deviation (Hz):", self.deviation_var),
            ("Master Scale:", self.master_scale_var),
            ("CTCSS Level (amplitude):", self.ctcss_level_var),
            ("CTCSS Deviation (Hz):", self.ctcss_deviation_var),
            ("TX Gain Override (dB):", self.tx_gain_var),
            ("Gate Open Threshold:", self.gate_open_var),
            ("Gate Close Threshold:", self.gate_close_var),
            ("Gate Attack (ms):", self.gate_attack_var),
            ("Gate Release (ms):", self.gate_release_var),
        ]
        for idx, (label, var) in enumerate(entries):
            ttk.Label(settings, text=label).grid(row=idx, column=0, sticky="w", **subpad)
            ttk.Entry(settings, textvariable=var, width=18).grid(
                row=idx, column=1, sticky="we", **subpad
            )
        ttk.Label(
            settings,
            text="Leave CTCSS deviation blank to rely on amplitude scaling.",
            font=("", 9),
        ).grid(row=len(entries), column=0, columnspan=2, sticky="w", **subpad)
        ttk.Label(
            settings,
            text=(
                "Gate tip: open≈0.015, close≈0.006, attack≈4 ms, release≈200 ms "
                "keeps tones muted between tracks."
            ),
            font=("", 9),
        ).grid(row=len(entries) + 1, column=0, columnspan=2, sticky="w", **subpad)
        settings.columnconfigure(1, weight=1)

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
        row = ChannelRow(self.channels_container, self.presets, self.remove_channel)
        self.channel_rows.append(row)
        row.grid(row=len(self.channel_rows) - 1, column=0, sticky="we", pady=4)
        row.set_index(len(self.channel_rows))
        self.channels_container.columnconfigure(0, weight=1)

    def remove_channel(self, row: ChannelRow) -> None:
        if row in self.channel_rows:
            if len(self.channel_rows) == 1:
                messagebox.showwarning("Cannot remove", "At least one channel is required")
                return
            self.channel_rows.remove(row)
            row.destroy()
            for idx, channel in enumerate(self.channel_rows, start=1):
                channel.set_index(idx)

    def _collect_channel_data(self):
        file_groups: List[List[Path]] = []
        freqs: List[float] = []
        gains: List[float] = []
        ctcss_tones: List[Optional[float]] = []
        dcs_codes: List[Optional[str]] = []

        for idx, row in enumerate(self.channel_rows, start=1):
            freq = row.get_frequency()
            if not row.files:
                raise ValueError("Each channel must have at least one audio file selected")
            gain_str = row.gain_var.get().strip()
            try:
                gain = float(gain_str) if gain_str else 1.0
            except ValueError as exc:
                raise ValueError(f"Invalid gain for channel {idx}") from exc
            file_groups.append(row.files)
            freqs.append(freq)
            gains.append(gain)
            ctcss_tones.append(row.get_ctcss_tone())
            dcs_codes.append(row.get_dcs_code())
            if ctcss_tones[-1] is not None and dcs_codes[-1] is not None:
                raise ValueError(
                    f"Channel {idx} cannot enable both CTCSS and DCS simultaneously"
                )

        min_freq = min(freqs)
        max_freq = max(freqs)
        center_freq = (min_freq + max_freq) / 2.0
        frequency_offsets = [freq - center_freq for freq in freqs]

        return center_freq, file_groups, frequency_offsets, gains, ctcss_tones, dcs_codes

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
        try:
            (
                center_freq,
                file_groups,
                offsets,
                gains,
                ctcss_tones,
                dcs_codes,
            ) = self._collect_channel_data()
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
