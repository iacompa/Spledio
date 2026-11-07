# Continuation guide

Where to pick up development. Read [ARCHITECTURE.md](ARCHITECTURE.md) first.

## Milestone status

| Milestone | Status |
|---|---|
| **M1 – core editor** (open, spectrogram, select, audition, gain/mute/delete, undo, save/reopen, export) | **Done**, covered by automated tests and a manual run on macOS (see [TESTING.md](TESTING.md)). |
| **M2 – layers & precision** (extract/copy to layer, layer mixer, brushes, feather, compound selections, resonance finder, delete/crop/insert time, autosave/recovery, portable projects, adjusted areas with ↑/↓ keys) | **Mostly done**. Missing: moving selected content, harmonic selection, markers UI, persistent undo. |
| **M3 – AI separation** | Not started. Adapter slot is `src/spledio/models/`. |
| **M4 – repair suite** (heal, de-click, de-hum, noise profile) | Not started. Contract for modules below. |
| **M5 – packaging** | Not started. Notes in [PACKAGING.md](PACKAGING.md). |

## Invariants you must keep

1. **Audio is never derived from the display image.** All edits come from the complex STFT of the
   layer audio (`dsp/spectral.py`). The spectrogram image is display-only.
2. **One edit = one undoable command** (`commands/history.py`). A cancelled or failed job pushes nothing
   (`EditorSession._commit` is only reached after the full result exists).
3. **Playback and export use the same mix** (`Project.render_mix`). Preview → Apply reuses the previewed
   samples, so what you heard is what gets committed.
4. **Display settings never change audio.** The processing `STFTConfig` is stored with each edit
   in `operation_log` and in the command; the display config is separate (`ViewState.display_config`).
5. **No network, no telemetry.** Do not add a dependency that phones home. Any future model download
   must be explicit, user-initiated, show the licence, and verify a checksum.
6. **Nothing outside `ui/` and `app.py` imports Qt.** `EditorSession` is fully testable headless.
7. **Adjusted areas are linear bookkeeping** (`engine/areas.py`). An area stores its component `c`;
   every change adds `(G' − G)·c` and removal adds `(1 − G)·c`. Never recompute an area's audio from the
   current layer (that would bake in other areas). Timeline edits must go through `EditorSession._timeline`,
   which freezes areas in the same undo step.

## How to add a processing module (M4 template)

A module is a pure function plus a parameter dataclass and a small panel:

```python
# src/spledio/modules/declick.py
@dataclass(frozen=True)
class DeclickParams:
    sensitivity: float = 0.5

def process(audio: np.ndarray, sr: int, region: tuple[int, int], selection_mask, params, cancel) -> np.ndarray:
    """Return the new samples for audio[:, region[0]:region[1]] (same shape)."""
```

Then in `EditorSession` add `preview_module(fn, params)` / reuse `commit_preview`: compute the
new region in a worker (`controller.run_job`), store it as an `EditPreview`, play it via
`controller.play_preview()`, and on Apply wrap it in a `PatchCommand` (`make_patch`) – exactly
like `preview_gain`. Register actions in `MainWindow._register_actions` with `enabled_when`
guards, and add the module to the Edit panel or a new dock. Add tests next to `tests/test_session.py`
that check: preview == commit == export, cancel leaves audio untouched, undo restores bit-exactly.

## How to add an AI separation backend (M3)

* Put adapters in `src/spledio/models/<name>.py` implementing
  `separate(audio, sr, cancel, progress) -> dict[str, np.ndarray]` (stem name → `(channels, frames)`).
* Never import the heavy dependency at module import time; check availability lazily and show
  "Optional dependency not installed" in the UI and the feature matrix.
* Each output stem becomes a new layer via one `CompositeCommand` of `AddLayerCommand`s so it is one undo.
* Record the model name, version, licence and weights checksum in the manifest's `models_used`.
* Document the model licence in [MODEL_LICENSES.md](MODEL_LICENSES.md) **before** merging. GPL-3.0-only
  compatibility must be checked (e.g. non-commercial weights cannot be bundled).

## Suggested next tasks (in order)

1. **Package for macOS**: run `UNSIGNED=1 scripts/release_macos.sh`, fix what breaks, then do the signed and notarized run
   ([RELEASE_MACOS.md](RELEASE_MACOS.md)) and test on a clean machine. The website launch waits on this ([WEBSITE_PLAN.md](WEBSITE_PLAN.md)).
2. **Output device picker** (the backend already accepts a `device`; there is no UI yet).
3. **Run and fix on Windows and Linux** (the `.bat` scripts are untested).
4. **Move selected content** in time/frequency (Transform tool with audio): extract → shift → re-add, as one composite command.
5. **Spectral heal / interpolate** (fill a selection from its neighbourhood) – the most-requested repair.
6. **Harmonic selection** (select f0 and integer multiples, using `dsp/resonance.py` peak tracking).
7. **Markers & regions UI** (model and file format already support markers).
8. **Persist undo history** in the project (store `PatchStore` blocks as `.npy` members).
9. **Chunk long-file operations** (time delete/insert, export SRC) for multi-hour files.

## Where things are

| Need to change… | Look in |
|---|---|
| App name / ids / extension | `src/spledio/branding.py` |
| What a menu item / button / shortcut does | `src/spledio/ui/main_window.py` (`_register_actions`) |
| Editing maths | `src/spledio/dsp/spectral.py`, `src/spledio/engine/session.py` |
| Selection shapes / feather / combine | `src/spledio/selections/` |
| Spectrogram colours / scales / tiles | `src/spledio/rendering/` |
| Canvas mouse interaction, area outlines / hint bar | `src/spledio/ui/spectrogram_view.py` |
| Adjusted areas (step sizes, merge window, maths) | `src/spledio/engine/areas.py`, `EditorSession.adjust_area`, `ui/level_bar.py`, `ui/panels/areas_panel.py` |
| Playback device / transport | `src/spledio/playback/engine.py` |
| Project file | `src/spledio/persistence/project_file.py`, [PROJECT_FORMAT.md](PROJECT_FORMAT.md) |
| Export formats / dither / clipping | `src/spledio/audio/export.py` |
| Theme | `src/spledio/ui/theme.py` |
| Icon | `src/spledio/resources/icons/app/`, `scripts/build_icons.py` |
