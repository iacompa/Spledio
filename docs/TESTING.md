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

Result: **482 passed in 46 s** with `SPLEDIO_AUDIO_TEST=1` (the real audio device test included) on
2026-11-07, after the fixes for selections at the file edges, extract levels, A/B after area changes,
the save marker, saving after the source changed, project-file checks, playback of files with more
channels than the output, Delete/Crop Time with a feather, dither only when samples need rounding,
A/B of multi-part edits, the transfer brush at different layer levels, the selection after undoing a
time edit, and the channel scope applied on the GUI thread. Without that variable the real-device
test is skipped (481 passed, 1 skipped, 38 s). Before those fixes the same run gave 437 passed. `tests/test_licenses.py` covers Help ▸ Licenses and Notices, the About box
credits, the license texts the app must carry, and the checksum table in `THIRD_PARTY_NOTICES.md`. The time varies a lot with how busy the machine is: one run took 133 s, and one
during heavy load took 412 s. That slow run exposed a real bug: quick level presses could become
separate undo steps when rendering was slow. It is fixed, and a regression test was added
(`test_merge_counts_from_the_press_not_from_when_rendering_finished`).

The real-device test in `tests/test_playback.py` is opt-in because it plays sound through the default
output (`SPLEDIO_AUDIO_TEST=1`). It passed in the run above. Real-device playback was also checked in the
real-app run below.

| File | Tests | What it covers |
|---|---:|---|
| `test_audio_io.py` | 106 | import of every PCM/float subtype and container libsndfile offers, MP3 / Ogg Vorbis / Opus decoded whole and repeatably (libsndfile's MP3 decoder is wrong when read in pieces), an MP3 without length information refused instead of cut short, channel counts, metadata, errors |
| `test_export.py` | 84 | WAV/FLAC subtypes, dither (skipped when no sample needs rounding, so an unedited file at its own depth is bit-identical), clipping policies, SRC, overwrite protection, export == mix |
| `test_core_dsp.py` | 78 | STFT perfect reconstruction, selective gain accuracy, untouched samples bit-identical, selections, feather, a selection that reaches the file start or end is edited right up to the edge, resonance finder (incl. a ring that lasts only part of the file) |
| `test_playback.py` | 36 | transport, loop, audition modes, A/B, device loss, latency compensation (fake device) |
| `test_ui_panels.py` | 28 | every dock panel builds, buttons map to registered actions |
| `test_rendering.py` | 25 | tiled spectrogram, caching, colour maps, display never changes audio, edited tiles keep their previous image until recomputed (no blinking) |
| `test_session.py` | 52 | save/reopen bit-exact, portable projects, relink with SHA-256, saving after the source file changed on disk, malicious archives rejected (incl. compressed members and undrawable selection shapes), autosave/recovery, unsaved mark kept after the oldest undo step is dropped, extract keeps the mix with layer gain / polarity / mute / solo, A/B and Difference after an area change and over every part of a multi-part edit, the transfer brush keeps the mix at any layer level / polarity and refuses a move between an audible and a muted layer, Delete/Crop Time use the drawn span, not the feather, undoing a time edit brings back the selection |
| `test_ui_canvas.py` | 20 | canvas tools, selection drawing, zoom/pan, crosshair |
| `test_ui_modules.py` | 12 | Preview / Bypass / Apply / Cancel through the Edit panel, tutorials |
| `test_areas.py` | 11 | adjusted areas: 1 dB / 6 dB step sequence, silence and back is exact, independent and overlapping areas, merge of rapid presses into one undo step (counted from the key press, so a slow render does not split them), locked layer, removed area refused, save/reopen, timeline freeze + undo |
| `test_ui_areas.py` | 8 | real main window: ↑/↓/+/−/Shift/0 keys, click-to-pick, Delete removes, Show / Hide areas (bar under the spectrogram, Shift+H, Areas panel stay in step), level bar (− / + / slider / Silence / ✕, placement, no busy flash, actions not left disabled), zoom shortcuts, zoom buttons fully readable at 1440 and 1024 px and zooming, trackpad pinch |
| `test_ui_workflow.py` | 15 | real main window, core resonance workflow end to end, a 3- and 6-channel file plays folded to a stereo output, the Channels scope applied to the selection on the GUI thread (edit, preview, mute, audition, area), every enabled action triggered once without error, `--smoke-test` mode passes and reports a refused file at once, an MP3 opened (File ▸ Open lists MP3/OGG/Opus), edited, saved and reopened, the time readout showing the new file's length at once, no leftover panel tab bars over the panels, always dark |

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

## Not tested

* Windows and Linux: no installers built, and the `.bat`/`.sh` setup scripts have not been run there.
* The packaged app on **another Mac**, and on macOS 15–26. The app declares macOS 15.0 as its minimum,
  but it has only been run on this Mac (macOS 27).
* **Audible** playback inside the packaged app. The smoke test runs the real output stream with silence;
  audible playback was tested from source (see above).
* The website page `website/spledio/index.html`: layout previewed in a browser; links point to the GitHub repository (public since 2026-11-07); not clicked through since.
* The opt-in real-device unit test in this record (see above).
* Very long files (> 30 min) for memory and speed.
* Screen readers.
