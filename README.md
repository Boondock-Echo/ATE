# ATE

Automated test engineering utilities focused on audio and RF experimentation.

## Multi-channel NBFM transmitter

This repository includes a multi-channel narrowband FM transmitter that can
drive HackRF, Pluto SDR, and PlutoPlus SDR devices. The Python flowgraph in
`multich_nbfm_tx.py` lets you queue playlists per-channel, apply per-channel
gains, and stream the composite RF waveform out an `osmosdr` sink.

### High-level workflow

```mermaid
graph TD
    A[Audio playlists per channel] -->|Queued by| B(QueuedAudioSource blocks)
    B -->|Resampled to match target rate| C[Resampler]
    C -->|FM modulated| D[NBFM Modulator]
    D -->|Upconverted & summed| E[Channel Combiner]
    E -->|Sent to SDR| F[osmosdr Sink]
    F -->|RF Transmission| G((Air))
```

1. Each logical channel keeps a playlist of WAV and/or MP3 files.
2. `QueuedAudioSource` reads the playlists, decodes audio, and performs optional
   sample-rate conversion so every stream matches the transmitter's base
   audio sample rate.
3. Individual channel audio is frequency shifted to its assigned offset or
   direct RF frequency, then modulated as narrowband FM.
4. All modulated channels are summed and forwarded to an `osmosdr` sink which
   pushes complex baseband samples to the connected SDR at the configured
   transmit sample rate and gain.

### Provided entry points

* `multich_nbfm_tx.py` – command-line transmitter that streams per-channel
  file queues (comma-separated file lists), optionally resamples mismatched
  audio sample rates on the fly, exposes per-channel gain trims, looping
  control, and either baseband offsets or direct frequency entry (`--freqs`).
  Additional channel features include:
  * Optional CTCSS tone generation per channel with either a normalized level
    (`--ctcss-level`) or explicit deviation target (`--ctcss-deviation`).
  * Optional DCS (CDCSS) code injection per channel so you can key receivers
    that expect digital squelch signaling.
  * Audio-driven gating (`--gate-open`, `--gate-close`, `--gate-attack-ms`,
    `--gate-release-ms`) that mutes quiet segments before modulation so idle
    channels do not inject unnecessary noise into the composite RF signal.
  * Per-channel configuration summaries printed at start-up for quick
    verification of offsets, gains, tone settings, and estimated bandwidth.
* `multich_gui.py` – lightweight Tkinter GUI that wraps the transmitter so you
  can configure devices, FRS/GMRS channel selections, per-channel playlists,
  and gain levels without memorizing CLI arguments. Core transmitter defaults
  now live in `transmitter_settings.json` so you can tweak them once via the
  GUI's **Settings → Manage Transmitter Settings…** dialog instead of retyping
  CLI overrides every launch. Pass `python multich_gui.py --help` to
  temporarily override any of the saved defaults via CLI flags.
* `ctcss_channel1_squelch.py` – helper utility that reuses the main
  transmitter pipeline to broadcast a continuous CTCSS tone (default 67.0 Hz)
  on channel 1. This is handy for validating that handheld receivers open
  squelch for the configured tone without re-entering full playlists.
* `audio_activity_report.py` – utility that scans WAV/MP3 files and reports
  their audio duty cycle (percentage of chunks whose RMS amplitude exceeds a
  configurable threshold). By default it evaluates one-second chunks with a
  0.1 RMS threshold and writes the CSV summary to `audio_duty_cycle.csv`, but
  you can override any of those settings via CLI flags. Use this to build CSV
  summaries of long playlists before scheduling them for transmission.

### Channel presets

The GUI reads `channel_presets.csv` (bundled in this repository) to populate
its channel picker. Edit that CSV to add, rename, or reorder channels for your
site. Each preset must provide a `frequency_hz` column, and the `display_name`
is shown in the dropdown list.

### Transmitter defaults

`transmitter_settings.json` stores the validated baseline values for TX sample
rate, modulation rate, FM deviation, master scale, tone levels, and squelch
gate thresholds. Use the GUI's Settings menu to edit these values through a
compact dialog. The dialog persists your changes back to the JSON file so
subsequent sessions inherit the tuned defaults without exposing the controls on
the main window.

### Dependencies

#### System dependencies

* GNU Radio runtime (`gnuradio`), including the analog, blocks, filter, and
  grc runtime modules used by the transmitter.
* `gr-osmosdr` and the associated SDR driver stack for the target hardware
  (e.g., HackRF, Pluto/PlutoPlus via libiio, or other SoapySDR-compatible
  devices).
* If you plan to use MP3 playlists with the optional `audioread` dependency,
  install a backend such as FFmpeg or GStreamer (system packages vary by
  platform).

#### Python dependencies

Install the core Python dependencies from `pyproject.toml`. Optional MP3
support can be enabled by installing the `mp3` extra.

```bash
pip install .
# Optional MP3 support
pip install ".[mp3]"
```

#### Node.js

Node.js is not required for this repository, and there is currently no
JavaScript build tooling.
