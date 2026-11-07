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
[checksum table](#license-file-sources-and-checksums).

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
| License file under `licenses/` | **None.** The wheels ship no license file: the dist-info folders have no `licenses/` and there is no LICENSE/COPYING anywhere under `PySide6/` or `shiboken6/`. The LGPL-3.0 text is not on this machine. **Gap:** add the LGPL-3.0 and GPL-3.0 texts from <https://www.gnu.org/licenses/> before distributing a bundle. |
| Bundled native libraries | Qt 6.11.3 frameworks (`PySide6/Qt/lib/*.framework`, 155 entries), Qt plugins (`PySide6/Qt/plugins`), `libpyside6.abi3.6.11.dylib`, `libpyside6qml.abi3.6.11.dylib`, `shiboken6/libshiboken6.abi3.6.11.dylib`. Qt reports itself as a "shared (dynamic) release build". The Addons wheel also ships **FFmpeg n7.1.5** (`PySide6/Qt/lib/libavcodec.61`, `libavformat.61`, `libavutil.59`, `libswresample.5`, `libswscale.8`). Each reports `... license: LGPL version 2.1 or later`, and the configure string has no `--enable-gpl`/`--enable-nonfree`. Only `Qt/plugins/multimedia/libffmpegmediaplugin.dylib` uses them. Spledio does not import QtMultimedia, does not load them (checked with dyld), and must **not** bundle them. |
| Qt modules Spledio loads | QtCore, QtGui, QtWidgets, plus QtDBus (QtGui links to it on macOS). See [`docs/DEPENDENCY_INVENTORY.md`](docs/DEPENDENCY_INVENTORY.md#qt--pyside6-lgpl-30-obligations-when-distributing-a-bundled-app). |
| Third-party code inside Qt | QtGui contains libpng 1.6.59 (shipped strings: `Copyright (c) 2018-2026 Cosmin Truta`, `Copyright (c) 1998-2002,2004,2006-2018 Glenn Randers-Pehrson`), plus FreeType, HarfBuzz and md4c. QtCore contains PCRE2. The imageformats plugins contain libtiff, libwebp and libjpeg-turbo. **No license texts for these components ship in the wheel.** Qt lists them, with their licenses, on [Licenses Used in Qt](https://doc.qt.io/qt-6/licenses-used-in-qt.html), and since Qt 6.8 also as an SBOM. FreeType's FTL, for example, requires a credit in the documentation. **Gap:** collect these texts before distributing. |

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
| libFLAC | 1.4.3 (`reference libFLAC 1.4.3 20230623`) | BSD-3-Clause per the libsndfile-binaries README. **Not verified locally:** no file shipped. | Josh Coalson, Xiph.Org Foundation (README) | **missing** |
| libogg | present (`ogg_*` symbols); version not determined | BSD-3-Clause per README ("Ogg Vorbis"). **Not verified locally.** | Xiph.Org Foundation (README) | **missing** |
| libvorbis / libvorbisenc | 1.3.7 (`Xiph.Org libVorbis 1.3.7`) | BSD-3-Clause per README. **Not verified locally.** | Xiph.Org Foundation (README) | **missing** |
| libopus | 1.4 (`libopus 1.4`) | BSD-3-Clause per README. **Not verified locally.** | Xiph.Org Foundation, Broadcom, Microsoft (README) | **missing** |
| libmpg123 (MP3 decode) | 1.32.3 (next to `src/libmpg123/` strings) | LGPL-2.1 per README. **Not verified locally.** | Michael Hipp and others (README) | LGPL-2.1 text: [`licenses/libsndfile-LGPL-2.1.txt`](licenses/libsndfile-LGPL-2.1.txt); upstream COPYING **missing** |
| LAME (MP3 encode) | 3.100 (`LAME3.100`) | LGPL-2.0 per README (written "LGP v2"). The "or later" variant is **not verified locally**. | Mike Cheng, Mark Taylor, The LAME Project (README) | **missing** (LGPL-2.0 text not on this machine) |

libsndfile also has internal codecs (ALAC, GSM 6.10, G.72x, IMA/MS ADPCM; the ALAC and GSM subtypes are listed by `soundfile.available_subtypes()`). Their individual notices are **not verified locally**.

Spledio rejects MP3/Ogg/Opus on import and exports only WAV and FLAC, but **redistributing `libsndfile_arm64.dylib` redistributes all of the codecs above**. **Gap:** before distributing a bundle, obtain the upstream COPYING/LICENSE files for libFLAC, libogg, libvorbis, libopus, mpg123 and LAME (the BSD-3-Clause ones require the copyright notice in binary distributions).

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
| License file | [`licenses/portaudio-binaries-README.md`](licenses/portaudio-binaries-README.md) (the only notice in the wheel). **Gap:** the full PortAudio license text is not shipped; add the upstream LICENSE.txt to a distribution. |
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

A frozen app (PyInstaller or similar) also redistributes the interpreter. The
verified interpreter is **Homebrew CPython 3.14.7**
(`/opt/homebrew/opt/python@3.14/...`).

| Component | License | Copyright as shipped | File |
|---|---|---|---|
| CPython 3.14.7 | PSF-2.0 (Python Software Foundation License; see file for history and CNRI/CWI terms) | `Copyright (c) 2001 Python Software Foundation; All Rights Reserved` | [`licenses/CPython-3.14-LICENSE.txt`](licenses/CPython-3.14-LICENSE.txt) (from `.../lib/python3.14/LICENSE.txt`) |
| Homebrew libraries PyInstaller **did** collect in the first build (2026-11-07): OpenSSL 3.6.4 (`libssl.3`, `libcrypto.3`), xz 5.8.4 (`liblzma.5`), zstd 1.5.7 (`libzstd.1`), mpdecimal 4.0.1 (`libmpdec.4`). `libsqlite3` was not collected. | Expected: Apache-2.0 (OpenSSL 3), 0BSD (xz's liblzma), BSD-3-Clause (zstd, which also offers GPL-2.0), BSD-2-Clause (mpdecimal). **Not verified** against the shipped files. | not verified | **missing**: see gap 5 |

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

## Gaps to close before distributing a bundled app

1. LGPL-3.0 text for Qt/PySide6/shiboken6 (not shipped in the wheels or anywhere on this machine).
2. Third-party notices for code inside the Qt parts that ship (QtCore, QtGui, QtWidgets, QtDBus, QtSvg and the cocoa, svg and macstyle plugins: libpng, FreeType, HarfBuzz, md4c, PCRE2, …). The JPEG, TIFF and WebP plugins are removed from the bundle (`scripts/prune_qt_bundle.py`), so libjpeg-turbo, libtiff and libwebp notices are not needed. Sources: [Licenses Used in Qt](https://doc.qt.io/qt-6/licenses-used-in-qt.html) and the Qt SBOM.
3. Upstream license files for libFLAC, libogg, libvorbis, libopus, mpg123 and LAME (LAME needs the LGPL-2.0 text).
4. The full PortAudio license text.
5. Notices for the libraries the frozen interpreter pulls in. The Homebrew interpreter brought OpenSSL, xz, zstd and mpdecimal. A release should be built with the python.org Python anyway (minimum macOS), so collect the notices for **that** interpreter's libraries after the first python.org build.
6. The app's `Contents/Resources/licenses/` folder gets `LICENSE`, this file and every file in `licenses/` except the development-only tools (`scripts/release_macos.sh`). The texts in gaps 1–5 must be added to `licenses/` before a public release.

## License file sources and checksums

SHA-256 (first 16 hex digits) of each file in `licenses/`; each file is identical to its source.
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
