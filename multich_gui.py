#!/usr/bin/env python3
"""Lightweight GUI wrapper for the multi-channel NBFM transmitter."""

import csv
import threading
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Dict, List, Optional

from multich_nbfm_tx import MultiNBFMTx


@dataclass(frozen=True)
class ChannelPreset:
    """Represents a selectable preset channel."""

    key: str
    label: str
    frequency_hz: float


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
            except (KeyError, ValueError) as exc:
                raise ValueError(
                    f"Invalid row in {presets_path}: {row!r}"  # pragma: no cover - configuration issue
                ) from exc

            if label is None or frequency is None:
                raise ValueError(
                    f"Incomplete preset definition in {presets_path}: {row!r}"  # pragma: no cover - configuration issue
                )

            key = row.get("channel_id", label)
            presets.append(ChannelPreset(key=str(key), label=str(label), frequency_hz=frequency))

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

        self.files_label = ttk.Label(self, text="No files selected", width=40)
        self.files_label.grid(row=3, column=0, columnspan=2, padx=4, pady=2, sticky="we")

        select_btn = ttk.Button(self, text="Choose Files", command=self.select_files)
        select_btn.grid(row=1, column=2, padx=4, pady=2)

        remove_btn = ttk.Button(self, text="Remove", command=self.remove)
        remove_btn.grid(row=3, column=2, padx=4, pady=2)

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


class MultiChannelApp(tk.Tk):
    TX_SAMPLE_RATE = 8_000_000
    MOD_SAMPLE_RATE = 250_000
    DEVIATION_HZ = 3_000
    MASTER_SCALE = 0.8
    TX_GAIN_DEFAULTS = {
        "hackrf": 0.0,
        "pluto": -10.0,
        "plutoplus": -10.0,
        "plutoplussdr": -10.0,
    }

    def __init__(self):
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

        ttk.Label(
            main,
            text=(
                f"TX Sample Rate: {self.TX_SAMPLE_RATE:,} sps\n"
                f"Mod Sample Rate: {self.MOD_SAMPLE_RATE:,} sps\n"
                f"Deviation: {self.DEVIATION_HZ:,} Hz\n"
                f"Master Scale: {self.MASTER_SCALE:.2f}"
            ),
            justify="left",
        ).grid(row=2, column=0, columnspan=2, sticky="w", **padding)

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

        min_freq = min(freqs)
        max_freq = max(freqs)
        center_freq = (min_freq + max_freq) / 2.0
        frequency_offsets = [freq - center_freq for freq in freqs]

        return center_freq, file_groups, frequency_offsets, gains

    def start_transmission(self) -> None:
        if self.running:
            return
        try:
            center_freq, file_groups, offsets, gains = self._collect_channel_data()
        except ValueError as exc:
            messagebox.showerror("Invalid configuration", str(exc))
            return

        tx_sr = float(self.TX_SAMPLE_RATE)
        mod_sr = float(self.MOD_SAMPLE_RATE)
        deviation = float(self.DEVIATION_HZ)
        master_scale = float(self.MASTER_SCALE)
        audio_sr = None
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
    app = MultiChannelApp()
    app.mainloop()


if __name__ == "__main__":
    main()
