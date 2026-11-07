# Known limitations (v1.1)

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
* **Whole-file in memory.** Each layer is held in RAM (float32 by default). A 60-minute stereo 48 kHz
  file ≈ 1.4 GB per layer. Undo patches spill to disk above a RAM budget, but layers themselves do not.
  Very long files with many layers can exhaust memory. Measured in the running app (2026-11-07, 44.1 kHz
  stereo, one layer): a 10-minute file uses about 1.5 GB once its spectrogram is drawn and a 30-minute file
  about 1.6 GB (the display cache is capped at 512 MB); one adjusted area adds about 4 to 5 times the audio
  it covers (0.9 GB for 5 minutes, 2.9 GB for 25 minutes), and an export briefly needs about twice the
  audio again. Songs and recordings up to about 15 minutes are comfortable on a Mac with 8 GB; much
  longer files work but slow down once macOS runs short of memory.
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
* **Area memory.** Each area keeps its component (the audio it covered) in the undo store and in the
  project file: a full-length stereo area of a 10-minute 48 kHz file is about 230 MB in float32 on disk.
  In the running app an area costs more, about 4 to 5 times the audio it covers (see *Whole-file in
  memory*).
* **viridis-like / cividis-like** colour maps are approximations, not the exact published tables.
* **Playback latency compensation** uses the latency PortAudio reports; Bluetooth outputs often
  report it inaccurately, so the playhead can visibly lead/lag on them.
* **Sample-rate change on export** uses polyphase resampling in memory (no chunking).

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
