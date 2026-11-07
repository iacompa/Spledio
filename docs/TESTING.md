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

Result: **425 passed, 1 skipped in 45 s** (426 collected) on 2026-11-07, and again in the release script's
run before the latest packaged build. The skipped test needs a real audio device
(`SPLEDIO_AUDIO_TEST=1`). `tests/test_licenses.py` covers Help ▸ Licenses and Notices, the About box
credits, the license texts the app must carry, and the checksum table in `THIRD_PARTY_NOTICES.md`. The time varies a lot with how busy the machine is: one run took 133 s, and one
during heavy load took 412 s. That slow run exposed a real bug: quick level presses could become
separate undo steps when rendering was slow. It is fixed, and a regression test was added
(`test_merge_counts_from_the_press_not_from_when_rendering_finished`).

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
| `test_areas.py` | 11 | adjusted areas: 1 dB / 6 dB step sequence, silence and back is exact, independent and overlapping areas, merge of rapid presses into one undo step (counted from the key press, so a slow render does not split them), locked layer, removed area refused, save/reopen, timeline freeze + undo |
| `test_memory.py` | 33 | long-file memory without changing samples: float32 component == float64 component rounded once, in-place gain == whole-region render, one stored undo copy per edit (swap), merged key presses add no undo memory, unused area components freed, difference of an older edit exact, spill to disk and back, block-rendered mix/export == whole render, adopted playback buffer, RAM-sized budgets, area components stay in RAM, block-wise clip peak == whole-region peak (NaN kept), in-place swap in RAM and on disk (also interrupted, and for another dtype) |
| `test_ui_areas.py` | 4 | real main window: ↑/↓/+/−/Shift/0 keys, click-to-pick, Delete removes, Highlight toggle, Areas panel, level bar (− / + / slider / Silence / ✕, placement, no busy flash, actions not left disabled), zoom shortcuts |
| `test_ui_workflow.py` | 4 | real main window, core resonance workflow end to end, every enabled action triggered once without error, `--smoke-test` mode passes |

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

## Packaged macOS app (unsigned local build, 2026-11-07)

Built with `UNSIGNED=1 PYTHON=/opt/homebrew/bin/python3.14 scripts/release_macos.sh`. Full details,
bundle contents and the fixes the first runs needed are in
[RELEASE_MACOS.md](RELEASE_MACOS.md#first-local-build-2026-11-07).

`Spledio --smoke-test FILE` runs inside the packaged app and checks it (list of checks in
RELEASE_MACOS.md). Results:

| Where it ran | Result |
|---|---|
| `dist/Spledio.app`, before signing | **PASSED**, all 9 checks |
| same app, ad-hoc signed, hardened runtime on | **PASSED**, all 9; the output stream ran on "MacBook Pro Speakers", 18.3 ms latency |
| launched with `open` (as Finder does) | **PASSED**, all 9 |
| app copied out of the mounted `Spledio-macOS-arm64.dmg` | `codesign --verify --deep --strict` valid; smoke test **PASSED**, all 9 |
| normal launch with a file, no test flags | main window opened with the file, spectrogram and all panels (screenshot taken); nothing printed to stderr |

Limits of that first build: it ran only on **macOS 26 or later**, because the Homebrew Python it was frozen
from targets macOS 26. It is ad-hoc signed, so Gatekeeper rejects it (`spctl -a -t exec`: "rejected");
only a notarized build passes Gatekeeper.

**Rebuilt with the python.org Python 3.14.10** (`PYTHON="$(scripts/get_build_python.sh)" UNSIGNED=1
scripts/release_macos.sh`): 418 passed, 1 skipped; minimum macOS **15.0**; smoke test **PASSED** before
and after signing and from a copy taken out of the DMG; normal launch opened the main window. A scan of all
187 binaries found no link to anything outside the app or macOS, and no path from the build Mac.

**Rebuilt with every license text** (commit `9ddfaa1`): 425 passed, 1 skipped; smoke test **PASSED** before and
after signing; 64 license files in `Contents/Resources/licenses/`; setuptools and `packaging` no longer
bundled. The library-replacement steps in `licenses/SOURCE-AND-REPLACING-LIBRARIES.md` were followed on a
copy of the app with a modified `libsndfile_arm64.dylib`: smoke test **PASSED** (details in
[RELEASE_MACOS.md](RELEASE_MACOS.md#first-local-build-2026-11-07)).

**First Developer ID signed and notarized build, released as 1.1** (commit `d5110f9`, new icon, bundle id `com.iacompa.spledio`):
425 passed, 1 skipped (run on that commit just before the build). Smoke test **PASSED** unsigned, after
signing, and on a copy taken out of the notarized DMG. Notarization **Accepted** with no issues; stapled.
Gatekeeper (`spctl`) **accepts** a quarantined copy of the DMG and the app copied out of it as "Notarized
Developer ID". Details in
[RELEASE_MACOS.md](RELEASE_MACOS.md#first-signed-and-notarized-build-2026-11-07).

## Memory measurements (2026-11-07)

`scripts/memory_profile.py` runs the real main window (offscreen Qt) on a generated 44.1 kHz stereo
16-bit WAV. It opens the file and waits for the spectrogram, makes a −6 dB adjusted area (200–4000 Hz,
from 30 s), exports the mix as 32-bit float WAV through the window's export job, opens the export
dialog, and undoes the area. A thread reads the process's memory footprint (`phys_footprint`, what
Activity Monitor shows as Memory) every 5 ms. **Before** is Spledio 1.1 (commit `60009fc`), **after**
is commit `1a3edd0`; both ran on this Mac (24 GB RAM, so both use a 512 MB tile cache and a 768 MB undo
budget). Every run also checked that the exported file equals the playback mix sample for sample and
that undo restores the layer bit-exactly; all passed.

Peak memory footprint in GB:

| File / area | Version | Open | Area applied | Export | Undo |
|---|---|---|---|---|---|
| 10 min / 5 min | before | 1.47 | 2.53 | 2.53 | 2.06 |
| | after | 0.78 | 1.30 | 1.30 | 1.31 |
| 30 min / 25 min | before | 3.68 | 6.26 | 5.08 | 5.86 |
| | after | 1.64 | 3.48 | 3.48 | 3.49 |
| 60 min / 50 min | before | 7.05 | 11.75 | 7.39 | 11.04 |
| | after | 2.87 | 6.26 | 3.97 | 5.28 |

The export peak includes what was already in use when the export started; the new export itself adds
nothing measurable (the mix is rendered in blocks). With the budgets of an 8 GB Mac (256 MB tile cache,
512 MB undo; set with `MEMPROF_RAM_GB=8` on this Mac) the new code measured: 30 min / 25 min: 1.64 /
3.49 / 3.49 / 2.80; 60 min / 50 min: 2.89 / 6.38 / 3.97 / 5.27.

What stays in use: after the 50-minute area the footprint settled at 3.96 GB, which is the layer
(1.27 GB), the float32 playback mix (1.27 GB), the area's component (1.06 GB) and the app itself; the
undo copy of the span is on disk. A `--tracemalloc` run (10 min) found only these arrays alive after
the area, plus the one undo copy that fits in RAM. Values read 3 s after a phase vary between runs,
because macOS takes freed memory back lazily: the same 30-minute area settled at 3.46 GB in one run and
at 2.03 GB in another, with peaks of 3.46 and 3.47 GB. The peaks repeat within about 0.2 GB.

Not measured: a visible window, playback while editing, files with several layers, 48 kHz files
(arrays about 9 % larger than at 44.1 kHz), and a real 8 GB Mac.

The full suite on commit `1a3edd0`, with `SPLEDIO_AUDIO_TEST=1`: **459 passed**, nothing skipped.

## Not tested

* Windows and Linux: no installers built, and the `.bat`/`.sh` setup scripts have not been run there.
* The packaged app on **another Mac**, and on macOS 15–26. The app declares macOS 15.0 as its minimum,
  but it has only been run on this Mac (macOS 27).
* **Audible** playback inside the packaged app. The smoke test runs the real output stream with silence;
  audible playback was tested from source (see above).
* The website page `website/spledio/index.html`: layout previewed in a browser; links point to the GitHub repository (public since 2026-11-07); not clicked through since.
* The opt-in real-device unit test in this record (see above).
* Very long files (> 30 min) for speed and real interactive use. Memory was measured up to 60 min, in the
  offscreen app only (see [Memory measurements](#memory-measurements-2026-11-07)).
* Screen readers.
