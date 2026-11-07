# Releasing Spledio for macOS: signed, notarized, downloadable

> **Status (2026-11-07):** the **unsigned local test build works**. `UNSIGNED=1 scripts/release_macos.sh`
> ran end to end on this Mac and produced a `.app` and `.dmg` that pass the built-in smoke test. See
> [First local build](#first-local-build-2026-11-07) below.
> **Not done yet:** a Developer ID signature, notarization, stapling, a build that runs on macOS older
> than 26, and a test on a second Mac. None of these can be done without the Apple membership and a
> python.org Python (steps 1–5 below).

Why this matters: macOS Gatekeeper blocks apps downloaded from the internet unless they are **signed with a
Developer ID** and **notarized by Apple**. With both, users double-click and the app opens normally.

## One-time setup (owner of the release)

1. **Join the Apple Developer Program** at developer.apple.com, as an individual or an organization.
   It is a paid yearly membership. The name on the membership is what macOS shows as the developer.
2. **Create a "Developer ID Application" certificate.** In Xcode: *Settings → Accounts → Manage
   Certificates → + → Developer ID Application*. Or create it in the developer portal and install it in the
   login keychain. Check it is there:
   ```bash
   security find-identity -v -p codesigning
   ```
   You should see `Developer ID Application: Your Name (TEAMID)`. Copy that exact text.
3. **Store notarization credentials** in the keychain once. Use an app-specific password from
   account.apple.com → *Sign-In and Security → App-Specific Passwords*:
   ```bash
   xcrun notarytool store-credentials spledio-notary --apple-id "you@example.com" --team-id TEAMID
   ```
   It asks for the app-specific password. The script refers to these credentials as `spledio-notary` and never
   sees the password.
4. **Decide the bundle identifier before the first release.** Today it is `org.spledio.Spledio`
   (`APP_ID` in `src/spledio/branding.py`). If you own a domain, use its reverse form, for example
   `com.yourdomain.spledio`. Changing it after release makes macOS treat it as a different app.
   Pass `BUNDLE_ID=...` to the script and update `branding.py` to match.
5. **Install a python.org CPython 3.14** (macOS installer from python.org) to build with. The Homebrew Python
   on the development Mac targets macOS 26+, so an app frozen from it would not run on older macOS.
   See [PACKAGING.md](PACKAGING.md).

## Every release

```bash
SIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)" \
NOTARY_PROFILE=spledio-notary \
PYTHON=/Library/Frameworks/Python.framework/Versions/3.14/bin/python3.14 \
scripts/release_macos.sh
```

What the script does, in order:

1. Creates or updates a separate build venv (`.venv-build`), installs the pinned requirements and PyInstaller,
   and records `dist-build-freeze.txt`.
2. Runs the test suite (set `SKIP_TESTS=1` to skip).
3. Freezes `Spledio.app` with PyInstaller in **onedir** mode. Never use onefile: Qt and libsndfile must
   stay replaceable files under the LGPL.
4. Sets the version in `Info.plist` and copies `LICENSE`, `THIRD_PARTY_NOTICES.md` and `licenses/` into the bundle.
5. Removes Qt plugins Spledio never uses, then every Qt framework nothing links to any more
   (`scripts/prune_qt_bundle.py`; see below).
6. Checks the bundle: no FFmpeg, WebEngine, Pdf or Multimedia; libsndfile and PortAudio present; `--version` runs;
   then runs **`Spledio --smoke-test`** on a generated test file (see below). Any failure stops the script.
7. Signs everything inside-out with the hardened runtime and a secure timestamp (`packaging/entitlements.plist`),
   verifies with `codesign --verify --deep --strict`, and runs the smoke test **again on the signed app**.
8. Builds `dist/Spledio-macOS-arm64.dmg` (app + Applications shortcut) and signs it.
9. Submits it to Apple's notary service and waits. If Apple rejects it, the script downloads the log to
   `dist/notarization-log.json` and stops.
10. Staples the ticket, validates it, and asks Gatekeeper (`spctl`) to assess the DMG.
11. Writes `dist/Spledio-macOS-arm64.dmg.sha256`.

Then:

```bash
git tag v0.1.0
```

```bash
git push origin v0.1.0
```

```bash
gh release create v0.1.0 dist/Spledio-macOS-arm64.dmg dist/Spledio-macOS-arm64.dmg.sha256 --title "Spledio 0.1.0" --notes-file release-notes.md
```

Keep the asset name `Spledio-macOS-arm64.dmg`: the website's download button links to
`releases/latest/download/Spledio-macOS-arm64.dmg`.

Release notes should state: version, minimum macOS, Apple Silicon only, SHA-256, what changed, known issues,
and "Source code for this release: the tag `vX.Y.Z` in this repository (GPL-3.0-only)".

## Test before publishing

* Copy the DMG to a **clean Mac or a new macOS user account**, download it through a browser so it gets the
  quarantine flag, open it, drag to Applications, and launch. It must open with no warning beyond the normal
  "downloaded from the internet" confirmation.
* Open audio, play (**this exercises the PortAudio/cffi callback under the hardened runtime**), adjust an
  area, preview, apply, undo, export WAV and FLAC, save, quit, reopen.
* If it crashes when playback starts, add `com.apple.security.cs.allow-unsigned-executable-memory` to
  `packaging/entitlements.plist` (the comment there explains), rebuild, and note it here.

## A local test build without a certificate

```bash
UNSIGNED=1 scripts/release_macos.sh
```

This produces an ad-hoc signed DMG for checking the packaging on your own Mac. It is **not notarized**:
other Macs will block it, so never publish it.

Ad-hoc signatures have no Team ID, so with the hardened runtime macOS refuses to load the app's own
libraries ("mapping process and mapped file (non-platform) have different Team IDs"). For this test build
only, the script adds `com.apple.security.cs.disable-library-validation` to a temporary copy of the
entitlements. The hardened runtime's other protections stay on, so the smoke test still shows whether the
audio callback works under them. A Developer ID build signs every file with one Team ID and uses
`packaging/entitlements.plist` unchanged (empty).

## The smoke test (`--smoke-test`)

```bash
dist/Spledio.app/Contents/MacOS/Spledio --smoke-test some-audio.wav
```

It runs inside the real app window, prints one PASS/FAIL line per check and exits with 0 (all passed)
or 1. Use it on any build, including a downloaded one. It checks:

* every dock panel loaded (no "panel failed to load");
* the app icon loads;
* PortAudio loads and lists output devices;
* **the app's real output stream runs, playing silence**: this exercises the PortAudio → cffi
  audio callback, the part most likely to break under the hardened runtime;
* the file opens and the spectrogram renders;
* an adjusted area at −6 dB changes the audio there and leaves the audio before it bit-identical;
* WAV export is identical to the playback mix;
* save + reopen of a project gives the identical mix.

It does not play audible sound and leaves no files behind.

## First local build (2026-11-07)

Command: `UNSIGNED=1 SKIP_TESTS=1 PYTHON=/opt/homebrew/bin/python3.14 scripts/release_macos.sh` on an
Apple Silicon Mac with macOS 27.0.1, PyInstaller 6.22.3 with pyinstaller-hooks-contrib 2026.9. The same run without `SKIP_TESTS` is recorded in
[TESTING.md](TESTING.md).

**Fixes the first runs needed (all now in the scripts):**

| Problem | Fix |
|---|---|
| The bundle check failed: `QtPdf` was bundled. PyInstaller collects every image-format and input-method plugin; `imageformats/libqpdf` pulls in QtPdf, and the virtual-keyboard plugin pulls in QtVirtualKeyboard, QtQuick, QtQml and more. `generic/libqtuiotouchplugin` (TUIO touch input over UDP) pulls in QtNetwork. | `scripts/prune_qt_bundle.py` removes those plugins and the image formats Spledio never reads (JPEG, TIFF, WebP, GIF, …), then removes every Qt framework that no remaining binary links to (checked with `otool -L`). |
| The signed app would not start: "different Team IDs" (ad-hoc + hardened runtime). | Library validation is turned off for `UNSIGNED=1` builds only (see above). |
| Dock panels are imported by name at run time, so PyInstaller might miss them. | `--collect-submodules spledio`. The smoke test confirms that all 7 panels load. |
| `plutil -insert` treats dots in an entitlement key as a key path. | `PlistBuddy` is used instead. |

**Result:**

| | |
|---|---|
| `dist/Spledio.app` | 125 MB. Qt frameworks: QtCore, QtGui, QtWidgets, QtDBus, QtSvg (before pruning there were also QtPdf, QtVirtualKeyboard(+Qml), QtQuick, QtQml(+Meta, Models, WorkerScript), QtOpenGL and QtNetwork). Qt plugins: `platforms/libqcocoa` (+ minimal, offscreen), `styles/libqmacstyle`, `imageformats/libqsvg`, `iconengines/libqsvgicon`. |
| Other native libraries | `libsndfile_arm64`, `libportaudio`, scipy's `libgfortran.5`, `libgcc_s.1.1` and `libquadmath.0`, `libpyside6`/`libshiboken6`, and from the Homebrew interpreter: OpenSSL 3.6.4 (`libssl.3`, `libcrypto.3`), xz 5.8.4 (`liblzma.5`), zstd 1.5.7 (`libzstd.1`), mpdecimal 4.0.1 (`libmpdec.4`). No FFmpeg, WebEngine, Pdf or Multimedia. |
| `dist/Spledio-macOS-arm64.dmg` | 56 MB, `hdiutil verify` valid, checksum file matches. |
| Final full run (with tests, PyInstaller pinned) | `UNSIGNED=1 PYTHON=/opt/homebrew/bin/python3.14 scripts/release_macos.sh`: 418 passed, 1 skipped; both smoke tests **PASSED**; DMG built; **118 s** in total. |
| Smoke test, unsigned app | **PASSED** (all checks) |
| Smoke test, ad-hoc signed app (hardened runtime on) | **PASSED**: output stream ran on "MacBook Pro Speakers", 18.3 ms latency. So the cffi audio callback works under the hardened runtime **without** `allow-unsigned-executable-memory`. |
| Launched through `open` (like a Finder double-click) | smoke test **PASSED**; a normal launch opened the main window with the file and its spectrogram |
| App copied out of the mounted DMG | `codesign --verify --deep --strict` valid; smoke test **PASSED** |
| Gatekeeper (`spctl -a -t exec`) | **rejected**, as expected for an ad-hoc signed app. Only a notarized build passes. |
| Minimum macOS of this build | **26.0**, because the bundled Homebrew `Python.framework` has `minos 26.0` (the launcher itself says 11.0). A release must be built with a python.org Python (step 5 of the setup). |

## Troubleshooting

| Symptom | Likely fix |
|---|---|
| `signing identity not found` | Run `security find-identity -v -p codesigning` and pass the exact string. The certificate must be in the login keychain together with its private key. |
| Notarization "Invalid": *not signed with a valid Developer ID* / *no secure timestamp* | A nested binary was missed. Check the log for its path, and make sure it is signed in step 6 (frameworks are signed after their contents). |
| Notarization "Invalid": *hardened runtime not enabled* | The binary was signed without `--options runtime`. Same as above. |
| App opens on the build Mac but not elsewhere | It was built with the Homebrew Python (macOS 26+ only). Use a python.org Python. |
| `Failed to load Python shared library … different Team IDs` | Ad-hoc signature + hardened runtime. Expected only with `UNSIGNED=1`, where the script already handles it. With a Developer ID it means some file was not re-signed; check that step 7 signed every Mach-O file. |
| A smoke-test line says `FAIL` | The message names the check. "panels failed to load" means a module was not bundled (check `--collect-submodules spledio`). An output-stream failure after signing means the audio callback is blocked by the hardened runtime: add `allow-unsigned-executable-memory` (see `packaging/entitlements.plist`). |
| "Spledio is damaged and can't be opened" | The DMG or app was modified after signing, or stapling failed. Rebuild. Do not edit files inside the signed app. |

## Licensing notes for distribution

* GPL-3.0-only allows selling and distributing binaries. Recipients must be able to get the
  **corresponding source** for the exact version. Keep the repository public, tag every release, and keep the
  build scripts (`scripts/release_macos.sh`, `packaging/`) in the repository.
* Developer ID signing and notarization are fine with the GPL. They do not stop users from modifying and
  running their own build, which they sign ad hoc.
* **Do not distribute through the Mac App Store.** Its terms conflict with the GPL.
* Qt/PySide6 are LGPL-3.0: ship them as separate frameworks (onedir build), include their license and a
  link to their source (already in `THIRD_PARTY_NOTICES.md` and `licenses/`).
