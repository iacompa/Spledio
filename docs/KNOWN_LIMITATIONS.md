# Known limitations (v1.3)

Honest list of what is missing, rough, or only partly verified. See
[FEATURE_MATRIX.md](FEATURE_MATRIX.md) for per-feature status.

## Not implemented yet

* **AI / source separation** (vocals/drums/bass, voice denoise, de-reverb) – Milestone 3. No models are
  bundled or downloaded; the `models/` package is an empty adapter slot. See [MODEL_LICENSES.md](MODEL_LICENSES.md).
* **Repair modules beyond gain**: spectral heal/interpolate, de-click, de-hum, noise reduction by profile,
  harmonic selection, transient/tonal/noise split, time-stretch/pitch-shift. Not started.
* **Moving or transforming selected content** (drag a selected region to a new time/frequency). The
  *Transform* tool moves or resizes the **selection shape** only, not the audio.
* **Clone / paste spectral content**, cross-layer paste at a new position.
* **Plugin hosting** (VST3/AU/LV2/CLAP). Not planned for v1.
* **Exports**: only WAV (PCM 16/24, float 32) and FLAC (16/24). No AIFF, OGG, MP3 or 32-bit integer export,
  even though 32-bit integer and 64-bit float sources import (and are edited in float64).
* **Imports**: WAV (also WAVEX, RF64, W64), AIFF, FLAC, CAF and the lossy MP3, Ogg Vorbis and Opus
  (decoded by the mpg123, libvorbis and libopus built into the bundled libsndfile). No video containers,
  no AAC/M4A, no ADPCM or GSM inside WAV. A lossy import is decoded once to 32-bit float; editing it and
  exporting WAV or FLAC does not bring back what the MP3/OGG encoder removed. An MP3 without length
  information (a VBR file missing its Xing/Info header) is refused with a message instead of being cut
  short: libsndfile would stop decoding at its length estimate. Convert such a file to WAV or FLAC first.
  libsndfile 1.2.2's MP3 decoder gives wrong samples when a stream is read in pieces, so Spledio decodes
  every lossy file with a single read.
* **Output device**: Spledio plays through the system default output. There is no device picker in the app
  yet; change the output in the operating system's sound settings and press Play again.
* **Monitoring more channels than the output has**: a file with more channels than the output device
  (for example 6 channels on the built-in speakers or headphones) is *heard* as a stereo fold-down:
  channels 1, 3, 5 … left and 2, 4, 6 … right (a lone last channel on both sides), and 6-channel files as
  5.1 (L R C LFE Ls Rs, centre, LFE and the surrounds at −3 dB), scaled so the fold-down is never louder
  than the loudest channel. The file's own channel layout is not read. Editing and export keep every
  channel.
* **Markers / regions UI**: markers are stored in the project model and file but have no editor yet.
* **Undo history is not persisted** in the project; reopening starts a fresh history (the edited audio
  itself is restored exactly).

## Behaviour to know about

* **Time/frequency resolution trade-off.** Edits happen on an STFT grid (default 4096-point Hann,
  hop 1024 at any sample rate ≈ 11.7 Hz × 21 ms at 48 kHz). A selection narrower than one bin or
  shorter than one hop covers no grid cell and is refused with a message that says so; change
  *Processing resolution* in the Edit panel. The **display** resolution is separate and never changes audio.
* **Edges of an STFT edit spread by up to n_fft/2 samples** before/after the selected time
  (that is how a smooth spectral edit works). Use time feather to control it. Samples beyond that
  span are bit-identical. A full-band *time range* selection is applied in the sample domain and is exact.
* **Selections at the start or end of the file** are applied right up to the first / last sample. Audio
  that starts or stops abruptly at the file edge still leaves a short trace outside the selected band:
  silencing a 1–2 kHz box over a −9 dBFS 1.5 kHz test tone that starts at the first sample leaves about
  −32 dBFS in the first 10 ms and −60 dBFS by 40 ms (the edge itself is a broadband onset).
* **Boosting can clip.** The app warns with the new peak after any boost, and export lets you choose
  refuse / reduce level / limit / hard-clip. 32-bit float export never clips.
* **Whole-file in memory.** Each layer is held in RAM (float32 by default), and so is the float32
  playback mix: a 60-minute stereo 48 kHz file ≈ 1.4 GB per layer plus 1.4 GB for playback. Undo data
  moves to disk above a RAM budget (1/16 of the machine's RAM, 256–768 MB); layers and the playback mix
  do not. Measured on 44.1 kHz stereo files (peak memory of the app, 2026-11-07; details in
  [TESTING.md](TESTING.md#memory-measurements-2026-11-07)): a 30-minute file opens in 1.6 GB and a
  25-minute adjusted area peaks at 3.4–3.5 GB; a 60-minute file opens in 2.9 GB, and a 50-minute area
  peaks at 6.1–6.5 GB while it is applied and then settles at about 4.0 GB. On a Mac with 8 GB that
  leaves plenty of room for 30-minute files; on a 60-minute file a long area briefly needs more than
  6 GB, close to the limit (not tried on an 8 GB Mac), and macOS slows down once it runs short of memory.
  Several layers or several long areas need more.
* **Delete/crop time and insert silence** ripple every layer and are undoable, but they are full-array
  operations (fast enough for normal files, but not chunked).
* **Display at extreme zoom-out** decimates analysis frames (power-of-two stride). A very short event
  can be skipped visually at strides > 1; zoom in to see it. Audio is never affected.
* **Selection overlay** uses nearest-cell sampling; a one-cell selection can vanish when zoomed far out.
* **Adjusted areas and the timeline.** Delete/crop time and insert silence *freeze* adjusted areas: their
  effect stays in the audio but they leave the Areas list and can no longer be changed one by one (the app
  says so; undo brings them back). Other edits on top of an area are fine – areas stay exact because
  each one only adds or subtracts its own stored component.
* **Area keys need canvas focus.** ↑/↓, +/−, 0, [ and ] act on areas only while the spectrogram has
  keyboard focus (the hint bar says "Click the spectrogram, then …" otherwise), so lists and number boxes
  keep their own arrow keys. Zoom is on Ctrl/Cmd+= / Ctrl/Cmd+− and Ctrl/Cmd+Shift+↑/↓.
* **Area memory.** Each area keeps its component (the audio it covered, float32) in the project file and
  in RAM – it is read on every change of that area, so it moves to disk only after all other undo data.
  Its undo step keeps one copy of the samples from before it, which moves to disk first. A full-length
  stereo area of a 10-minute 48 kHz file is about 230 MB of component plus 230 MB of undo data. While an
  area is applied, memory rises briefly more than that: by about 1.8 GB for a 25-minute area of a
  30-minute 44.1 kHz file and by 3.2–3.5 GB for a 50-minute area of a 60-minute one (measured; it includes a new copy of
  the playback mix).
* **viridis-like / cividis-like** colour maps are approximations, not the exact published tables.
* **Playback latency compensation** uses the latency PortAudio reports; Bluetooth outputs often
  report it inaccurately, so the playhead can visibly lead/lag on them.
* **Sample-rate change on export** uses polyphase resampling in memory (no chunking): one input channel
  at a time, but the whole resampled output in float64.

## Platform status

| Platform | Status |
|---|---|
| macOS 27 (Apple Silicon), CPython 3.14.7 | Tested: full test suite, app launched and used (see [TESTING.md](TESTING.md)) |
| macOS 15–26 (Apple Silicon) | The packaged app declares macOS 15.0 as its minimum; **not tested** on those versions yet |
| Intel Macs | Not supported by the packaged app (Apple Silicon only); running from source there is not tested |
| Windows | Setup/run `.bat` scripts written, **not tested** |
| Linux | Expected to work (pure Python + wheels with bundled libsndfile/PortAudio); **not tested** |
| macOS `.app`/`.dmg` | **Signed with a Developer ID and notarized** for macOS 15+ (built 2026-11-07; Gatekeeper accepts the downloaded DMG). Not yet tried on a second Mac. See [RELEASE_MACOS.md](RELEASE_MACOS.md). |
| Windows/Linux installers (.exe/.msi, AppImage) | **Not built yet**. See [PACKAGING.md](PACKAGING.md). |

## Accessibility

* Keyboard: every action has a menu entry and almost all have shortcuts; the command palette (Ctrl/Cmd+K)
  reaches any action by name. Shortcuts are remappable.
* Font scaling: `--font-scale 1.25` (or `SPLEDIO_FONT_SCALE`).
* Colour-blind-friendly colour maps are available (viridis-like, cividis-like, grayscale). Selection
  overlays use outline + hatch, not colour alone.
* Screen-reader support is limited to Qt's defaults: widgets have accessible names, but the spectrogram
  canvas itself is not navigable by a screen reader.
