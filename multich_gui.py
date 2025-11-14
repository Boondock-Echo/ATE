#!/usr/bin/env python3
"""Lightweight GUI wrapper for the multi-channel NBFM transmitter."""

import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import List, Optional

from multich_nbfm_tx import MultiNBFMTx


class ChannelRow(ttk.Frame):
    """Widget that captures per-channel configuration."""

    def __init__(self, master, remove_callback):
        super().__init__(master)
        self.remove_callback = remove_callback
        self.freq_var = tk.StringVar()
        self.gain_var = tk.StringVar(value="1.0")
        self.files: List[Path] = []

        self.header = ttk.Label(self, text="Channel")
        self.header.grid(row=0, column=0, padx=4, pady=2, sticky="w")

        ttk.Label(self, text="Frequency (Hz):").grid(
            row=1, column=0, padx=4, pady=2, sticky="w"
        )
        self.freq_entry = ttk.Entry(self, textvariable=self.freq_var, width=20)
        self.freq_entry.grid(row=1, column=1, padx=4, pady=2, sticky="we")

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
    def __init__(self):
        super().__init__()

        self.title("Multi-channel NBFM TX")
        self.resizable(True, True)

        self.device_var = tk.StringVar(value="hackrf")
        self.center_freq_var = tk.StringVar(value="462600000")
        self.tx_sr_var = tk.StringVar(value="8000000")
        self.tx_gain_var = tk.StringVar(value="0")
        self.deviation_var = tk.StringVar(value="3000")
        self.mod_sr_var = tk.StringVar(value="250000")
        self.audio_sr_var = tk.StringVar()
        self.master_scale_var = tk.StringVar(value="0.8")
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

        ttk.Label(main, text="Center Frequency (Hz):").grid(
            row=1, column=0, sticky="w", **padding
        )
        ttk.Entry(main, textvariable=self.center_freq_var).grid(
            row=1, column=1, sticky="we", **padding
        )

        ttk.Label(main, text="TX Sample Rate (sps):").grid(
            row=2, column=0, sticky="w", **padding
        )
        ttk.Entry(main, textvariable=self.tx_sr_var).grid(
            row=2, column=1, sticky="we", **padding
        )

        ttk.Label(main, text="TX Gain:").grid(row=3, column=0, sticky="w", **padding)
        ttk.Entry(main, textvariable=self.tx_gain_var).grid(
            row=3, column=1, sticky="we", **padding
        )

        ttk.Label(main, text="Deviation (Hz):").grid(row=4, column=0, sticky="w", **padding)
        ttk.Entry(main, textvariable=self.deviation_var).grid(
            row=4, column=1, sticky="we", **padding
        )

        ttk.Label(main, text="Mod Sample Rate (sps):").grid(
            row=5, column=0, sticky="w", **padding
        )
        ttk.Entry(main, textvariable=self.mod_sr_var).grid(
            row=5, column=1, sticky="we", **padding
        )

        ttk.Label(main, text="Audio Sample Rate (Hz, optional):").grid(
            row=6, column=0, sticky="w", **padding
        )
        ttk.Entry(main, textvariable=self.audio_sr_var).grid(
            row=6, column=1, sticky="we", **padding
        )

        ttk.Label(main, text="Master Scale:").grid(row=7, column=0, sticky="w", **padding)
        ttk.Entry(main, textvariable=self.master_scale_var).grid(
            row=7, column=1, sticky="we", **padding
        )

        ttk.Checkbutton(
            main,
            text="Loop queued audio",
            variable=self.loop_var,
        ).grid(row=8, column=0, columnspan=2, sticky="w", **padding)

        ttk.Separator(main).grid(row=9, column=0, columnspan=3, sticky="we", pady=(10, 5))

        self.channels_container = ttk.Frame(main)
        self.channels_container.grid(row=10, column=0, columnspan=3, sticky="nsew")

        add_btn = ttk.Button(main, text="Add Channel", command=self.add_channel)
        add_btn.grid(row=11, column=0, sticky="w", **padding)

        self.status_var = tk.StringVar(value="Idle")
        ttk.Label(main, textvariable=self.status_var).grid(
            row=11, column=1, sticky="e", **padding
        )

        button_frame = ttk.Frame(main)
        button_frame.grid(row=12, column=0, columnspan=3, sticky="e", pady=(10, 0))
        self.start_button = ttk.Button(button_frame, text="Start", command=self.start_transmission)
        self.start_button.grid(row=0, column=0, padx=5)
        self.stop_button = ttk.Button(
            button_frame, text="Stop", command=self.stop_transmission, state="disabled"
        )
        self.stop_button.grid(row=0, column=1, padx=5)

        main.columnconfigure(1, weight=1)
        main.rowconfigure(10, weight=1)

    def add_channel(self) -> None:
        row = ChannelRow(self.channels_container, self.remove_channel)
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
        offsets: List[float] = []
        gains: List[float] = []

        center_str = self.center_freq_var.get().strip()
        if not center_str:
            raise ValueError("Center frequency is required")
        center_freq = float(center_str)

        for idx, row in enumerate(self.channel_rows, start=1):
            freq_str = row.freq_var.get().strip()
            if not freq_str:
                raise ValueError("Each channel requires a transmit frequency")
            freq = float(freq_str)
            if not row.files:
                raise ValueError("Each channel must have at least one audio file selected")
            gain_str = row.gain_var.get().strip()
            try:
                gain = float(gain_str) if gain_str else 1.0
            except ValueError as exc:
                raise ValueError(f"Invalid gain for channel {idx}") from exc
            file_groups.append(row.files)
            offsets.append(freq - center_freq)
            gains.append(gain)

        return center_freq, file_groups, offsets, gains

    def start_transmission(self) -> None:
        if self.running:
            return
        try:
            center_freq, file_groups, offsets, gains = self._collect_channel_data()
            tx_sr = float(self.tx_sr_var.get())
            tx_gain = float(self.tx_gain_var.get())
            deviation = float(self.deviation_var.get())
            mod_sr = float(self.mod_sr_var.get())
            master_scale = float(self.master_scale_var.get())
            audio_sr = (
                float(self.audio_sr_var.get()) if self.audio_sr_var.get().strip() else None
            )
        except ValueError as exc:
            messagebox.showerror("Invalid configuration", str(exc))
            return

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
        self.status_var.set("Transmitting…")
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
