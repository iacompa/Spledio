# Testing and results actually obtained

This page records only what was actually run. Nothing here is projected or assumed.

## Environment of the recorded run (2026-11-07)

| | |
|---|---|
| Machine | Apple Silicon (arm64), macOS 27.0.1 |
| Python | 3.14.7 (project `.venv`) |
| Libraries | NumPy 2.4.6 · SciPy 1.17.1 · PySide6 6.11.3 · soundfile 0.13.1 (libsndfile 1.2.2) · sounddevice 0.5.5 (PortAudio V19.7.0) |

## Automated test suite

```bash
.venv/bin/python -m pytest -q
```

Result: **416 passed, 1 skipped in 133 s** (417 collected). An earlier run of the same suite took 27 s;
the time varies with how busy the machine is.

The skipped test is `tests/test_playback.py` real-device test. It is opt-in because it plays sound
through the default output (`SPLEDIO_AUDIO_TEST=1`). It was **not** run for this record. Real-device
playback was instead checked in the real-app run below.

| File | Tests | What it covers |
|---|---:|---|
| `test_audio_io.py` | 103 | import of every PCM/float subtype and container libsndfile offers, channel counts, metadata, errors |
| `test_export.py` | 80 | WAV/FLAC subtypes, dither, clipping policies, SRC, overwrite protection, export == mix |
| `test_core_dsp.py` | 74 | STFT perfect reconstruction, selective gain accuracy, untouched samples bit-identical, selections, feather, resonance finder (incl. a ring that lasts only part of the file) |
| `test_playback.py` | 36 | transport, loop, audition modes, A/B, device loss, latency compensation (fake device) |
| `test_ui_panels.py` | 28 | every dock panel builds, buttons map to registered actions |
| `test_rendering.py` | 25 | tiled spectrogram, caching, colour maps, display never changes audio, edited tiles keep their previous image until recomputed (no blinking) |
| `test_session.py` | 22 | save/reopen bit-exact, portable projects, relink with SHA-256, malicious archives rejected, autosave/recovery |
| `test_ui_canvas.py` | 20 | canvas tools, selection drawing, zoom/pan, crosshair |
| `test_ui_modules.py` | 12 | Preview / Bypass / Apply / Cancel through the Edit panel, tutorials |
| `test_areas.py` | 10 | adjusted areas: 1 dB / 6 dB step sequence, silence and back is exact, independent and overlapping areas, merge of rapid presses into one undo step, locked layer, removed area refused, save/reopen, timeline freeze + undo |
| `test_ui_areas.py` | 4 | real main window: ↑/↓/+/−/Shift/0 keys, click-to-pick, Delete removes, Highlight toggle, Areas panel, level bar (− / + / slider / Silence / ✕, placement, no busy flash, actions not left disabled), zoom shortcuts |
| `test_ui_workflow.py` | 3 | real main window, core resonance workflow end to end, every enabled action triggered once without error |

UI tests run headless (`QT_QPA_PLATFORM=offscreen`) against the real `MainWindow` with a fake audio device
whose output buffer is pulled and measured.

## Real application run (native macOS window, real sound card)

Scratch driver scripts opened the real window (Cocoa platform, not offscreen) and drove it through the
user-facing actions on the bundled demo file (`scripts/make_fixture.py`: 8 s, 48 kHz stereo, planted
2370 Hz resonance from 1.5 to 4.5 s).

### Core workflow

| Step | Observed |
|---|---|
| Open `demo.wav` | 48000 Hz, 2 ch, 8.000 s |
| Resonance Workflow panel → *Find candidates* | top candidate **2367.2 Hz**, +40.9 dB prominence; next ones were the music tones (439, 328, 275, 223 Hz) |
| *Accept* top candidate | selection 1.47–4.54 s, 2332–2402 Hz |
| Hear selected (S) | playing on **MacBook Pro Speakers**, reported latency 18.3 ms, no device error, playhead advanced |
| Preview −12 dB, Bypass | audition switched Preview → "Bypass – original" while playing |
| Apply | 2370 Hz: **−12.00 dB** on both channels; 440 Hz: **±0.0000 dB**; length unchanged; first second bit-identical |
| Export WAV 24-bit TPDF | max \|export − mix\| = **1.78e-7** (24-bit quantisation + dither) |
| Save project, reopen | mix **bit-identical** after reopen, project not dirty |

### Adjusted areas

| Step | Observed |
|---|---|
| Box on the 2.37 kHz ring, ↓ ×4 | Area 1 at −12 dB |
| Box on 300–360 Hz, 5.0–7.5 s, **0** | Area 2 silenced (hatched outline) |
| Box on 6–12 kHz hiss, **+** ×2 | Area 3 at +6 dB (green outline) |
| History | one entry per area (rapid presses merged) |
| Click inside Area 1 | Area 1 picked (handles shown, list row highlighted) |
| Highlight off | Areas 2 and 3 hidden, picked Area 1 still shown |
| Delete | Area 1 removed; Areas 2 and 3 unchanged |

### Level bar, 1 dB keys, no blinking

| Step | Observed |
|---|---|
| Box on the 2.37 kHz ring | level bar shown just above the box: "Selection · 0 dB" |
| ↓ ×6 (window active, canvas focused) | Area 1 at **−6 dB**, one undo step |
| "−" on the level bar ×3 | **−9 dB**, bar shows "-9 dB" |
| Shift+↓ | **−12 dB** |
| Slider to −20 | **−20 dB** |
| After all changes | Undo and Export enabled (nothing left greyed out) |
| Spectrogram redraws during these changes | 16 redraws, **0** showed the pending hatch; the only "busy" signal was the file-open job |

When the window was not the active window (someone using another app), key presses sent by the script
were not delivered as shortcuts, which is normal macOS behaviour. The offscreen tests cover the key logic.

## Not tested

* Windows and Linux. The `.bat`/`.sh` setup scripts for those platforms have not been run there.
* Packaged builds (no `.app`/installer has been built yet; see [PACKAGING.md](PACKAGING.md)).
* `scripts/release_macos.sh` (signing, notarization, DMG): syntax-checked with bash 3.2 and shellcheck only, never run.
* The website page `website/spledio/index.html`: layout previewed in a browser; links point to the GitHub repository, which is still private.
* The opt-in real-device unit test in this record (see above).
* Very long files (> 30 min) for memory and speed.
* Screen readers.
