# Spledio

*Working title: “Spectral Workshop”.* A free, local, open-source desktop
**spectral audio editor**: open audio, view it as a high-resolution
spectrogram, select regions in time *and* frequency, listen to only what you
selected, attenuate / mute / remove / extract it without cutting time, compare
before/after, undo, save a project, and export the real edited audio.

* Runs entirely on your computer. No accounts, no telemetry, no uploads, no network access.
* Licensed **GPL-3.0-only** (see [LICENSE](LICENSE) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)).
* Not affiliated with, endorsed by, or derived from Steinberg, SpectraLayers, or any other commercial product.
  It uses its own name, icon, UI, project format and code.

> Rename the app by editing `src/spledio/branding.py` (display name, org, ids, project extension).
> The Python package is still called `spledio`.

## Quick start (macOS / Linux)

Requires Python **3.12+** (tested with CPython 3.14.7 on macOS arm64).

```bash
scripts/setup.sh        # creates .venv and installs pinned dependencies
scripts/run.sh          # starts the app
scripts/run.sh path/to/audio.wav
```

Manual equivalent:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -e .
.venv/bin/python -m spledio
```

Windows: `scripts\setup.bat` then `scripts\run.bat` (**written but not yet tested on Windows**).

Need a test file? `.venv/bin/python scripts/make_fixture.py demo.wav` writes an 8 s
stereo 48 kHz clip with music-like tones, noise, a click and a planted
**2370 Hz resonance** between 1.5 and 4.5 s.

## The core workflow (try this first)

1. **File → Open Audio…** (Ctrl/Cmd+O) and pick `demo.wav`.
2. Choose the **Rectangle** tool (**M**) and drag a narrow box around the bright line near 2.4 kHz between 1.5 s and 4.5 s.
   (Or open the **Resonance Workflow** panel → *Find candidates* → *Accept* on the top candidate.)
3. Press **S** to hear only the selection. **Shift+S** hears the same time range, all frequencies.
4. In the **Edit** panel set *−12 dB* and press **Preview (P)**. Toggle **Bypass (B)** or **A/B (A)** to compare.
5. **Apply** (Ctrl/Cmd+Return). Time is never cut — only that frequency band in that time span changes.
6. **D** plays what was removed (difference). **Ctrl/Cmd+Z** undoes, **Ctrl/Cmd+Shift+Z** redoes.
7. **File → Export…** (Ctrl/Cmd+E) writes WAV or FLAC; the export is the same render you hear.
8. **File → Save** stores a `.spledio` project; reopening restores layers and edits bit-exactly.

### Faster: adjust areas with the arrow keys

1. Drag a box on the spectrogram. A small **level bar** appears next to it: click **−** / **+** (1 dB per
   click, hold to repeat), drag its slider, or click **Silence**. The keys do the same: **↓ / ↑** (or
   **− / +**) = 1 dB, **Shift+↓ / ↑** = 6 dB, **0** = silence (below −48 dB the next step is silence).
   You hear the change while playing; **▶** on the bar plays that part in the full mix.
2. The region stays outlined with its level ("Area 1 · −12 dB"). Select another region and adjust it
   separately — every area keeps its own level, even where areas overlap.
3. Click an area later to pick it again; the arrow keys then change *that* area. **Delete** removes the
   adjustment (the audio there comes back exactly, other areas are untouched). **[ / ]** step through areas.
4. The **Highlight** toolbar button (**Shift+H**, on by default) hides the outlines when there are many.
   The **Adjusted Areas** panel lists every area with a level box, *Silence* and *✕ remove*.

Repeated presses on one area within 2 s are one undo step. Areas are saved in the project and can still be
changed after reopening. The arrow keys only adjust while the spectrogram has keyboard focus (click it);
zoom moved to **Ctrl/Cmd+= / Ctrl/Cmd+−** (time) and **Ctrl/Cmd+Shift+↑/↓** (frequency).

Press **F1** for built-in tutorials and **Ctrl/Cmd+K** for the searchable command palette.

## What works

See [docs/FEATURE_MATRIX.md](docs/FEATURE_MATRIX.md) for the honest per-feature status, and
[docs/ACTIONS.md](docs/ACTIONS.md) for what every button and shortcut does.

Highlights:

* Invertible STFT engine (512–16384 FFT, Hann/Hamming/Blackman, hop ½–⅛) with exact reconstruction.
* Phase-preserving selective gain / mute / delete / extract / copy-to-layer, with only the
  selected region recomputed; untouched samples stay bit-identical.
* Selections: time range, frequency range, rectangle, ellipse, freehand lasso, polygon, brush;
  add / subtract / intersect / invert; separate time and frequency feather.
* Reduce / amplify / transfer brushes (one stroke = one undo).
* Adjusted areas: select, press ↑/↓ to change the level; each area stays outlined, can be picked and
  changed again, silenced and brought back, or removed exactly, independently of other areas.
* Layers with mute, solo, gain, polarity, lock, rename, colour, reorder, duplicate, merge, export stems.
* Playback through PortAudio: play/pause/stop/loop, audition selection / remainder / difference, A/B.
* Resonance finder that suggests narrow, persistent peaks and selects them.
* Delete / crop time and insert silence (rippling all layers), all undoable.
* Project save/reopen (`.spledio`, schema v1), portable projects, missing-media relink with SHA-256 check,
  autosave + crash recovery.
* Export WAV (16/24-bit PCM, 32-bit float) and FLAC (16/24-bit), optional sample-rate conversion,
  TPDF dither, and an explicit clipping policy (refuse / reduce level / limit / clip).

## Development

```bash
.venv/bin/pip install -r requirements-dev.txt
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest
QT_QPA_PLATFORM=offscreen .venv/bin/python scripts/gen_action_docs.py   # regenerate docs/ACTIONS.md
.venv/bin/python scripts/build_icons.py                                 # regenerate icon PNG/ICNS/ICO
```

Real-device playback test (opt-in): `SPLEDIO_AUDIO_TEST=1 .venv/bin/python -m pytest tests/test_playback.py`.

## Documentation

| Doc | Contents |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | modules, data conventions, the editing model |
| [docs/USER_MANUAL.md](docs/USER_MANUAL.md) | **the user manual** (install, first edit, every workflow, shortcuts, troubleshooting) |
| [docs/FEATURE_MATRIX.md](docs/FEATURE_MATRIX.md) | implemented / experimental / not supported yet |
| [docs/ACTIONS.md](docs/ACTIONS.md) | every action, shortcut and when it is disabled (generated) |
| [docs/PROJECT_FORMAT.md](docs/PROJECT_FORMAT.md) | the `.spledio` file format |
| [docs/TESTING.md](docs/TESTING.md) | test suite and the results actually obtained |
| [docs/KNOWN_LIMITATIONS.md](docs/KNOWN_LIMITATIONS.md) | what is missing or rough |
| [docs/CONTINUATION.md](docs/CONTINUATION.md) | where to pick up development |
| [docs/PACKAGING.md](docs/PACKAGING.md) | building installers |
| [docs/RELEASE_MACOS.md](docs/RELEASE_MACOS.md) | signing + notarizing the Mac download (`scripts/release_macos.sh`, not run yet) |
| [docs/WEBSITE_PLAN.md](docs/WEBSITE_PLAN.md), [website/spledio/](website/spledio/index.html) | the app page for the website and the pre-launch checklist |
| [docs/PRIVACY_AND_STORAGE.md](docs/PRIVACY_AND_STORAGE.md) | what is stored where |
| [docs/DEPENDENCY_INVENTORY.md](docs/DEPENDENCY_INVENTORY.md), [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) | licences |
| [docs/MODEL_LICENSES.md](docs/MODEL_LICENSES.md) | optional AI models (none bundled) |
| [docs/BRANDING.md](docs/BRANDING.md) | the logo and how to regenerate it |

## License

Copyright © 2026 the Spledio contributors.

This program is free software: you can redistribute it and/or modify it under
the terms of the GNU General Public License as published by the Free Software
Foundation, **version 3 of the License only** (SPDX: `GPL-3.0-only`).

This program is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See [LICENSE](LICENSE) for details.
