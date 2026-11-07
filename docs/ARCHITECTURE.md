# Spledio architecture

Spledio (working title "Spectral Workshop"; the visible name lives only in
`src/spledio/branding.py`) is a desktop-first Python application:
PySide6 UI, NumPy/SciPy DSP, libsndfile (via `soundfile`) for file I/O and
PortAudio (via `sounddevice`) for playback. Everything runs locally; there are
no network calls, accounts, or telemetry.

## Data conventions (apply everywhere)

| Concept | Representation |
|---|---|
| PCM audio in the engine | `numpy.ndarray`, shape **(channels, frames)**, C-contiguous. Layer storage dtype is `float32` (or `float64` when the source is 32-bit integer / 64-bit float). All processing math is `float64`. |
| PCM at the soundfile / PortAudio boundary | shape **(frames, channels)** – convert at the boundary only. |
| Time | sample index (int) internally; seconds only for UI and selection geometry. |
| Frequency | Hz (float). |
| STFT | `dsp/stft.py`: frame `k` is centred on sample `k*hop`; bin `b` is `b*sr/n_fft` Hz. Periodic windows, WOLA with exact normalisation. |
| Spectrogram image | display-only; computed by `rendering/` from audio. Never edited, never inverted. |
| Selection | `selections/`: vector geometry in (seconds, Hz) + feather params + channel scope. Rasterised to a soft mask in [0,1] on an STFT frame/bin grid. |
| Display settings | colour map, dB range, gamma, zoom, overlay opacity: never affect audio. |

## Packages

```
src/spledio/
  branding.py        product name / ids (rename here)
  app.py             QApplication bootstrap, `main()`
  dsp/               stft.py (transform), spectral.py (masked component / gain / extraction), gain.py
  selections/        geometry (shapes), Selection (ops + feather + channels), raster (mask on STFT grid)
  domain/            Project, Layer, Asset dataclasses; audibility (mute/solo) and mix rendering
  audio/             io.py (probe/import/validate), export.py (subtypes, clipping policy, dither, atomic write)
  rendering/         display analysis tiles + LOD cache (analysis.py), view image + colour maps + freq scales
  playback/          sources.py (read-only buffers + overlays), engine.py (PortAudio stream, transport)
  commands/          history.py (undo/redo, patch store), edits.py (concrete reversible commands)
  engine/            session.py – EditorSession facade used by UI and tests (no Qt imports)
  persistence/       project_file.py – versioned `.spledio` archive (manifest + npy patches)
  modules/           processing modules (preview / bypass / apply / cancel contract)
  models/            optional AI adapters (none installed by default)
  ui/                Qt widgets, action registry, panels, theme
```

Rule: nothing outside `ui/` and `app.py` imports Qt. `playback/` imports
`sounddevice` lazily so tests can use a fake backend.

## Editing model (phase-preserving delta render)

For a soft mask `M` on the edit STFT grid and gain `g` dB:

```
A = 1 + M * (10^(g/20) - 1)        (mute / delete: 10^(g/20) := 0)
component c = ISTFT(M · X)          (computed only on frames where M > 0)
result    y = x + (10^(g/20) - 1) · c
extract:  new layer = c, remainder = x - c   (sum == x exactly in float64)
```

`X` is the complex STFT (magnitude **and** phase) of the active layer. Only
frames that carry a non-zero mask are transformed, so samples outside
`[k0*hop - n_fft/2, (k1-1)*hop + n_fft/2)` are bit-identical to the input.
A pure full-band time-range selection uses a direct sample-domain envelope
instead (exact time boundaries, no STFT smearing).

Every committed edit stores its STFT config, gain, selection snapshot, channel
scope and target layer, plus a `LayerPatch` (region and samples) so undo
restores exact samples. A patch keeps only the version that is not in the layer
right now (the samples from before the edit while it is applied, the samples it
wrote while it is undone); undo/redo swap it with the layer's samples, in place
and block by block (streamed through a file when it is on disk), which is
exact because only commands write layer audio and history is linear. Display FFT settings are
independent and never reinterpret committed edits.

## Threads

* GUI thread: Qt only.
* Worker pool (`concurrent.futures.ThreadPoolExecutor`): spectrogram tiles,
  edit/preview/audition renders, export. Results are handed back to the GUI
  thread via Qt signals. NumPy / scipy.fft release the GIL for heavy work.
* PortAudio callback: copies from a pre-rendered, immutable buffer
  (`PlaybackSource.read_into`) into the output, applies monitor volume and a
  pre-computed crossfade ramp. No FFT, file I/O, locks, GUI calls or large
  allocations.

See `docs/PROJECT_FORMAT.md`, `docs/ACTIONS.md`, `docs/FEATURE_MATRIX.md` and
`docs/CONTINUATION.md`.
