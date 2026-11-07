# Source code and replacing libraries

Spledio is free software under the **GNU General Public License version 3 only**
(GPL-3.0-only, see `LICENSE`). Its app for macOS also contains libraries under the
GNU Lesser General Public License (LGPL). Both licenses give you the right to get the
source code and to use the app with modified versions of those libraries. This file tells
you where the source code is and how to swap a library.

## Spledio's source code

Each release on <https://github.com/iacompa/Spledio/releases> offers, next to
`Spledio-macOS-arm64.dmg`:

* `Spledio-<version>-source.tar.gz`: the complete source of that version, made with
  `git archive` from the exact commit the app was built from (the release script refuses to
  make a signed release from uncommitted changes);
* `third-party-sources/`: the unmodified source archives of the LGPL libraries listed below,
  with a `SHA256SUMS` file (made by `scripts/fetch_lgpl_sources.sh`).

The same source is in the repository, <https://github.com/iacompa/Spledio>, under the tag
`v<version>`. `docs/RELEASE_MACOS.md` explains how the app is built from it
(`scripts/release_macos.sh`).

## Libraries under the LGPL inside Spledio.app

Spledio does not change any of these libraries. Each is a separate file inside the app, loaded
when the app starts. Paths are relative to `Spledio.app/Contents/Frameworks/`.

| Library | Version | License | File(s) in the app | Source code |
|---|---|---|---|---|
| Qt (QtCore, QtGui, QtWidgets, QtDBus, QtSvg, and the cocoa, minimal, offscreen, macstyle, svg and svg-icon plugins) | 6.11.3 | LGPL-3.0-only | `PySide6/Qt/lib/*.framework`, `PySide6/Qt/plugins/` | `qtbase-everywhere-src-6.11.3.tar.xz` and `qtsvg-everywhere-src-6.11.3.tar.xz` from <https://download.qt.io/official_releases/qt/6.11/6.11.3/submodules/> |
| PySide6 and shiboken6 (Qt for Python) | 6.11.3 | LGPL-3.0-only | `PySide6/libpyside6.abi3.6.11.dylib`, `PySide6/*.abi3.so`, `shiboken6/libshiboken6.abi3.6.11.dylib`, `shiboken6/Shiboken.abi3.so` | `pyside-setup-everywhere-src-6.11.3.tar.xz` from <https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.11.3-src/> |
| libsndfile | 1.2.2 | LGPL-2.1 | `_soundfile_data/libsndfile_arm64.dylib` | `libsndfile-1.2.2.tar.xz` from <https://github.com/libsndfile/libsndfile/releases/tag/1.2.2> |
| mpg123 (MP3 decoding), linked into libsndfile | 1.32.3 | LGPL-2.1 | same file | `mpg123-1.32.3.tar.bz2` from <https://mpg123.de/download/> |
| LAME (MP3 encoding), linked into libsndfile | 3.100 | LGPL-2.0 | same file | `lame-3.100.tar.gz` from <https://sourceforge.net/projects/lame/files/lame/3.100/> (LAME's site: www.mp3dev.org) |
| libquadmath (part of GCC; comes with SciPy) | from GCC 13, exact release not determined | LGPL-2.1 | `libquadmath.0.dylib` | GCC source: <https://gcc.gnu.org/>. See the note below. |

`libsndfile_arm64.dylib` also contains libFLAC 1.4.3, libogg 1.3.5, libvorbis 1.3.7 and
libopus 1.4 (BSD-3-Clause). Their source archives are offered too, with the script that built
the library: `libsndfile-binaries` at commit `804500c476f9f8059b25eafafbe97593ee16452e`
(<https://github.com/bastibe/libsndfile-binaries>, file `mac_build.sh`).

**libquadmath note.** SciPy 1.17.1's wheel for macOS 14+ (the one in the app) brings
`libquadmath`, `libgfortran` and `libgcc_s`. According to SciPy's build workflow
(`.github/workflows/wheels.yml` at tag `v1.17.1`), these come from the `gfortran-13`
preinstalled on GitHub's `macos-14` build machines. The exact GCC 13 release could not be read
from the files. The GCC sources are at <https://gcc.gnu.org/> (Apple Silicon support is in
<https://github.com/iains/gcc-13-branch>). `libgfortran` and `libgcc_s` are under
GPL-3.0-or-later with the GCC Runtime Library Exception 3.1 (text in `scipy-LICENSE.txt`).

The other components (Python, NumPy, SciPy, PortAudio, OpenSSL, zstd, xz, cffi, pycparser,
soundfile, sounddevice) are under permissive licenses; their notices are in this folder.

## Using a modified library

You can replace any of the libraries above with your own build. You need an Apple Silicon Mac
and the Xcode command-line tools (`xcode-select --install`).

1. **Build your version** for arm64, macOS 15 or later, with the same file name. It must stay
   compatible with the one it replaces: for Qt, any Qt 6.11.x (PySide6 6.11.3 needs Qt 6.11);
   for libsndfile, a 1.x release. The build recipe linked above shows how the shipped
   libsndfile was made. Qt's build instructions are at <https://doc.qt.io/qt-6/build-sources.html>.
2. **Work on a copy** of the app:

   ```bash
   cp -R /Applications/Spledio.app ~/Spledio-custom.app
   ```

3. **Replace the file.** For example, libsndfile:

   ```bash
   cp ~/build/libsndfile.dylib ~/Spledio-custom.app/Contents/Frameworks/_soundfile_data/libsndfile_arm64.dylib
   ```

   Or QtGui: `~/Spledio-custom.app/Contents/Frameworks/PySide6/Qt/lib/QtGui.framework/Versions/A/QtGui`.
4. **Sign the copy on your own Mac.** Changing a file breaks the app's signature, and macOS does
   not run code on Apple Silicon without one. These commands replace the developer's signature
   with a local ("ad hoc") one that only your Mac trusts. They also turn off the hardened
   runtime, so the app accepts your library:

   ```bash
   codesign --force --sign - ~/Spledio-custom.app/Contents/Frameworks/_soundfile_data/libsndfile_arm64.dylib
   codesign --force --deep --sign - ~/Spledio-custom.app
   xattr -dr com.apple.quarantine ~/Spledio-custom.app
   ```

   The first command signs the file you replaced (use its path). The last one only matters for
   a copy of a downloaded app.
5. **Check it**: `~/Spledio-custom.app/Contents/MacOS/Spledio --smoke-test some-audio.wav`
   prints each step it runs: open a file, render the picture, play through the sound output,
   and more. Then open `~/Spledio-custom.app` as usual.

You can also rebuild the whole app from Spledio's source with your libraries
(`docs/RELEASE_MACOS.md`). If the app does not start, run
`~/Spledio-custom.app/Contents/MacOS/Spledio` in Terminal to see the error. "Library not
loaded" or "symbol not found" means your build is not compatible with the one it replaced.
