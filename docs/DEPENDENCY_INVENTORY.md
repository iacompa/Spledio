# Dependency inventory

Spledio is **GPL-3.0-only**. This inventory was read from the verified
environment (`.venv`, CPython 3.14.7, macOS 27.0.1 arm64) on 2026-11-06 with
`pip show -v`, `importlib.metadata`, the `*.dist-info/` license files,
`otool -L` and `strings`. Copyright lines and license files are listed in
[`THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md), and copies of the license
texts are in [`licenses/`](../licenses/). **No installer has been built yet**,
so the redistribution obligations below are requirements for a future bundle,
not a description of an existing one.

## Inventory

| Package | Version | Role | License (SPDX) | Bundled native libraries | GPL-3.0-only compatible? | Redistribution obligations |
|---|---|---|---|---|---|---|
| PySide6 | 6.11.3 | runtime (meta-package) | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only | none of its own (depends on the three below) | **Yes.** Spledio uses it under LGPL-3.0, and GPL-3.0 is also offered. | See [Qt / PySide6](#qt--pyside6-lgpl-30-obligations-when-distributing-a-bundled-app) |
| PySide6_Essentials | 6.11.3 | runtime | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only | Qt 6.11.3 frameworks (QtCore, QtGui, QtWidgets, QtDBus, QtSvg, QtQml, QtQuick, ...), Qt plugins, `libpyside6` | **Yes.** LGPL-3.0 libraries linked dynamically. A few GPL-3.0-only frameworks are in the wheel; they are also compatible but unused. | LGPL-3.0: license texts, notice, corresponding source (or a written offer), Qt libraries the user can replace |
| PySide6_Addons | 6.11.3 | installed, **not used** (pulled in by `PySide6`) | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only (wheel metadata); individual Qt modules are GPL-3.0-only (see below) | Qt add-on frameworks incl. QtWebEngine (Chromium), QtPdf, QtMultimedia, **FFmpeg n7.1.5** (LGPL-2.1-or-later build) | **Yes.** Everything in it is LGPL or GPL-3.0. | **Exclude from bundles.** Spledio imports none of these modules. |
| shiboken6 | 6.11.3 | runtime | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only | `libshiboken6.abi3.6.11.dylib` | **Yes.** LGPL-3.0. | Same as PySide6 |
| numpy | 2.4.6 | runtime | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | **none** (links the system Accelerate framework) | **Yes.** Permissive licenses only. | Keep the copyright notices and license texts |
| scipy | 1.17.1 | runtime | BSD-3-Clause | `libgfortran.5`, `libgcc_s.1.1` (GPL-3.0-or-later WITH GCC-exception-3.1); `libquadmath.0` (LGPL-2.1-or-later). **No OpenBLAS** (uses Accelerate). | **Yes.** BSD-3-Clause is permissive. The GCC Runtime Library Exception allows combination, and LGPL-2.1+ is compatible. | Keep scipy `LICENSE.txt` (it contains the GCC exception and the GPL-3.0 text). libquadmath: LGPL-2.1 text plus a source offer or link (GCC sources), as a replaceable dylib. |
| soundfile | 0.13.1 | runtime | BSD-3-Clause | `libsndfile_arm64.dylib` (libsndfile 1.2.2 with libFLAC 1.4.3, libogg, libvorbis 1.3.7, libopus 1.4, mpg123 1.32.3, LAME 3.100 statically linked) | **Yes.** BSD-3-Clause, and libsndfile is LGPL. | BSD notice; see [libsndfile](#libsndfile-lgpl-21-obligations) |
| sounddevice | 0.5.5 | runtime | MIT | `libportaudio.dylib` (PortAudio V19.7.0-devel, universal) | **Yes.** MIT. | MIT notice; see [PortAudio](#portaudio) |
| cffi | 2.1.1 | runtime (only `_cffi_backend` is loaded) | MIT-0 | none (links system `/usr/lib/libffi.dylib`) | **Yes.** MIT-0 is permissive. | None required by MIT-0. The text is included anyway. |
| pycparser | 3.12 | runtime dependency of cffi; **not loaded at runtime** (soundfile/sounddevice ship pre-generated `_soundfile.py`/`_sounddevice.py`, and importing both did not import `cffi` or `pycparser`) | BSD-3-Clause | none | **Yes.** Permissive. | Keep the notice if shipped |
| CPython | 3.14.7 (Homebrew build) | bundle only (frozen interpreter) | PSF-2.0 | a bundler may collect Homebrew `libcrypto` (OpenSSL 3.6.4), `liblzma`, `libmpdec`, `libsqlite3` | **Yes.** PSF-2.0 is GPL-compatible. The bundled-library licenses are **not verified**. | Include `licenses/CPython-3.14-LICENSE.txt` plus notices for every collected dylib |
| pytest | 9.0.3 | dev | MIT | none | Yes (not distributed) | none; not shipped |
| pytest-qt | 4.5.0 | dev | MIT | none | Yes (not distributed) | none; not shipped |
| pluggy | 1.7.0 | dev | MIT | none | Yes (not distributed) | none; not shipped |
| iniconfig | 2.3.1 | dev | MIT | none | Yes (not distributed) | none; not shipped |
| packaging | 26.3 | dev | Apache-2.0 OR BSD-2-Clause | none | Yes (not distributed) | none; not shipped |
| Pygments | 2.21.0 | dev | BSD-2-Clause | none | Yes (not distributed) | none; not shipped |
| typing_extensions | 4.16.0 | dev, transitive (required by pytest-qt), **not pinned** in `requirements-dev.txt` | PSF-2.0 | none | Yes (not distributed) | none; not shipped |

`pip` 26.3 in `.venv` is tooling. It is not a dependency and must not be bundled.

Optional dependencies: none are declared or installed. `pyproject.toml` has only the `dev` extra.

## Why GPL-3.0-only plus LGPL/BSD/MIT dependencies is compatible

- **Permissive licenses** (BSD-2/3-Clause, MIT, MIT-0, 0BSD, Zlib, CC0-1.0,
  NCSA, PSF-2.0, Apache-2.0) only ask you to keep notices; Apache-2.0 adds a
  patent clause. None adds restrictions beyond what GPL-3.0 allows. The FSF
  lists all of them as GPL-3.0-compatible, and Apache-2.0 is compatible with
  GPL-3.0 but not with GPL-2.0.
- **LGPL-2.1 / LGPL-2.1-or-later / LGPL-3.0** libraries can be combined with
  a GPL program. The combined work is distributed under the GPL, and the
  library keeps its LGPL terms, including relinking and source. LGPL-2.1 §3
  even allows converting the library to the GPL.
- **GPL-3.0-or-later WITH GCC-exception-3.1** (libgfortran/libgcc_s): the
  exception allows combination with programs under any license, and this
  program is GPL-3.0 in any case.
- The result: a Spledio bundle is distributed as GPL-3.0-only for the
  combined work, while each component stays under its own license and its
  notices must be kept.

## Qt / PySide6 (LGPL-3.0) obligations when distributing a bundled app

### Qt modules Spledio actually uses (verified)

`grep -rn "PySide6\." src --include=*.py` finds exactly three modules:

| Module | Import lines in `src/` |
|---|---|
| `PySide6.QtCore` | 22 |
| `PySide6.QtGui` | 14 |
| `PySide6.QtWidgets` | 21 |

(The counts are from the final re-check on 2026-11-06. `src/ui/` was still
changing during this audit, so re-run the grep before each release.)

- **`PySide6.QtSvg` is *not* imported by the application.** It is imported
  only by the dev-time script `scripts/build_icons.py`, which renders the
  PNG/ICNS/ICO icons from SVG. At runtime, `ui/icons.py` draws its UI icons
  with QPainter. `app_icon()` loads the PNG set in
  `resources/icons/app/png/` and falls back to `QIcon("spledio-icon.svg")`
  only when no PNG is found. That fallback is the one runtime path that
  would need SVG support (the `imageformats/libqsvg` or
  `iconengines/libqsvgicon` plugin plus `QtSvg.framework`, both LGPL), so
  ship the PNGs. With the PNGs present, QtSvg was not loaded (checked
  offscreen).
- No `shiboken6` import in `src/`. The only `importlib.import_module` call
  (`ui/main_window.py`) loads Spledio's own panel modules.
- Importing every `spledio.*` module with no import failures, then creating
  a `QApplication` and the app icon, loaded these Qt binaries (checked
  through dyld): `QtCore`, `QtGui`, `QtWidgets` and **`QtDBus`** frameworks (QtGui
  links to QtDBus on macOS), plus `libpyside6` and `libshiboken6`. A real
  window also needs the `platforms/libqcocoa.dylib` plugin, and normally
  `styles/libqmacstyle.dylib`.
- `imageformats/libqsvg` and `iconengines/libqsvgicon` would pull in
  `QtSvg.framework` (LGPL). `imageformats/libqpdf.dylib` pulls in
  `QtPdf.framework` (Addons; based on Chromium's PDFium) and `QtNetwork`. Do
  not ship `libqpdf` unless it is needed.

### Obligations

From the LGPL-3.0 text and Qt's own summary
([Obligations of the GPL and LGPL](https://www.qt.io/development/open-source-lgpl-obligations),
[Qt Licensing](https://doc.qt.io/qt-6/licensing.html), read 2026-11-06).
Spledio is itself GPL-3.0, so its own source is already available, which
removes most of the usual friction.

1. **Link dynamically.** Ship Qt and PySide6 as the unmodified shared
   frameworks/dylibs from the wheel. Do not statically link or merge them
   into another binary. A PyInstaller `.app` (onedir) keeps them as separate
   files; avoid single-file modes that hide the libraries.
2. **Users must be able to replace the Qt libraries** and run the app with
   the replacement. In LGPLv3 terms, the bundle must be relinkable and enough
   "installation information" must be given. Document where the frameworks
   live in the bundle (`Contents/Frameworks/PySide6/Qt/lib/...`) and that
   re-signing with an ad-hoc signature (`codesign --force --deep -s -`) is
   required after swapping a library. Do not use code-signing or
   notarization in a way that prevents this; the user can always re-sign
   ad-hoc.
3. **Provide the license texts**: LGPL-3.0 and GPL-3.0 (LGPL-3.0 builds on
   GPL-3.0). **The wheels do not ship them** (no license file anywhere under
   `PySide6/`, `shiboken6/` or their dist-info), so add them from
   <https://www.gnu.org/licenses/>. Show a prominent notice that the app uses
   Qt under LGPL-3.0, for example in the About box and the bundled notices
   file.
4. **Offer the complete corresponding source** of the LGPL components you
   ship, even unmodified: Qt 6.11.3 and PySide6/shiboken6 6.11.3. Either ship
   it, or give a written offer or a stable link to the exact versions
   (<https://download.qt.io/official_releases/qt/6.11/6.11.3/> and
   <https://download.qt.io/official_releases/QtForPython/pyside6/>; check
   these URLs before release). Keeping a mirror next to Spledio's own source
   release is the most robust option.
5. **Include third-party notices for code inside Qt.** QtCore/QtGui contain
   PCRE2, libpng 1.6.59, FreeType, HarfBuzz, md4c and others; the
   imageformats plugins contain libtiff, libwebp and libjpeg-turbo. See
   [Licenses Used in Qt](https://doc.qt.io/qt-6/licenses-used-in-qt.html)
   (that page tracks current Qt, so versions may differ slightly from 6.11.3)
   and the Qt SBOM. FreeType's FTL option requires a credit in the
   documentation. **These texts are not in the wheel.**
6. **Ship only what is used.** Exclude PySide6_Addons modules, QtWebEngine,
   QtMultimedia, FFmpeg, the Qt tools (`Designer.app`, `Assistant.app`,
   `Linguist.app`, `qmlls`, `qmllint`, `qmlformat`, `lupdate`, `lrelease`,
   `balsam`, `svgtoqml`, `libexec/*`) and QML. Qt's tools are GPL-3.0 with
   the Qt GPL exception, not LGPL.

### Qt modules in the installed wheels that are not LGPL

Sources: [Qt Licensing](https://doc.qt.io/qt-6/licensing.html) lists "Modules
available under GNU General Public License v3". The
[Qt Charts](https://doc.qt.io/qt-6/qtcharts-index.html) and
[Qt Data Visualization](https://doc.qt.io/qt-6/qtdatavisualization-index.html)
pages say each is "available under commercial licenses ... In addition, it is
available under the GNU General Public License, version 3". The wheel
metadata gives the generic `LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only`
for all modules, so these per-module restrictions come from Qt's
documentation, not from the wheel.

| Qt module (GPL-3.0 or commercial, **not LGPL**) | Present in this venv as | Python module? |
|---|---|---|
| Qt Charts | `QtCharts.framework`, `QtChartsQml` (Addons) | `PySide6.QtCharts` |
| Qt Data Visualization | `QtDataVisualization*.framework` (Addons) | `PySide6.QtDataVisualization` |
| Qt Graphs | `QtGraphs.framework`, `QtGraphsWidgets.framework` (Addons) | `PySide6.QtGraphs`, `PySide6.QtGraphsWidgets` |
| Qt HTTP Server | `QtHttpServer.framework` (Addons) | `PySide6.QtHttpServer` |
| Qt Network Authorization | `QtNetworkAuth.framework` (Addons) | `PySide6.QtNetworkAuth` |
| Qt Quick 3D | `QtQuick3D*.framework` (14 frameworks, Addons) | `PySide6.QtQuick3D` |
| Qt Canvas Painter | `QtCanvasPainter.framework` (Addons) | `PySide6.QtCanvasPainter` |
| Qt Virtual Keyboard | `QtVirtualKeyboard*.framework` (Addons) | none |
| Qt Lottie Animation | `QtLottie*.framework` (**Essentials**) | none |
| Qt Quick Timeline | `QtQuickTimeline*.framework` (**Essentials**) | none |
| Qt Qml Compiler | `QtQmlCompiler.framework` (**Essentials**) | none |

- Not present: Qt CoAP, Qt GRPC, Qt MQTT, Qt Quick 3D Physics, Qt Wayland
  Compositor (GPL-only).
- Commercial-only modules (Qt Insight Tracker, Qt VNC Server, per
  [Qt modules](https://doc.qt.io/qt-6/qtmodules.html)) are **not present**.
- The license of `QtShaderTools`, the `QtLabs*` technology-preview frameworks
  and `QtQuickVectorImage*` is **not verified**.
- None of these are imported by Spledio. GPL-3.0 modules would even be
  compatible with a GPL-3.0-only app, but keeping them out keeps the Qt part
  of the bundle purely LGPL and much smaller. `PySide6/Qt/` is 1.0 GB;
  `QtWebEngineCore.framework` alone is 590 MB.

## libsndfile (LGPL-2.1) obligations

`soundfile` ships one self-contained `libsndfile_arm64.dylib` (libsndfile
1.2.2) with the codecs statically linked into it: libFLAC 1.4.3, libogg,
libvorbis 1.3.7, libopus 1.4, mpg123 1.32.3 and LAME 3.100. The wheel ships
only `_soundfile_data/COPYING` (LGPL-2.1 text). The codec licenses come from
the [libsndfile-binaries](https://github.com/bastibe/libsndfile-binaries)
README (BSD-3-Clause for FLAC/Ogg Vorbis/Opus, LGPL-2.1 for mpg123, LGPL v2
for LAME) and are **not verified locally**.

When distributing:

1. Keep `libsndfile_arm64.dylib` a **separate, replaceable dynamic library**
   (it is loaded through cffi `dlopen`, so this is the default), and say in
   the notices how to replace it.
2. Include the LGPL-2.1 text ([`licenses/libsndfile-LGPL-2.1.txt`](../licenses/libsndfile-LGPL-2.1.txt)),
   the LGPL-2.0 text for LAME, and the BSD-3-Clause notices for
   FLAC/Ogg/Vorbis/Opus. The upstream codec files are **missing**; obtain
   them first.
3. Provide or offer the corresponding source of libsndfile **and** of every
   LGPL library statically linked into it (mpg123, LAME), at the exact
   versions above, plus the build scripts. The libsndfile-binaries repository
   holds the build recipe. Check which commit matches soundfile 0.13.1 before
   release (**not verified**).
4. Static linking of mpg123/LAME into an LGPL library that is itself
   distributed as a replaceable dylib with source satisfies relinking: the
   user can rebuild the whole dylib.
5. Spledio itself imports only uncompressed/lossless formats and exports only
   WAV/FLAC, but the dylib contains all codecs, so all notices apply.

## FFmpeg: not used by Spledio, not to be bundled

- Spledio's code does not use FFmpeg. `grep -ri ffmpeg src docs` found no
  matches before this file existed, and no FFmpeg binary or library is called.
- **However, the `PySide6_Addons` wheel ships FFmpeg n7.1.5** shared
  libraries in `PySide6/Qt/lib/` (`libavcodec.61`, `libavformat.61`,
  `libavutil.59`, `libswresample.5`, `libswscale.8`; about 36 MB of unique
  code). They are used only by QtMultimedia's
  `plugins/multimedia/libffmpegmediaplugin.dylib`. Each library reports
  `license: LGPL version 2.1 or later`, and the embedded configure line has
  no `--enable-gpl`/`--enable-nonfree`. Spledio does not import QtMultimedia
  and dyld shows they are never loaded. **Exclude them from any bundle.**
  Shipping them would add LGPL-2.1 source obligations for FFmpeg and the
  patent questions of its codecs, for no benefit.
- A future optional FFmpeg integration (for example decoding MP3/AAC/video
  soundtracks) must use an **LGPL build** (no `--enable-gpl`, no
  `--enable-nonfree`) loaded as a separate replaceable shared library or
  called as an external program, ship its license and source offer, and
  document codec patent status. A GPL build would also be license-compatible
  with GPL-3.0-only, but an LGPL build keeps options open and matches the
  rest of the stack. `--enable-nonfree` builds are **not redistributable**.

## PortAudio

- `sounddevice` ships `_sounddevice_data/portaudio-binaries/libportaudio.dylib`
  (PortAudio `V19.7.0-devel, revision unknown`, universal x86_64/arm64). It
  links only macOS system audio frameworks.
- License: MIT. The only notice in the wheel is the README line "PortAudio
  by Ross Bencina and Phil Burk, MIT License." **The full license text is not
  shipped.** Add upstream
  [LICENSE.txt](https://github.com/PortAudio/portaudio/blob/master/LICENSE.txt)
  (`Copyright (c) 1999-2006 Ross Bencina and Phil Burk`) to a distribution.
  Upstream also makes a non-binding request that modifications be sent back.
- **Windows (untested):** the sounddevice Windows wheel also ships an
  ASIO-enabled PortAudio DLL (`*-asio.dll`; the README credits the Steinberg
  ASIO SDK). The ASIO SDK's license terms are **not verified** here. Ship
  only the non-ASIO DLL unless they have been checked for GPL-3.0
  compatibility.

## Other native code

- **Apple system frameworks** (Accelerate, CoreAudio, AppKit, ...) and
  `/usr/lib/libffi`, `libSystem`, `libc++`, `libz` are part of macOS. They
  are not redistributed and fall under the GPL "System Libraries" exception.
- **GCC runtime in scipy**: `libgfortran.5` and `libgcc_s.1.1` are
  GPL-3.0-or-later WITH GCC-exception-3.1; `libquadmath.0` is
  LGPL-2.1-or-later. `scipy.interpolate` (`_fitpack`, `_dfitpack`) and
  `scipy.odr` link them. dyld showed them loaded after Spledio's modules
  were imported, so a bundle needs them. Keep them as separate dylibs,
  keep scipy's `LICENSE.txt`, and give a link to the GCC source for
  libquadmath (the exact GCC version is **not verified**).
