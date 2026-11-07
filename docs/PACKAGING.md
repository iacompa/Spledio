# Packaging

> **macOS: use [RELEASE_MACOS.md](RELEASE_MACOS.md) and `scripts/release_macos.sh`.** That script is the
> working, tested recipe for the macOS PyInstaller build: an unsigned local `.app`/`.dmg` was built
> and passed the smoke test on 2026-11-07. Signing with a Developer ID and notarization are **not done yet**.
> The macOS notes below are the original research behind it. Where they disagree with the script,
> the script wins.
>
> **Windows and Linux: NOT YET TESTED.** No `.exe`, installer or AppImage has been built.
> Briefcase was not tried. PyInstaller is installed only in the separate build venv `.venv-build`
> (created by the release script), not in `.venv`.

## Blockers and facts found while preparing this (2026-11-06)

- **`src/ui/` was still changing during this audit.** Early in the audit,
  `spledio.ui.spectrogram_view` and `spledio.ui.icons` were missing, so the
  main window could not be built. Both exist now, and every `spledio.*`
  module imports without error. A full GUI launch was not exercised. Before
  freezing, re-check the PySide6 imports: at the final re-check `src/` used
  only QtCore, QtGui and QtWidgets.
- **Ship the PNG icon set.** `ui/icons.py:app_icon()` reads
  `resources/icons/app/png/icon_*.png` and falls back to the SVG master only
  if those are missing. Keep the PNGs in the bundle (`--collect-data
  spledio`) so the SVG plugins and QtSvg are not needed.
- **The Homebrew interpreter limits the target OS.** The development
  CPython 3.14.7 is a Homebrew build whose `Python` binary has `minos 26.0`,
  so a bundle frozen from it needs macOS 26 or later (verified). Releases are
  frozen with the python.org CPython instead (`scripts/get_build_python.sh`,
  no install needed), which gives **macOS 15.0**: PySide6 6.11.3's Python
  modules need 15.0 although its wheel is tagged `macosx_13_0`; numpy/scipy
  need 14.0 (verified 2026-11-07).
- **numpy/scipy are arm64-only here.** Build an arm64 `.app`. A universal2
  or x86_64 build needs x86_64 wheels and a separate build (untested).
- **Signatures.** All bundled native libraries in the wheels are ad-hoc
  signed (`Signature=adhoc`, no TeamIdentifier), checked on QtCore,
  libsndfile, libportaudio and libgfortran. Distribution therefore needs
  re-signing with a Developer ID.
- **No microphone access.** Spledio only opens `sounddevice.OutputStream`,
  so no microphone usage description should be needed (**to verify**: that
  device enumeration does not prompt).
- **No Finder "open with".** Spledio does not handle `QFileOpenEvent`, so
  double-clicking a `.spledio` file in Finder would not open it. Do not
  declare document types in `Info.plist` until that is implemented.
- **No `qmacstyle` needed.** The UI uses the built-in **Fusion** style
  (`ui/theme.py`), so the `styles/libqmacstyle` plugin is not required.

## macOS: PyInstaller `.app` (works: automated in `scripts/release_macos.sh`)

### 1. Build environment

Use a separate venv so the verified `.venv` stays untouched. Use the same
pins as `requirements.txt`.

```sh
cd "/path/to/Spledio"
"$(scripts/get_build_python.sh)" -m venv .venv-build   # python.org CPython, unpacked (verified)
.venv-build/bin/python -m pip install -r requirements.txt
.venv-build/bin/python -m pip install -r requirements-packaging.txt            # exact PyInstaller pins
.venv-build/bin/python -m pip install . --no-deps
.venv-build/bin/python -m pip freeze > build-freeze.txt                       # keep with the release
```

The PyInstaller pins are in `requirements-packaging.txt` (the versions of the first working
build). For stronger reproducibility, add hashes and install with `--require-hashes`, build from a
clean checkout, and keep `dist-build-freeze.txt` (written by the release script) plus the SHA-256 of
the final `.dmg` with each release. PyInstaller output is not bit-for-bit reproducible.

### 2. Freeze

```sh
.venv-build/bin/pyinstaller --noconfirm --clean --windowed \
  --name Spledio \
  --osx-bundle-identifier org.spledio.Spledio \
  --icon src/spledio/resources/icons/app/spledio.icns \
  --collect-data spledio \
  --exclude-module tkinter \
  --exclude-module pytest --exclude-module pytestqt --exclude-module pygments \
  --exclude-module PySide6.QtNetwork --exclude-module PySide6.QtQml \
  --exclude-module PySide6.QtQuick --exclude-module PySide6.QtMultimedia \
  --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtPdf \
  src/spledio/__main__.py
```

- Build in **onedir** mode, which is what `--windowed` produces on macOS.
  **Never `--onefile`**: Qt and libsndfile must stay separate, replaceable
  libraries (LGPL).
- Icons: macOS `src/spledio/resources/icons/app/spledio.icns`; Windows
  `src/spledio/resources/icons/app/spledio.ico`. Regenerate both with
  `.venv/bin/python scripts/build_icons.py`.
- Set `CFBundleShortVersionString`/`CFBundleVersion` to `0.1.0` (from
  `spledio.__version__`), for example by generating a `.spec` file with
  `--osx-bundle-identifier` and adding an `info_plist` to its `BUNDLE(...)`.
  Keep the `.spec` file in the repository: it is part of the GPL
  "Corresponding Source" (the build scripts).

### 3. Inspect what was collected (do this every build)

The release script does this automatically, then runs `--smoke-test`. What the first real build
contained is listed in [RELEASE_MACOS.md](RELEASE_MACOS.md#first-local-build-2026-11-07). In short, these
predictions were right except for two things. **QtSvg** is also kept, because the SVG icon engine and image
plugin link it. **PyInstaller bundles extra plugins** (`imageformats/libqpdf`, the virtual keyboard, TUIO
touch), which pull in QtPdf, QtQuick/QtQml and QtNetwork; `scripts/prune_qt_bundle.py` removes them.
The interpreter brought OpenSSL, xz, zstd and mpdecimal; `libsqlite3` was not collected.

```sh
APP=dist/Spledio.app
find "$APP" -name "*.framework" -maxdepth 8 | sed 's#.*/##' | sort -u   # expect QtCore QtGui QtWidgets QtDBus (+ QtSvg if svg plugins come along)
find "$APP" \( -name "libav*" -o -name "libsw*" -o -name "QtWebEngine*" -o -name "QtPdf*" -o -name "QtMultimedia*" \)   # expect nothing
find "$APP" \( -name "libsndfile*" -o -name "libportaudio*" -o -name "libgfortran*" -o -name "libquadmath*" \)   # expect all four
find "$APP" -path "*plugins*" -name "*.dylib" | sed 's#.*/plugins/##' | sort   # expect platforms/libqcocoa; remove imageformats/libqpdf if present
"$APP/Contents/MacOS/Spledio" --version
```

Expected native contents, based on the venv analysis: Qt frameworks QtCore,
QtGui, QtWidgets and QtDBus (QtGui links QtDBus on macOS), `libpyside6`,
`libshiboken6`, the `platforms/libqcocoa` plugin, `libsndfile_arm64.dylib`,
`libportaudio.dylib`, and scipy's `libgfortran.5`, `libgcc_s.1.1` and
`libquadmath.0`. The interpreter may pull in Homebrew or python.org copies
of OpenSSL `libcrypto` (Spledio imports `hashlib`), `liblzma`, `libmpdec`
and `libsqlite3`. In the python.org build they are OpenSSL 3.5.9, zstd 1.5.7,
XZ Utils 5.2.3 and mpdecimal 4.0.1 (no SQLite); their notices are in `licenses/`, see
[`THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md).

Then test on a **clean** Mac or a new user account: open, play, edit, undo,
save, reopen, export WAV/FLAC, force-quit and check recovery. Check that no
data appears outside the paths in
[`PRIVACY_AND_STORAGE.md`](PRIVACY_AND_STORAGE.md).

### 4. LGPL / GPL compliance inside the bundle

Status 2026-11-07: done in `scripts/release_macos.sh`, the app and `licenses/`
(only the libquadmath GCC version is still open, see
[`THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md#status-of-the-notices-for-a-distributed-app-2026-11-07)).

1. `Spledio.app/Contents/Resources/licenses/` gets `LICENSE` (Spledio, GPL-3.0-only),
   `THIRD_PARTY_NOTICES.md` and every file in `licenses/` except the development-only
   tools. **Help ▸ Licenses and Notices** shows them; the About box lists the main
   components and carries the LAME, mpg123 and FreeType credits.
2. The texts the wheels do not ship are in `licenses/`: LGPL-3.0 (Qt/PySide6/shiboken6),
   LGPL-2.0 (LAME), the upstream files of libFLAC, libogg, libvorbis, libopus, mpg123
   and PortAudio, Qt's third-party notices (`Qt-6.11.3-third-party-notices.md` and
   18 license texts), and the notices for the interpreter's libraries.
3. Source: the release script makes `dist/Spledio-<version>-source.tar.gz`
   (`git archive` of the built commit; a signed release refuses uncommitted changes)
   and `dist/third-party-sources/` (`scripts/fetch_lgpl_sources.sh`: Qt 6.11.3 qtbase
   and qtsvg, pyside-setup 6.11.3, libsndfile 1.2.2, mpg123, LAME, the Xiph codecs and
   the libsndfile-binaries build recipe, checked against pinned SHA-256). Publish both
   with the DMG. [`licenses/SOURCE-AND-REPLACING-LIBRARIES.md`](../licenses/SOURCE-AND-REPLACING-LIBRARIES.md)
   explains this to users.
4. **Replacing libraries** (in the same file):
   - Qt lives in `Contents/Frameworks/PySide6/Qt/lib/*.framework`,
     libsndfile in `Contents/Frameworks/_soundfile_data/libsndfile_arm64.dylib`, PortAudio
     in `Contents/Frameworks/_sounddevice_data/portaudio-binaries/libportaudio.dylib`
     (paths verified in the first build, 2026-11-07);
   - the replacement must be ABI-compatible (Qt 6.11.x for PySide6 6.11.3);
   - after replacing, re-sign ad hoc with
     `codesign --force --deep --sign - Spledio.app` and open the app once
     with right-click > Open.

   This covers the LGPLv3 requirement that users can run a modified
   library.
5. Do not merge, strip or obfuscate the Qt/libsndfile binaries. PyInstaller
   rewrites load commands and re-signs them; that keeps them replaceable.
6. Do **not** ship FFmpeg (`libav*`/`libsw*`), QtWebEngine, QtPdf,
   QtMultimedia, the Qt tools (`Designer.app`, `qmlls`, ...) or the
   GPL-only Qt add-ons. Spledio uses none of them. See
   [`DEPENDENCY_INVENTORY.md`](DEPENDENCY_INVENTORY.md).

### 5. Code signing and notarization (ad-hoc signing tested; Developer ID + notarization NOT YET TESTED)

Needs an Apple Developer Program membership and a **Developer ID
Application** certificate.

```sh
# Option A: let PyInstaller sign: add  --codesign-identity "Developer ID Application: NAME (TEAMID)" \
#           --osx-entitlements-file packaging/entitlements.plist   to the command above.
# Option B: sign afterwards, inside-out (every Mach-O, then the app):
find dist/Spledio.app -type f \( -name "*.dylib" -o -name "*.so" \) -print0 \
  | xargs -0 codesign --force --timestamp --options runtime --sign "Developer ID Application: NAME (TEAMID)"
codesign --force --timestamp --options runtime --entitlements packaging/entitlements.plist \
  --sign "Developer ID Application: NAME (TEAMID)" dist/Spledio.app
codesign --verify --deep --strict --verbose=2 dist/Spledio.app

hdiutil create -volname Spledio -srcfolder dist/Spledio.app -ov -format UDZO dist/Spledio-0.1.0-arm64.dmg
codesign --timestamp --sign "Developer ID Application: NAME (TEAMID)" dist/Spledio-0.1.0-arm64.dmg
xcrun notarytool submit dist/Spledio-0.1.0-arm64.dmg --keychain-profile "spledio-notary" --wait
xcrun stapler staple dist/Spledio-0.1.0-arm64.dmg
spctl -a -vvv -t open --context context:primary-signature dist/Spledio-0.1.0-arm64.dmg
```

- `packaging/entitlements.plist` exists and is empty (`<dict/>`).
- Hardened runtime is required for notarization. In the ad-hoc signed test build
  the PortAudio/cffi output callback ran under the hardened runtime **without**
  `allow-unsigned-executable-memory` (smoke test, 2026-11-07). Library validation had to
  be turned off for that ad-hoc build only, because ad-hoc signatures have no Team ID.
  A Developer ID build should need no entitlements at all. **To confirm** on the first signed build.
- Signing all frameworks inside-out with `--deep` alone is unreliable. Sign
  nested `*.framework` bundles explicitly if `codesign --verify` complains.
- Notarization does not stop users from replacing LGPL libraries. They
  re-sign ad hoc as described above. Say so in the notices.

## macOS alternative: Briefcase (NOT YET TESTED)

BeeWare Briefcase builds a `.app`/`.dmg` around its own standalone Python
support package and has built-in `--identity` signing and notarization
(`briefcase create/build/package macOS`). It would need a
`[tool.briefcase]` section in `pyproject.toml` (not added) listing
`sources = ["src/spledio"]`, the pinned `requires`, and
`icon = "src/spledio/resources/icons/app/spledio"`; Briefcase adds the
extension itself. The same LGPL steps (notices, source links, no Addons,
no FFmpeg) apply. Whether Briefcase's Python 3.14 support package and
PySide6 6.11 work together is **not verified**.

## Windows (NOT YET TESTED)

- PyInstaller onedir with
  `--icon src/spledio/resources/icons/app/spledio.ico --windowed`, run from
  a Windows venv created by `scripts/setup.bat` (also untested).
- The Windows `sounddevice` wheel ships both a normal and an ASIO-enabled
  PortAudio DLL. **Bundle only the non-ASIO DLL** unless the Steinberg ASIO
  SDK terms have been verified as GPL-3.0-compatible.
- Windows numpy/scipy wheels normally bundle OpenBLAS and a GCC runtime, so
  their `LICENSE.txt` "bundled libraries" sections then apply. **Not
  verified**: the macOS wheels here have no OpenBLAS.
- The redistribution terms and notices for the MSVC runtime
  (`vcruntime140*.dll`) are **not verified**.
- Sign with Authenticode (`signtool sign /fd sha256 /tr <timestamp-url> /td sha256`).
  Use an Inno Setup or MSIX installer that also installs the license folder.

## Linux (NOT YET TESTED)

- PyInstaller onedir, then optionally AppImage. Flatpak with the KDE
  runtime is an alternative that gets Qt from the runtime.
- `sounddevice` on Linux does **not** bundle PortAudio, so the system
  package (`libportaudio2`) is required. Whether the Linux `soundfile`
  wheel bundles libsndfile is **not verified**.
- The Qt xcb platform plugin needs system X11/xcb libraries (for example
  `libxcb-cursor0`). Wayland needs Qt's wayland plugins. Both are
  **untested**.
- Desktop entry: `Name=Spledio`, `Exec=spledio %f`, `Icon=spledio` (PNG
  sizes from `src/spledio/resources/icons/app/png/`), and
  `StartupWMClass` matching `QApplication.setDesktopFileName("org.spledio.Spledio")`.
