# Feature matrix (v0.1.0)

Status legend:

* **Implemented**: works end to end (UI → engine → playback/export) and has automated tests unless noted.
* **Experimental**: works, but is rough or only lightly tested.
* **Optional dependency not installed**: code path exists, needs an extra package.
* **Not supported yet**: no working implementation. Not shown as a working control in the UI.

"Tested" refers to tests in `tests/`; results are in [TESTING.md](TESTING.md).

## Files & project

| Feature | Status | Notes / tests |
|---|---|---|
| Open audio (WAV, AIFF, FLAC, OGG, MP3, CAF, W64, RF64 … via libsndfile 1.2.2) | Implemented | 8/16/24/32-bit int, 32/64-bit float, mono–multichannel. `test_audio_io.py` |
| Drag & drop a file onto the window | Implemented | |
| Open from command line (`spledio file.wav`) | Implemented | |
| Save / Save As project (`.spledio`, schema v1) | Implemented | atomic write; `test_session.py::test_save_*` |
| Portable project (embeds source audio) | Implemented | `test_portable_project_survives_missing_source` |
| Relink missing media with SHA-256 check | Implemented | `test_missing_media_relink_and_checksum` |
| Reject malformed / malicious project archives | Implemented | `test_malicious_archives_rejected` |
| Autosave + crash recovery | Implemented | every 120 s while dirty; recovered work keeps the original path and stays unsaved. `test_recovered_session_*` |
| Recent files | Not supported yet | |

## Display

| Feature | Status | Notes |
|---|---|---|
| Tiled, cached, multi-threaded spectrogram (512–16384 FFT) | Implemented | display resolution separate from processing resolution. `test_rendering.py` |
| Linear / logarithmic frequency scale, note labels | Implemented | |
| Colour maps: ember, ice, grayscale, viridis-like, cividis-like | Implemented | last two approximate the published maps |
| dB range, gamma, overlay opacity | Implemented | never affect audio |
| Zoom/pan time & frequency, fit project / selection, overview strip | Implemented | |
| Waveform strip, crosshair readout (time, Hz, note, dB) | Implemented | |
| Channel view: mix / left / right / per channel | Implemented | |
| Display active layer vs. composite of all layers | Implemented | |

## Selection

| Feature | Status | Notes |
|---|---|---|
| Time range, frequency range, rectangle, ellipse | Implemented | `test_core_dsp.py` |
| Freehand lasso, polygon lasso | Implemented | |
| Selection brush (size in pixels or in s/Hz, hardness, round/square) | Implemented | |
| Add / subtract / intersect / invert, modifier keys | Implemented | combine rules tested |
| Time feather and frequency feather (separate), on/off | Implemented | |
| Channel scope: linked / left / right / each channel | Implemented | |
| Select all / none / reselect / expand / contract / save & load named selection | Implemented | |
| Resonance finder (suggest narrow persistent peaks → select) | Implemented | found 2367 Hz for a planted 2370 Hz resonance |
| Harmonic selection (f0 + multiples) | Not supported yet | |
| Transform tool: move/resize the selection shape | Implemented | moves the **selection**, not audio |
| Move selected audio content | Not supported yet | |

## Editing (all undoable, phase-preserving, time never cut)

| Feature | Status | Notes |
|---|---|---|
| Gain on selection (−∞…+24 dB), presets −3/−6/−12/+3/+6 | Implemented | measured −6.000 dB on target, neighbours unchanged |
| Adjusted areas: ↑/↓ or +/− = 1 dB, Shift = 6 dB, 0 = silence | Implemented | `test_areas.py`, `test_ui_areas.py`; checked in the real app |
| Areas stay outlined with their level; click to pick and re-adjust; Delete removes the adjustment exactly | Implemented | overlapping areas removed in any order restore the original (tested) |
| Level bar next to the selection: − / + (1 dB, hold to repeat), slider, ▶ play in mix, Silence, ✕ remove | Implemented | `test_ui_areas.py::test_level_bar_next_to_selection_buttons_and_slider`; checked in the real app |
| No blinking while levels change (previous spectrogram kept until the new tiles are ready; quick jobs don't grey out the UI) | Implemented | `test_rendering.py::test_edit_keeps_previous_image_until_new_tiles_are_ready` |
| "Highlight" toggle hides area outlines (picked area stays visible); Adjusted Areas panel | Implemented | display only |
| Areas saved in the project and re-adjustable after reopening | Implemented | `test_areas.py::test_areas_survive_save_and_reopen` |
| Mute / delete spectral content | Implemented | ≥ 60 dB attenuation |
| Preview / Bypass / A-B / Apply / Cancel workflow | Implemented | preview == commit == export (tested) |
| Reduce brush / amplify brush | Implemented | one stroke = one undo |
| Transfer brush (move painted content to another layer) | Implemented | sum preserved (tested) |
| Extract to new layer / copy to new layer | Implemented | extract conserves the sum to ~1e-16 |
| Delete time / crop time / insert silence (ripples all layers) | Implemented | |
| Processing resolution per edit (FFT size, hop, window) | Implemented | stored with each operation |
| Undo / redo (200 steps, RAM budget + disk spill) | Implemented | bit-exact restore tested; rapid key presses on one area merge into one step |
| History panel (jump to any state) | Implemented | |
| Clipping warning after boosts | Implemented | |
| Spectral heal / interpolate, de-click, de-hum, noise reduction | Not supported yet | M4 |
| Time-stretch / pitch-shift | Not supported yet | |

## Layers

| Feature | Status |
|---|---|
| Multiple layers, active layer, colours | Implemented |
| Mute / solo (solo doesn't override mute) / gain / polarity / lock / visible / rename | Implemented |
| Reorder, duplicate, delete, merge | Implemented |
| Export layer / stems | Implemented |
| Layer blend modes | Not supported yet |

## Playback

| Feature | Status | Notes |
|---|---|---|
| Play / pause / stop / return to start / seek by click | Implemented | PortAudio via sounddevice |
| Loop selection or region, pre/post-roll | Implemented | |
| Audition selected / all frequencies (same time) / remainder / difference | Implemented | |
| Monitor volume (does not affect export) | Implemented | |
| Device-loss handling (playback stops with a message, press Play again) | Implemented | |
| Choosing the output device inside the app | Not supported yet | plays through the system default output; change it in the OS sound settings |
| Playhead latency compensation | Implemented | relies on the latency the driver reports |
| Mono monitored on both speakers | Implemented | monitoring only, export stays mono |

## Export

| Feature | Status | Notes |
|---|---|---|
| WAV 16/24-bit PCM, 32-bit float | Implemented | `test_export.py` |
| FLAC 16/24-bit | Implemented | |
| AIFF, OGG, MP3, 32-bit int, 64-bit float export | Not supported yet | |
| Mix / selected layers / stems / time range | Implemented | |
| Sample-rate conversion on export | Implemented | polyphase, in memory |
| TPDF dither, clipping policy (refuse / reduce level / limit / clip) | Implemented | |
| Refuses to overwrite a source file; confirms other overwrites | Implemented | |

## AI / models

| Feature | Status |
|---|---|
| Source separation (vocals/drums/bass/other), voice denoise, de-reverb | Not supported yet (no model bundled or downloaded) |

## Interface

| Feature | Status | Notes |
|---|---|---|
| Central action registry: every button = menu item = shortcut, with disabled reasons | Implemented | `test_ui_workflow.py` |
| Command palette (Ctrl/Cmd+K) | Implemented | |
| Keyboard shortcut editor | Implemented | |
| Built-in tutorials (F1) | Implemented | |
| Dockable, restorable panels, focus mode | Implemented | |
| Dark theme, font scaling, colour-blind-friendly maps | Implemented | |
| Light theme | Not supported yet | |
| Localisation | Not supported yet | English only |
