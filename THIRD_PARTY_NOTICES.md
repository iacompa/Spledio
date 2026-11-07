# Third-party notices

Spledio's own code is licensed under **GPL-3.0-only**; the full text is in
[`LICENSE`](LICENSE). The app icon, small icon, mono mark and wordmark in
`src/spledio/resources/icons/app/` are original artwork drawn for this
project, contain no third-party glyphs or images, and are licensed with the
project under **GPL-3.0-only** (see [`docs/BRANDING.md`](docs/BRANDING.md)).

This file lists the third-party software Spledio uses, at the exact versions
pinned in `requirements.txt` / `requirements-dev.txt`. Every fact below was
read on 2026-11-06 from the verified environment (`.venv`, CPython 3.14.7,
macOS 27.0.1 arm64) using `pip show -v`, `importlib.metadata`, the files in
each `*.dist-info/`, the bundled native-library folders, `otool -L` and
`strings`. Anything that could not be read from a local file is marked
**not verified**. License files under [`licenses/`](licenses/) are
byte-for-byte copies of the files named in the "Source" column of the
[checksum table](#license-file-sources-and-checksums), except the few marked
there as converted, extracted or written for Spledio. Files added on 2026-11-07 for
the packaged app were compared byte for byte with the upstream release archives or
the tagged files in the upstream repositories.

In the app, all of these files are in `Spledio.app/Contents/Resources/licenses/`
and can be read with **Help ▸ Licenses and Notices**. The About box carries the
credits that LAME, mpg123 and the FreeType License ask for. Where to get the
source code of the LGPL libraries, and how to replace them, is in
[`licenses/SOURCE-AND-REPLACING-LIBRARIES.md`](licenses/SOURCE-AND-REPLACING-LIBRARIES.md).

No AI models are bundled or downloaded (see
[`docs/MODEL_LICENSES.md`](docs/MODEL_LICENSES.md)). Spledio does not use
FFmpeg. The `PySide6_Addons` wheel does contain FFmpeg 7.1.5 shared libraries
for QtMultimedia, but Spledio never loads them, and a distributed bundle must
leave them out (see below).

## Runtime dependencies

### PySide6 6.11.3, PySide6_Essentials 6.11.3, PySide6_Addons 6.11.3, shiboken6 6.11.3

| | |
|---|---|
| License (metadata `License:`) | `LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only` (all four wheels) |
| License (SPDX header in shipped `.pyi`/`.py` files) | `LicenseRef-Qt-Commercial OR LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only` |
| License Spledio uses | **LGPL-3.0-only**. Spledio could also use GPL-3.0-only, which matches its own license. |
| Copyright as shipped | `Copyright (C) 2022 The Qt Company Ltd.` (headers of `PySide6/*.pyi`, `shiboken6/__init__.py`) |
| License file under `licenses/` | [`licenses/LGPL-3.0.txt`](licenses/LGPL-3.0.txt) (from <https://www.gnu.org/licenses/lgpl-3.0.txt>). The LGPL-3.0 is a set of additional permissions on top of the GPL-3.0, whose text is Spledio's own [`LICENSE`](LICENSE) (identical to <https://www.gnu.org/licenses/gpl-3.0.txt>). The wheels themselves ship no license file: the dist-info folders have no `licenses/` and there is no LICENSE/COPYING anywhere under `PySide6/` or `shiboken6/`. |
| Bundled native libraries | Qt 6.11.3 frameworks (`PySide6/Qt/lib/*.framework`, 155 entries), Qt plugins (`PySide6/Qt/plugins`), `libpyside6.abi3.6.11.dylib`, `libpyside6qml.abi3.6.11.dylib`, `shiboken6/libshiboken6.abi3.6.11.dylib`. Qt reports itself as a "shared (dynamic) release build". The Addons wheel also ships **FFmpeg n7.1.5** (`PySide6/Qt/lib/libavcodec.61`, `libavformat.61`, `libavutil.59`, `libswresample.5`, `libswscale.8`). Each reports `... license: LGPL version 2.1 or later`, and the configure string has no `--enable-gpl`/`--enable-nonfree`. Only `Qt/plugins/multimedia/libffmpegmediaplugin.dylib` uses them. Spledio does not import QtMultimedia, does not load them (checked with dyld), and must **not** bundle them. |
| Qt modules Spledio loads | QtCore, QtGui, QtWidgets, plus QtDBus (QtGui links to it on macOS). See [`docs/DEPENDENCY_INVENTORY.md`](docs/DEPENDENCY_INVENTORY.md#qt--pyside6-lgpl-30-obligations-when-distributing-a-bundled-app). |
| Third-party code inside Qt | [`licenses/Qt-6.11.3-third-party-notices.md`](licenses/Qt-6.11.3-third-party-notices.md) lists every third-party component compiled into the Qt binaries that ship (QtCore, QtGui, QtDBus, QtSvg, the cocoa plugin) with version, SPDX license and copyright lines, from the `qt_attribution.json` files of qtbase and qtsvg at tag v6.11.3. Each component was checked against the binaries: for example FreeType 2.15.0, HarfBuzz 14.6.0, libpng 1.6.59, PCRE2 10.49, MD4C 0.6.0, double-conversion 3.4.0, TinyCBOR 7.0 and Unicode CLDR v48.2 were found in them. The file also lists what is *not* shipped and why: zlib (macOS's system zlib is used), libjpeg/libtiff/libwebp (plugins removed), libpsl (QtNetwork removed), SQLite, Vulkan and PCRE2's JIT. The 18 license texts it refers to are the unmodified `LICENSES/*.txt` files from qtbase/qtsvg v6.11.3 (`licenses/Qt-6.11.3-*.txt`). For dual-licensed parts, Spledio uses FTL for FreeType, AFL-2.1 for the libdbus-1 headers and CC0-1.0 for BLAKE2. The FreeType License requires a credit in the documentation; it is in the About box ("based in part on the work of the FreeType Team"). `libshiboken6` also contains code from Python 3.7.0 under the PSF License Agreement ([`licenses/PySide6-6.11.3-PSF-3.7.0.txt`](licenses/PySide6-6.11.3-PSF-3.7.0.txt), from pyside-setup v6.11.3). |

### numpy 2.4.6

| | |
|---|---|
| License (`License-Expression`) | `BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0` |
| Copyright as shipped | `Copyright (c) 2005-2025, NumPy Developers.` |
| License files | [`licenses/numpy-LICENSE.txt`](licenses/numpy-LICENSE.txt) (main) and the component files below |
| Bundled native libraries | **None in this wheel.** The tag is `cp314-cp314-macosx_14_0_arm64`, there is no `numpy/.dylibs` or `numpy.libs`, and `otool -L` shows links only to `/System/Library/Frameworks/Accelerate.framework`, `libSystem` and `libc++`. These are macOS system libraries, not bundled. The "This binary distribution of NumPy also bundles..." section of `LICENSE.txt` (OpenBLAS, LAPACK, GCC runtime, libquadmath) is generic text and **does not apply to this wheel**. |

Component license files that ship in `numpy-2.4.6.dist-info/licenses/` (the source code for these is compiled into numpy):

| Component | License (as stated in the file) | Copyright line(s) as shipped | File |
|---|---|---|---|
| libdivide | Zlib | `Copyright (C) 2010 - 2019 ridiculous_fish, <libdivide@ridiculousfish.com>`; `Copyright (C) 2016 - 2019 Kim Walisch, <kim.walisch@gmail.com>` | [`numpy-libdivide-LICENSE.txt`](licenses/numpy-libdivide-LICENSE.txt) |
| pythoncapi-compat | 0BSD | `Copyright Contributors to the pythoncapi_compat project.` | [`numpy-pythoncapi-compat-COPYING.txt`](licenses/numpy-pythoncapi-compat-COPYING.txt) |
| Highway | Apache-2.0 OR BSD-3-Clause (one file under CC0-1.0) | see file | [`numpy-highway-LICENSE.txt`](licenses/numpy-highway-LICENSE.txt) |
| Dragon4 | MIT | `Copyright (c) 2014 Ryan Juckett` | [`numpy-dragon4-LICENSE.txt`](licenses/numpy-dragon4-LICENSE.txt) |
| x86-simd-sort | BSD-3-Clause | `Copyright (c) 2022, Intel. All rights reserved.` | [`numpy-x86-simd-sort-LICENSE.md`](licenses/numpy-x86-simd-sort-LICENSE.md) |
| SVML | BSD-3-Clause | `Copyright (c) 2005-2021, NumPy Developers.` | [`numpy-svml-LICENSE.txt`](licenses/numpy-svml-LICENSE.txt) |
| pocketfft | BSD-3-Clause | `Copyright (C) 2010-2018 Max-Planck-Society` | [`numpy-pocketfft-LICENSE.md`](licenses/numpy-pocketfft-LICENSE.md) |
| lapack_lite | BSD-3-Clause (LAPACK) | `Copyright (c) 1992-2013 The University of Tennessee and The University of Tennessee Research Foundation.`; `Copyright (c) 2000-2013 The University of California Berkeley.`; `Copyright (c) 2006-2013 The University of Colorado Denver.` | [`numpy-lapack_lite-LICENSE.txt`](licenses/numpy-lapack_lite-LICENSE.txt) |
| numpy.ma | BSD-3-Clause | `Copyright (c) 2006, University of Georgia and Pierre G.F. Gerard-Marchant` | [`numpy-ma-LICENSE.txt`](licenses/numpy-ma-LICENSE.txt) |
| numpy.random | NCSA OR BSD-3-Clause | see file | [`numpy-random-LICENSE.md`](licenses/numpy-random-LICENSE.md) |
| random distributions | BSD-3-Clause and others | `Copyright (c) 2005-2017, NumPy Developers.` and others in file | [`numpy-random-distributions-LICENSE.md`](licenses/numpy-random-distributions-LICENSE.md) |
| MT19937 | BSD-3-Clause | `Copyright (c) 2003-2005, Jean-Sebastien Roy (js@jeannot.org)`; `Copyright (C) 1997 - 2002, Makoto Matsumoto and Takuji Nishimura` | [`numpy-random-mt19937-LICENSE.md`](licenses/numpy-random-mt19937-LICENSE.md) |
| PCG64 | MIT | `Copyright 2014 Melissa O'Neill <oneill@pcg-random.org>` | [`numpy-random-pcg64-LICENSE.md`](licenses/numpy-random-pcg64-LICENSE.md) |
| Philox | BSD-3-Clause | `Copyright 2010-2012, D. E. Shaw Research.` | [`numpy-random-philox-LICENSE.md`](licenses/numpy-random-philox-LICENSE.md) |
| SFC64 | MIT | `Copyright (c) 2018 Melissa E. O'Neill` | [`numpy-random-sfc64-LICENSE.md`](licenses/numpy-random-sfc64-LICENSE.md) |
| SplitMix64 | public-domain dedication (CC0-1.0 in the expression) | none (dedicated to the public domain) | [`numpy-random-splitmix64-LICENSE.md`](licenses/numpy-random-splitmix64-LICENSE.md) |

### scipy 1.17.1

| | |
|---|---|
| License | BSD-3-Clause. The metadata `License:` field contains the full license text, and the Classifier is `License :: OSI Approved :: BSD License`. |
| Copyright as shipped | `Copyright (c) 2001-2002 Enthought, Inc. 2003, SciPy Developers.` |
| License file | [`licenses/scipy-LICENSE.txt`](licenses/scipy-LICENSE.txt). It contains scipy's BSD text, the bundled-library notices, the GCC Runtime Library Exception 3.1 and the full GPL-3.0 text. |
| Bundled native libraries (`scipy/.dylibs/`) | `libgfortran.5.dylib` and `libgcc_s.1.1.dylib`: GCC runtime, `GPL-3.0-or-later WITH GCC-exception-3.1`, `Copyright (C) 2002-2017 Free Software Foundation, Inc.` `libquadmath.0.dylib`: `LGPL-2.1-or-later`, `Copyright (C) 2010-2019 Free Software Foundation, Inc.` The scipy file gives only a URL for the LGPL-2.1 text; the same text is in [`licenses/libsndfile-LGPL-2.1.txt`](licenses/libsndfile-LGPL-2.1.txt). |
| Not present | **OpenBLAS / LAPACK are not bundled** in this wheel (`cp314-cp314-macosx_14_0_arm64`): there is no `libscipy_openblas*`, and BLAS/LAPACK come from the system Accelerate framework. The OpenBLAS sections in `LICENSE.txt` are generic ("can also bundle ... depending on the build"). `libgfortran` is linked by `scipy.interpolate` (`_fitpack`, `_dfitpack`) and `scipy.odr`, and these were loaded when Spledio's modules were imported. |

### soundfile 0.13.1 (python-soundfile)

| | |
|---|---|
| License | BSD-3-Clause (metadata: `BSD 3-Clause License`) |
| Copyright as shipped | `Copyright (c) 2013, Bastian Bechtold` |
| License file | [`licenses/soundfile-LICENSE.txt`](licenses/soundfile-LICENSE.txt) |
| Bundled native library | `_soundfile_data/libsndfile_arm64.dylib`. It links only `libSystem`, so all codecs are statically linked inside it. See the next section. |

### libsndfile 1.2.2 (bundled in the soundfile wheel) and the codecs linked into it

| Library | Version (from strings in the binary) | License | Copyright | License file |
|---|---|---|---|---|
| libsndfile | 1.2.2 (`sf.__libsndfile_version__`) | **LGPL-2.1**: `_soundfile_data/COPYING` is the LGPL 2.1 text. "Or later" comes from upstream source headers and is **not verified locally**. | Not in shipped files. [libsndfile-binaries](https://github.com/bastibe/libsndfile-binaries) README: Erik de Castro Lopo (**not verified locally**) | [`licenses/libsndfile-LGPL-2.1.txt`](licenses/libsndfile-LGPL-2.1.txt) |
| libFLAC | 1.4.3 (`reference libFLAC 1.4.3 20230623`) | BSD-3-Clause (`COPYING.Xiph` at tag 1.4.3) | `Copyright (C) 2000-2009  Josh Coalson`, `Copyright (C) 2011-2023  Xiph.Org Foundation` | [`licenses/libFLAC-1.4.3-COPYING.Xiph.txt`](licenses/libFLAC-1.4.3-COPYING.Xiph.txt) |
| libogg | 1.3.5. The binary has no version string; 1.3.5 is the version that libsndfile-binaries' `mac_build.sh` builds at commit `804500c4`, the recipe that also gives the FLAC 1.4.3 and Opus 1.4 found in the binary. | BSD-3-Clause (`COPYING`) | `Copyright (c) 2002, Xiph.org Foundation` | [`licenses/libogg-1.3.5-COPYING.txt`](licenses/libogg-1.3.5-COPYING.txt) |
| libvorbis / libvorbisenc | 1.3.7 (`Xiph.Org libVorbis 1.3.7`) | BSD-3-Clause (`COPYING`) | `Copyright (c) 2002-2020 Xiph.org Foundation` | [`licenses/libvorbis-1.3.7-COPYING.txt`](licenses/libvorbis-1.3.7-COPYING.txt) |
| libopus | 1.4 (`libopus 1.4`) | BSD-3-Clause (`COPYING`, with Opus' patent notes) | `Copyright 2001-2011 Xiph.Org, Skype Limited, Octasic, ...` (see file) | [`licenses/libopus-1.4-COPYING.txt`](licenses/libopus-1.4-COPYING.txt) |
| libmpg123 (MP3 decode) | 1.32.3 (next to `src/libmpg123/` strings) | **LGPL-2.1** (`COPYING`: files without their own note are LGPL 2.1) | `Copyright (c) 1995-2020 by Michael Hipp and others` (the "main message" that `COPYING` asks to show in About boxes; it is in Spledio's About box) | [`licenses/mpg123-1.32.3-COPYING.txt`](licenses/mpg123-1.32.3-COPYING.txt), [`licenses/mpg123-1.32.3-AUTHORS.txt`](licenses/mpg123-1.32.3-AUTHORS.txt) |
| LAME (MP3 encode) | 3.100 (`LAME3.100`) | **LGPL-2.0** (`COPYING` is the GNU Library GPL version 2). LAME's `LICENSE` asks programs to acknowledge LAME and link to www.mp3dev.org; the About box does. | `Copyright (C) 1991 Free Software Foundation` (license text); authors in LAME's source | [`licenses/LAME-3.100-COPYING.txt`](licenses/LAME-3.100-COPYING.txt), [`licenses/LAME-3.100-LICENSE.txt`](licenses/LAME-3.100-LICENSE.txt) |

libsndfile also has internal codecs (ALAC, GSM 6.10, G.72x, IMA/MS ADPCM; the ALAC and GSM subtypes are listed by `soundfile.available_subtypes()`). Their individual notices are **not verified locally**.

Spledio rejects MP3/Ogg/Opus on import and exports only WAV and FLAC, but **redistributing `libsndfile_arm64.dylib` redistributes all of the codecs above**, so all their license files ship with the app. The source archives of libsndfile and the codecs, and the build recipe, are offered with each release (`scripts/fetch_lgpl_sources.sh`, [`licenses/SOURCE-AND-REPLACING-LIBRARIES.md`](licenses/SOURCE-AND-REPLACING-LIBRARIES.md)).

### sounddevice 0.5.5 (python-sounddevice)

| | |
|---|---|
| License (`License-Expression`) | MIT |
| Copyright as shipped | `Copyright (c) 2015-2025 Matthias Geier` |
| License file | [`licenses/sounddevice-LICENSE.txt`](licenses/sounddevice-LICENSE.txt) |
| Bundled native library | `_sounddevice_data/portaudio-binaries/libportaudio.dylib` (universal x86_64 + arm64). See PortAudio below. |

### PortAudio V19.7.0-devel (bundled in the sounddevice wheel)

| | |
|---|---|
| Version | `PortAudio V19.7.0-devel, revision unknown` (strings in the binary) |
| License | MIT, per the only shipped notice: `PortAudio by Ross Bencina and Phil Burk, MIT License.` (portaudio-binaries `README.md`) |
| Copyright | Shipped: "PortAudio by Ross Bencina and Phil Burk". Upstream [LICENSE.txt](https://github.com/PortAudio/portaudio/blob/master/LICENSE.txt): `Copyright (c) 1999-2006 Ross Bencina and Phil Burk` (web, **not in the wheel**). |
| License file | [`licenses/PortAudio-19.7.0-LICENSE.txt`](licenses/PortAudio-19.7.0-LICENSE.txt) (upstream `LICENSE.txt` at tag v19.7.0) and [`licenses/portaudio-binaries-README.md`](licenses/portaudio-binaries-README.md) (the only notice in the wheel). The binary says "revision unknown", so the exact PortAudio commit is not known; the license is the same MIT text. |
| Links to | macOS system frameworks only (CoreAudio, AudioToolbox, AudioUnit, CoreFoundation, CoreServices) |

### cffi 2.1.1

| | |
|---|---|
| License (`License-Expression`) | MIT-0 |
| Copyright as shipped | none: the LICENSE file is "MIT No Attribution" text without a copyright line. Authors in the metadata: Armin Rigo, Maciej Fijalkowski. |
| License file | [`licenses/cffi-LICENSE.txt`](licenses/cffi-LICENSE.txt) |
| Native | `_cffi_backend.cpython-314-darwin.so` links the **system** `/usr/lib/libffi.dylib`; no libffi is bundled. |

### pycparser 3.12

| | |
|---|---|
| License (`License-Expression`) | BSD-3-Clause |
| Copyright as shipped | `Copyright (c) 2008-2027, Eli Bendersky` (the year range is reproduced as shipped) |
| License file | [`licenses/pycparser-LICENSE.txt`](licenses/pycparser-LICENSE.txt) |

## Python runtime (only for a bundled, frozen build)

A frozen app (PyInstaller or similar) also redistributes the interpreter. Release builds use
**python.org CPython 3.14.10** (`scripts/get_build_python.sh`); its `LICENSE.txt` is identical to
[`licenses/CPython-3.14-LICENSE.txt`](licenses/CPython-3.14-LICENSE.txt). Development and the tests use
Homebrew CPython 3.14.7.

| Component | License | Copyright as shipped | File |
|---|---|---|---|
| CPython 3.14.10 (in the app; 3.14.7 for development) | PSF-2.0 (Python Software Foundation License; see file for history and CNRI/CWI terms) | `Copyright (c) 2001 Python Software Foundation; All Rights Reserved` | [`licenses/CPython-3.14-LICENSE.txt`](licenses/CPython-3.14-LICENSE.txt) (from `.../lib/python3.14/LICENSE.txt`) |
| Software incorporated in CPython 3.14.10 (Mersenne Twister, SipHash, dtoa, expat 2.9.0, libmpdec (mpdecimal 4.0.1), the Zstandard bindings, and the rest of CPython's list) | various permissive licenses, as listed | see file | [`licenses/CPython-3.14.10-incorporated-software.txt`](licenses/CPython-3.14.10-incorporated-software.txt) |
| HACL* (in `_sha1`, `_sha2`, `_sha3`, `_md5`, `_blake2`, `_hmac`) | MIT | `Copyright (c) 2016-2022 INRIA, CMU and Microsoft Corporation`, `Copyright (c) 2022-2023 HACL* Contributors` | [`licenses/CPython-3.14.10-HACL-MIT.txt`](licenses/CPython-3.14.10-HACL-MIT.txt) |
| OpenSSL 3.5.9 (`libssl.3.dylib`, `libcrypto.3.dylib`) | Apache-2.0 | see file | [`licenses/OpenSSL-3.5.9-LICENSE.txt`](licenses/OpenSSL-3.5.9-LICENSE.txt) |
| zstd 1.5.7 (`libzstd.1.dylib`) | BSD-3-Clause (zstd is dual BSD/GPL-2.0; Spledio uses BSD) | `Copyright (c) Meta Platforms, Inc. and affiliates. All rights reserved.` | [`licenses/zstd-1.5.7-LICENSE.txt`](licenses/zstd-1.5.7-LICENSE.txt) |
| XZ Utils 5.2.3, liblzma (compiled into `_lzma`) | **public domain**, per the `COPYING` of XZ Utils 5.2.3 (later XZ releases changed to 0BSD) | none | [`licenses/xz-5.2.3-COPYING.txt`](licenses/xz-5.2.3-COPYING.txt) |

The python.org build links these system libraries of macOS, which are not bundled: `libz`, `libbz2`, `libffi` (for `_ctypes`), `libedit` (for `readline`). SQLite is not bundled.

### PyInstaller 6.22.3 bootloader (only in the packaged app)

The app's main executable (`Spledio.app/Contents/MacOS/Spledio`) is PyInstaller's
bootloader, which starts the bundled Python. It is under GPL-2.0-or-later with the
PyInstaller Bootloader Exception, which allows distributing it with any program
(`SPDX: GPL-2.0-or-later WITH Bootloader-exception`). PyInstaller itself is a build
tool and is not in the app. License file:
[`licenses/PyInstaller-6.22.3-COPYING.txt`](licenses/PyInstaller-6.22.3-COPYING.txt).
The build also keeps `setuptools` and `packaging` out of the app
(`--exclude-module`; `scripts/release_macos.sh` fails if they are bundled): cffi
mentions setuptools only for compiling C extensions, which Spledio never does.

## Development-only dependencies (not redistributed)

These are needed only to run the tests (`requirements-dev.txt`) and are never
part of a distributed app.

| Package | Version | License (SPDX, from metadata) | Copyright as shipped | License file |
|---|---|---|---|---|
| pytest | 9.0.3 | MIT | `Copyright (c) 2004 Holger Krekel and others` | [`licenses/pytest-LICENSE.txt`](licenses/pytest-LICENSE.txt) |
| pytest-qt | 4.5.0 | MIT | `Copyright (c) 2013-2016 Bruno Oliveira and others` | [`licenses/pytest-qt-LICENSE.txt`](licenses/pytest-qt-LICENSE.txt) |
| pluggy | 1.7.0 | MIT | `Copyright (c) 2015 holger krekel (rather uses bitbucket/hpk42)` | [`licenses/pluggy-LICENSE.txt`](licenses/pluggy-LICENSE.txt) |
| iniconfig | 2.3.1 | MIT | `Copyright (c) 2010 - 2023 Holger Krekel and others` | [`licenses/iniconfig-LICENSE.txt`](licenses/iniconfig-LICENSE.txt) |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | `Copyright (c) Donald Stufft and individual contributors.` (BSD file; the Apache file has no project copyright line) | [`licenses/packaging-LICENSE.txt`](licenses/packaging-LICENSE.txt), [`licenses/packaging-LICENSE.APACHE.txt`](licenses/packaging-LICENSE.APACHE.txt), [`licenses/packaging-LICENSE.BSD.txt`](licenses/packaging-LICENSE.BSD.txt) |
| Pygments | 2.21.0 | BSD-2-Clause | `Copyright (c) 2006-2022 by the respective authors (see AUTHORS file).` | [`licenses/pygments-LICENSE.txt`](licenses/pygments-LICENSE.txt), [`licenses/pygments-AUTHORS.txt`](licenses/pygments-AUTHORS.txt) |
| typing_extensions | 4.16.0 | PSF-2.0 | `Copyright (c) 2001, 2002, ... 2023 Python Software Foundation; All Rights Reserved` (as shipped, years abbreviated here) | [`licenses/typing_extensions-LICENSE.txt`](licenses/typing_extensions-LICENSE.txt) |

`typing_extensions` is installed as a dependency of `pytest-qt`
(`Required-by: pytest-qt`) but is **not pinned** in `requirements-dev.txt`.
`pip` 26.3 is also present in `.venv`. It is a tool, not a dependency, and is
not listed or redistributed.

## Status of the notices for a distributed app (2026-11-07)

The gaps found on 2026-11-06 are closed:

1. LGPL-3.0 text for Qt/PySide6/shiboken6: [`licenses/LGPL-3.0.txt`](licenses/LGPL-3.0.txt).
2. Third-party code inside the Qt binaries that ship: [`licenses/Qt-6.11.3-third-party-notices.md`](licenses/Qt-6.11.3-third-party-notices.md) and its 18 license texts.
3. libFLAC, libogg, libvorbis, libopus, mpg123 and LAME: their upstream license files (LAME's is the LGPL-2.0).
4. PortAudio: its full license text.
5. The libraries the python.org interpreter brings in: OpenSSL 3.5.9, zstd 1.5.7, XZ Utils 5.2.3 (liblzma is public domain, not 0BSD as written here before), mpdecimal and expat (in CPython's list of incorporated software), and HACL*.
6. The app's `Contents/Resources/licenses/` gets `LICENSE`, this file and every file in `licenses/` except the development-only tools (`scripts/release_macos.sh`), and shows them under Help ▸ Licenses and Notices.

Still open:

- **libquadmath's exact GCC version** (LGPL-2.1, from the scipy wheel). It is not readable from the binary. SciPy 1.17.1's build workflow points to GitHub's preinstalled `gfortran-13` (see [`licenses/SOURCE-AND-REPLACING-LIBRARIES.md`](licenses/SOURCE-AND-REPLACING-LIBRARIES.md)), so the matching GCC 13 source is not pinned yet.
- **Publishing the source.** The LGPL and GPL source offers are only met once the release publishes `Spledio-<version>-source.tar.gz` and `third-party-sources/` next to the DMG (`scripts/release_macos.sh` makes both) and the repository is public.
- This review was done by the project, not by a lawyer.

## License file sources and checksums

SHA-256 (first 16 hex digits) of each file in `licenses/`. Each file is identical to its source unless
the Source column says it was converted, extracted or written for Spledio. `tests/test_licenses.py` checks
that this table lists every file in `licenses/` with its current hash.
`SP` = `.venv/lib/python3.14/site-packages`.

| File in `licenses/` | Source | SHA-256 (prefix) |
|---|---|---|
| `numpy-LICENSE.txt` | `SP/numpy-2.4.6.dist-info/licenses/LICENSE.txt` | `649a589ab31076e7` |
| `numpy-libdivide-LICENSE.txt` | `.../licenses/numpy/_core/include/numpy/libdivide/LICENSE.txt` | `fbc539f47d0cf83b` |
| `numpy-pythoncapi-compat-COPYING.txt` | `.../licenses/numpy/_core/src/common/pythoncapi-compat/COPYING` | `3f2c1ffa76383a55` |
| `numpy-highway-LICENSE.txt` | `.../licenses/numpy/_core/src/highway/LICENSE` | `e340270d4f643845` |
| `numpy-dragon4-LICENSE.txt` | `.../licenses/numpy/_core/src/multiarray/dragon4_LICENSE.txt` | `f7b14309e5f54e50` |
| `numpy-x86-simd-sort-LICENSE.md` | `.../licenses/numpy/_core/src/npysort/x86-simd-sort/LICENSE.md` | `211e7d737d6179a0` |
| `numpy-svml-LICENSE.txt` | `.../licenses/numpy/_core/src/umath/svml/LICENSE` | `c17011467b4fe6e2` |
| `numpy-pocketfft-LICENSE.md` | `.../licenses/numpy/fft/pocketfft/LICENSE.md` | `a85ca13fdf90160b` |
| `numpy-lapack_lite-LICENSE.txt` | `.../licenses/numpy/linalg/lapack_lite/LICENSE.txt` | `a14cc25e10d40a3a` |
| `numpy-ma-LICENSE.txt` | `.../licenses/numpy/ma/LICENSE` | `05f3b88351988ecf` |
| `numpy-random-LICENSE.md` | `.../licenses/numpy/random/LICENSE.md` | `103166b62b80443a` |
| `numpy-random-distributions-LICENSE.md` | `.../licenses/numpy/random/src/distributions/LICENSE.md` | `469778be8f33d54e` |
| `numpy-random-mt19937-LICENSE.md` | `.../licenses/numpy/random/src/mt19937/LICENSE.md` | `0e111337b25494a7` |
| `numpy-random-pcg64-LICENSE.md` | `.../licenses/numpy/random/src/pcg64/LICENSE.md` | `e7d4f4fb20358f31` |
| `numpy-random-philox-LICENSE.md` | `.../licenses/numpy/random/src/philox/LICENSE.md` | `6f5828a57223d4cc` |
| `numpy-random-sfc64-LICENSE.md` | `.../licenses/numpy/random/src/sfc64/LICENSE.md` | `9d4098846018319c` |
| `numpy-random-splitmix64-LICENSE.md` | `.../licenses/numpy/random/src/splitmix64/LICENSE.md` | `268b0ca06759d31e` |
| `scipy-LICENSE.txt` | `SP/scipy-1.17.1.dist-info/LICENSE.txt` | `60a7d05d729ed5c7` |
| `soundfile-LICENSE.txt` | `SP/soundfile-0.13.1.dist-info/LICENSE` | `0dd2e411fed553ba` |
| `libsndfile-LGPL-2.1.txt` | `SP/_soundfile_data/COPYING` | `ad01ea5cd2755f60` |
| `sounddevice-LICENSE.txt` | `SP/sounddevice-0.5.5.dist-info/licenses/LICENSE` | `b6eea21bbafbaf7a` |
| `portaudio-binaries-README.md` | `SP/_sounddevice_data/portaudio-binaries/README.md` | `b3cb053566b6f38c` |
| `cffi-LICENSE.txt` | `SP/cffi-2.1.1.dist-info/licenses/LICENSE` | `5ba24ddc57067f92` |
| `pycparser-LICENSE.txt` | `SP/pycparser-3.12.dist-info/licenses/LICENSE` | `0a9e78156ae8b96c` |
| `CPython-3.14-LICENSE.txt` | `/opt/homebrew/opt/python@3.14/Frameworks/Python.framework/Versions/3.14/lib/python3.14/LICENSE.txt` | `b0e25a78cffb43f4` |
| `pytest-LICENSE.txt` | `SP/pytest-9.0.3.dist-info/licenses/LICENSE` | `ca836a5f9ecca3b2` |
| `pytest-qt-LICENSE.txt` | `SP/pytest_qt-4.5.0.dist-info/licenses/LICENSE` | `ddb94f90ceb2ebb3` |
| `pluggy-LICENSE.txt` | `SP/pluggy-1.7.0.dist-info/licenses/LICENSE` | `d6b65e6c213a5d0b` |
| `iniconfig-LICENSE.txt` | `SP/iniconfig-2.3.1.dist-info/licenses/LICENSE` | `3409fa91f7ace557` |
| `packaging-LICENSE.txt` | `SP/packaging-26.3.dist-info/licenses/LICENSE` | `cad1ef5bd340d73e` |
| `packaging-LICENSE.APACHE.txt` | `SP/packaging-26.3.dist-info/licenses/LICENSE.APACHE` | `0d542e0c8804e39a` |
| `packaging-LICENSE.BSD.txt` | `SP/packaging-26.3.dist-info/licenses/LICENSE.BSD` | `b70e7e9b742f1cc6` |
| `pygments-LICENSE.txt` | `SP/pygments-2.21.0.dist-info/licenses/LICENSE` | `a9d66f1d526df02e` |
| `pygments-AUTHORS.txt` | `SP/pygments-2.21.0.dist-info/licenses/AUTHORS` | `71a83872ad82f57b` |
| `typing_extensions-LICENSE.txt` | `SP/typing_extensions-4.16.0.dist-info/licenses/LICENSE` | `3b2f81fe21d181c4` |
| `LGPL-3.0.txt` | <https://www.gnu.org/licenses/lgpl-3.0.txt> (downloaded 2026-11-07) | `e3a994d82e644b03` |
| `Qt-6.11.3-AFL-2.1.txt` | `LICENSES/AFL-2.1.txt`, qt/qtbase tag `v6.11.3` | `93bb4f7417aa775b` |
| `Qt-6.11.3-Apache-2.0.txt` | `LICENSES/Apache-2.0.txt`, qt/qtbase tag `v6.11.3` | `e3ba223bb1423f0a` |
| `Qt-6.11.3-BSD-2-Clause.txt` | `LICENSES/BSD-2-Clause.txt`, qt/qtbase tag `v6.11.3` | `8fda146749ca071a` |
| `Qt-6.11.3-BSD-3-Clause.txt` | `LICENSES/BSD-3-Clause.txt`, qt/qtbase tag `v6.11.3` | `9f0490f18656c6f2` |
| `Qt-6.11.3-CC0-1.0.txt` | `LICENSES/CC0-1.0.txt`, qt/qtbase tag `v6.11.3` | `a2010f343487d3f7` |
| `Qt-6.11.3-FTL.txt` | `LICENSES/FTL.txt`, qt/qtbase tag `v6.11.3` | `08c135755dd58903` |
| `Qt-6.11.3-HPND-sell-variant.txt` | `LICENSES/HPND-sell-variant.txt`, qt/qtsvg tag `v6.11.3` | `3a946598a9f674d4` |
| `Qt-6.11.3-HPND.txt` | `LICENSES/HPND.txt`, qt/qtbase tag `v6.11.3` | `67ac968431270291` |
| `Qt-6.11.3-Imlib2.txt` | `LICENSES/Imlib2.txt`, qt/qtbase tag `v6.11.3` | `8846f2baba1a7f4c` |
| `Qt-6.11.3-libpng-2.0.txt` | `LICENSES/libpng-2.0.txt`, qt/qtbase tag `v6.11.3` | `7317e078e2d3b5d7` |
| `Qt-6.11.3-Libpng.txt` | `LICENSES/Libpng.txt`, qt/qtbase tag `v6.11.3` | `58fe669792f9b4e0` |
| `Qt-6.11.3-LicenseRef-BSD-3-Clause-with-PCRE2-Binary-Like-Packages-Exception.txt` | `LICENSES/LicenseRef-BSD-3-Clause-with-PCRE2-Binary-Like-Packages-Exception.txt`, qt/qtbase tag `v6.11.3` | `0526625fcc746a42` |
| `Qt-6.11.3-LicenseRef-ICC-License.txt` | `LICENSES/LicenseRef-ICC-License.txt`, qt/qtbase tag `v6.11.3` | `9e34ff47b3a44814` |
| `Qt-6.11.3-LicenseRef-SHA1-Public-Domain.txt` | `LICENSES/LicenseRef-SHA1-Public-Domain.txt`, qt/qtbase tag `v6.11.3` | `65d86b8ec6b1cd8b` |
| `Qt-6.11.3-MIT-open-group.txt` | `LICENSES/MIT-open-group.txt`, qt/qtbase tag `v6.11.3` | `c55d2ab72d8a4f9e` |
| `Qt-6.11.3-MIT.txt` | `LICENSES/MIT.txt`, qt/qtbase tag `v6.11.3` | `d040359701c01996` |
| `Qt-6.11.3-third-party-notices.md` | **written for Spledio** from the `qt_attribution.json` files of qtbase and qtsvg at `v6.11.3` and pyside-setup at `v6.11.3` | `1d6e351f094bd865` |
| `Qt-6.11.3-Unicode-3.0.txt` | `LICENSES/Unicode-3.0.txt`, qt/qtbase tag `v6.11.3` | `80bb6294022d8764` |
| `Qt-6.11.3-X11.txt` | `LICENSES/X11.txt`, qt/qtbase tag `v6.11.3` | `e2936c6509784b4f` |
| `PySide6-6.11.3-PSF-3.7.0.txt` | `sources/shiboken6/shibokenmodule/files.dir/shibokensupport/signature/PSF-3.7.0.txt`, pyside-setup tag `v6.11.3` | `e5bdc4b568f0016d` |
| `libFLAC-1.4.3-COPYING.Xiph.txt` | `COPYING.Xiph` in `flac-1.4.3.tar.xz` (downloads.xiph.org) | `12600ea1a7affcbf` |
| `libogg-1.3.5-COPYING.txt` | `COPYING` in `libogg-1.3.5.tar.gz` (downloads.xiph.org) | `d2ab5758336489da` |
| `libvorbis-1.3.7-COPYING.txt` | `COPYING` in `libvorbis-1.3.7.tar.gz` (downloads.xiph.org) | `ec1815db59fcd302` |
| `libopus-1.4-COPYING.txt` | `COPYING` in `opus-1.4.tar.gz` (downloads.xiph.org) | `8338ce8d922bb441` |
| `mpg123-1.32.3-COPYING.txt` | `COPYING` in `mpg123-1.32.3.tar.bz2` (mpg123.de) | `c22482728a634a8d` |
| `mpg123-1.32.3-AUTHORS.txt` | `AUTHORS` in `mpg123-1.32.3.tar.bz2` (mpg123.de) | `bfa5dab0ed3b3ea7` |
| `LAME-3.100-COPYING.txt` | `COPYING` in `lame-3.100.tar.gz` (SourceForge) | `bfe4a52dc4645385` |
| `LAME-3.100-LICENSE.txt` | `LICENSE` in `lame-3.100.tar.gz` (SourceForge) | `7a40cbd8818c57e5` |
| `PortAudio-19.7.0-LICENSE.txt` | `LICENSE.txt`, PortAudio/portaudio tag `v19.7.0` | `ec52a1952d701f94` |
| `CPython-3.14.10-incorporated-software.txt` | **converted:** section "Licenses and Acknowledgements for Incorporated Software" of `license.html` in python.org's `python-3.14.10-macos11.pkg`, as plain text, with a note added at the top | `b4eecf7daa152180` |
| `CPython-3.14.10-HACL-MIT.txt` | **extracted:** license header of `Modules/_hacl/Hacl_Hash_SHA2.c`, python/cpython tag `v3.14.10`, with a two-line heading added | `6ee917331c2d1ed2` |
| `OpenSSL-3.5.9-LICENSE.txt` | `LICENSE.txt`, openssl/openssl tag `openssl-3.5.9` | `7d5450cb2d142651` |
| `zstd-1.5.7-LICENSE.txt` | `LICENSE`, facebook/zstd tag `v1.5.7` | `7055266497633c90` |
| `xz-5.2.3-COPYING.txt` | `COPYING`, tukaani-project/xz tag `v5.2.3` | `c4f8e14fafe458d8` |
| `PyInstaller-6.22.3-COPYING.txt` | `.venv-build/.../pyinstaller-6.22.3.dist-info/licenses/COPYING.txt` | `0598064c7d2718e3` |
| `SOURCE-AND-REPLACING-LIBRARIES.md` | **written for Spledio** | `e4e4e54f7c3a1421` |
