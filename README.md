# ATE
automated test engineering

## Multi-channel NBFM transmitter

This repository now includes an enhanced multi-channel narrowband FM transmitter
for HackRF, Pluto SDR, and PlutoPlus SDR devices.

* `multich_nbfm_tx.py` – command-line transmitter that streams per-channel
  file queues (comma-separated file lists), optionally resamples mismatched
  audio sample rates on the fly, exposes per-channel gain trims, looping
  control, and either baseband offsets or direct frequency entry (`--freqs`).
* `multich_gui.py` – lightweight Tkinter GUI that wraps the transmitter so you
  can configure devices, frequencies, per-channel playlists, and gain levels
  without memorizing CLI arguments.

Both scripts require GNU Radio with `osmosdr` support and NumPy available in
your Python environment. MP3 playlists are supported via the optional
[`audioread`](https://github.com/beetbox/audioread) dependency; install it with
`pip install audioread` if you want to mix MP3 tracks alongside WAV files.
