# Third-party notices for Qt 6.11.3 in Spledio.app

This file covers the third-party code that is compiled into the Qt 6.11.3 binaries shipped in Spledio.app for macOS (arm64). Those binaries come from the PySide6 6.11.3 wheels. The components, versions, SPDX license identifiers and copyright lines below are taken from the `qt_attribution.json` files in the qtbase and qtsvg repositories at tag v6.11.3. The license texts are the unmodified files from the `LICENSES/` directories of the same repositories at the same tag. A separate section covers code derived from Python inside the shiboken6 library of Qt for Python 6.11.3.

Qt itself is used under the GNU Lesser General Public License v3.0 only (LGPL-3.0-only). That license text is in the separate LGPL-3.0 license file shipped with Spledio, not in this file.

Qt 6.11.3 source code:

- qtbase: https://code.qt.io/cgit/qt/qtbase.git/tag/?h=v6.11.3 (mirror: https://github.com/qt/qtbase/tree/v6.11.3; archive: https://download.qt.io/official_releases/qt/6.11/6.11.3/submodules/qtbase-everywhere-src-6.11.3.tar.xz)
- qtsvg: https://code.qt.io/cgit/qt/qtsvg.git/tag/?h=v6.11.3 (mirror: https://github.com/qt/qtsvg/tree/v6.11.3; archive: https://download.qt.io/official_releases/qt/6.11/6.11.3/submodules/qtsvg-everywhere-src-6.11.3.tar.xz)
- Qt for Python (PySide6/shiboken6): https://code.qt.io/cgit/pyside/pyside-setup.git/tag/?h=v6.11.3 (mirror: https://github.com/qtproject/pyside-pyside-setup/tree/v6.11.3; archive: https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.11.3-src/pyside-setup-everywhere-src-6.11.3.tar.xz)

## Shipped binaries referred to below

| Name | Path inside Spledio.app |
|---|---|
| QtCore | Contents/Frameworks/PySide6/Qt/lib/QtCore.framework/Versions/A/QtCore |
| QtGui | Contents/Frameworks/PySide6/Qt/lib/QtGui.framework/Versions/A/QtGui |
| QtDBus | Contents/Frameworks/PySide6/Qt/lib/QtDBus.framework/Versions/A/QtDBus |
| QtSvg | Contents/Frameworks/PySide6/Qt/lib/QtSvg.framework/Versions/A/QtSvg |
| libqcocoa | Contents/Frameworks/PySide6/Qt/plugins/platforms/libqcocoa.dylib |
| libshiboken6 | Contents/Frameworks/shiboken6/libshiboken6.abi3.6.11.dylib |

QtWidgets, the plugins libqminimal, libqoffscreen, libqmacstyle, libqsvg and libqsvgicon, and the PySide6 binding libraries (libpyside6 and the QtCore, QtGui, QtWidgets and QtDBus `.abi3.so` modules, and shiboken6's Shiboken.abi3.so) are also shipped. No `qt_attribution.json` entry applies to them; the third-party code they use is in the binaries listed above.

## Included components

| Component | Binary | Version | SPDX license | Copyright (as in qt_attribution.json) | License file | Upstream / source |
|---|---|---|---|---|---|---|
| Apache Tika MimeType Definitions | QtCore | c29724782854bb5a319f4a1940d0828a58ace3f9 | Apache-2.0 | Copyright 2026 The Apache Software Foundation | Qt-6.11.3-Apache-2.0.txt | https://github.com/apache/tika/tree/main/tika-core/src/main/resources/org/apache/tika/mime<br>https://github.com/apache/tika/blob/c29724782854bb5a319f4a1940d0828a58ace3f9/tika-core/src/main/resources/org/apache/tika/mime/tika-mimetypes.xml |
| BLAKE2 (reference implementation) | QtCore | ed1974ea83433eba7b2d95c5dcd9ac33cb847913 | CC0-1.0 OR Apache-2.0 | Copyright 2012, Samuel Neves \<sneves@dei.uc.pt\> | Qt-6.11.3-CC0-1.0.txt<br>(CC0-1.0 elected) | https://blake2.net/<br>https://github.com/BLAKE2/BLAKE2/tree/ed1974ea83433eba7b2d95c5dcd9ac33cb847913 |
| Efficient Binary-Decimal and Decimal-Binary Conversion Routines for IEEE Doubles | QtCore | 3.4.0 | BSD-3-Clause | Copyright 2006-2012, the V8 project authors | Qt-6.11.3-BSD-3-Clause.txt | https://github.com/google/double-conversion<br>https://github.com/google/double-conversion/releases/tag/v3.4.0 |
| Easing Equations by Robert Penner | QtCore | — | BSD-3-Clause | Copyright (c) 2001 Robert Penner | Qt-6.11.3-BSD-3-Clause.txt | http://robertpenner.com/easing/ |
| forkfd | QtCore | — | MIT | Copyright (C) 2016 Intel Corporation<br>Copyright (C) 2015 Klarälvdalens Datakonsult AB, a KDAB Group company, info@kdab.com | Qt-6.11.3-MIT.txt | https://github.com/qt/qtbase/tree/v6.11.3/src/3rdparty/forkfd (Qt source; no upstream URL in qt_attribution.json) |
| MD4 | QtCore | — | CC0-1.0 | Written by Alexander Peslyak - better known as Solar Designer \<solar@openwall.com\> - in 2001, and placed in the public domain. There's absolutely no warranty. | Qt-6.11.3-CC0-1.0.txt | https://github.com/qt/qtbase/tree/v6.11.3/src/3rdparty/md4 (Qt source; no upstream URL in qt_attribution.json) |
| MD5 | QtCore | — | CC0-1.0 | Written by Colin Plumb in 1993, no copyright is claimed. Ian Jackson \<ian@chiark.greenend.org.uk\>. | Qt-6.11.3-CC0-1.0.txt | https://github.com/qt/qtbase/tree/v6.11.3/src/3rdparty/md5 (Qt source; no upstream URL in qt_attribution.json) |
| PCRE2 | QtCore | 10.49 | LicenseRef-BSD-3-Clause-with-PCRE2-Binary-Like-Packages-Exception | Copyright (c) 1997-2007 University of Cambridge<br>Copyright (c) 2007-2024 Philip Hazel<br>Copyright (c) 2010-2024 Zoltan Herczeg | Qt-6.11.3-LicenseRef-BSD-3-Clause-with-PCRE2-Binary-Like-Packages-Exception.txt | http://www.pcre.org/<br>https://github.com/PCRE2Project/pcre2/releases/download/pcre2-10.49/pcre2-10.49.tar.bz2 |
| QEventDispatcher on macOS | QtCore | — | BSD-3-Clause | Copyright (c) 2007-2008, Apple, Inc. | Qt-6.11.3-BSD-3-Clause.txt | https://github.com/qt/qtbase/tree/v6.11.3/src/corelib/kernel (Qt source; no upstream URL in qt_attribution.json) |
| Secure Hash Algorithm SHA-1 | QtCore | — | LicenseRef-SHA1-Public-Domain | Copyright (C) Dominik Reichl \<dominik.reichl@t-online.de\><br>Copyright (C) 2016 The Qt Company Ltd | Qt-6.11.3-LicenseRef-SHA1-Public-Domain.txt | http://www.dominik-reichl.de/projects/csha1/ |
| Secure Hash Algorithm SHA-3 - Keccak | QtCore | 3.2 | CC0-1.0 | Guido Bertoni, Joan Daemen, Michaël Peeters and Gilles Van Assche.<br>To the extent possible under law, the implementers have waived all copyright<br>and related or neighboring rights to the source code in this file. | Qt-6.11.3-CC0-1.0.txt | https://github.com/qt/qtbase/tree/v6.11.3/src/3rdparty/sha3 (Qt source; no upstream URL in qt_attribution.json) |
| Secure Hash Algorithm SHA-3 - brg_endian | QtCore | 1.0.0 | BSD-2-Clause | Copyright (c) 1998-2013, Brian Gladman, Worcester, UK. All rights reserved. | Qt-6.11.3-BSD-2-Clause.txt | https://github.com/qt/qtbase/tree/v6.11.3/src/3rdparty/sha3 (Qt source; no upstream URL in qt_attribution.json) |
| Secure Hash Algorithms SHA-384 and SHA-512 | QtCore | — | BSD-3-Clause | Copyright (c) 2011 IETF Trust and the persons identified as authors of the code. | Qt-6.11.3-BSD-3-Clause.txt | https://github.com/qt/qtbase/tree/v6.11.3/src/3rdparty/rfc6234 (Qt source; no upstream URL in qt_attribution.json) |
| SipHash Algorithm | QtCore | — | CC0-1.0 | Copyright (C) 2012-2014 Jean-Philippe Aumasson<br>Copyright (C) 2012-2014 Daniel J. Bernstein \<djb@cr.yp.to\><br>Copyright (C) 2016 Intel Corporation | Qt-6.11.3-CC0-1.0.txt | https://131002.net/siphash/<br>https://raw.githubusercontent.com/veorq/SipHash/adcbf09b1684a718f594faa650ffc56bacdb0777/siphash24.c |
| TinyCBOR | QtCore | 7.0 | MIT | Copyright (C) 2015-2025 Intel Corporation | Qt-6.11.3-MIT.txt | https://github.com/intel/tinycbor<br>https://github.com/intel/tinycbor/archive/v7.0/tinycbor-7.0.tar.gz |
| Unicode Character Database (UCD) | QtCore | 36 | Unicode-3.0 | Copyright (C) 1991-2025 Unicode, Inc. | Qt-6.11.3-Unicode-3.0.txt | https://www.unicode.org/ucd/ |
| Unicode Common Locale Data Repository (CLDR) | QtCore | v48.2 | Unicode-3.0 | Copyright (C) 2004-2025 Unicode, Inc. | Qt-6.11.3-Unicode-3.0.txt | https://cldr.unicode.org/ |
| tl::expected | QtCore | 41d3e1f48d682992a2230b2a715bca38b848b269 | CC0-1.0 | To the extent possible under law, Sy Brand has waived all copyright and related or neighboring rights to the expected library. This work is published from: United Kingdom. | Qt-6.11.3-CC0-1.0.txt | https://github.com/TartanLlama/expected/<br>https://github.com/TartanLlama/expected/blob/41d3e1f48d682992a2230b2a715bca38b848b269/include/tl/expected.hpp |
| libdbus-1 headers | QtDBus | 1.13.12 | AFL-2.1 OR GPL-2.0-or-later | Copyright (C) 2002, 2003 CodeFactory AB<br>Copyright (C) 2004, 2005 Red Hat, Inc. | Qt-6.11.3-AFL-2.1.txt<br>(AFL-2.1 elected) | https://www.freedesktop.org/wiki/Software/dbus/ |
| Adobe Glyph List For New Fonts | QtGui | 1.7 | BSD-3-Clause | Copyright 2002, 2003, 2005, 2006, 2008, 2010, 2015 Adobe Systems | Qt-6.11.3-BSD-3-Clause.txt | https://github.com/adobe-type-tools/agl-aglfn |
| Anti-aliasing rasterizer from FreeType 2 | QtGui | — | FTL OR GPL-2.0-only | Copyright 2000-2016 by David Turner, Robert Wilhelm, and Werner Lemberg. | Qt-6.11.3-FTL.txt<br>(FTL elected) | http://www.freetype.org |
| Emoji Segmenter | QtGui | 0.4.0 | Apache-2.0 | Copyright 2019 Google LLC | Qt-6.11.3-Apache-2.0.txt | https://github.com/google/emoji-segmenter<br>https://github.com/google/emoji-segmenter/releases/tag/0.4.0 |
| Freetype 2 | QtGui | 2.15.0 | FTL OR GPL-2.0-only | Copyright (c) 2007-2014 Adobe Systems Incorporated<br>Copyright (c) 2004-2026 Albert Chin-A-Young<br>Copyright (c) 2018-2026 Armin Hasitzka, David Turner, Robert Wilhelm, and Werner Lemberg<br>Copyright (c) 2000 Computing Research Labs, New Mexico State University<br>Copyright (c) 1996-2026 David Turner, Robert Wilhelm, Dominik Röttsches, and Werner Lemberg<br>Copyright (c) 2004-2026 David Turner, Robert Wilhelm, Werner Lemberg and George Williams<br>Copyright (c) 2022-2026 David Turner, Robert Wilhelm, Werner Lemberg, and Moazin Khatti<br>Copyright (c) 2008-2026 David Turner, Robert Wilhelm, Werner Lemberg, and suzuki toshiya<br>Copyright (c) 2003-2026 David Turner, Robert Wilhelm, and Werner Lemberg<br>Copyright (c) 2005-2026 David Turner<br>Copyright (c) 2007-2026 Derek Clegg and Michael Toftdal<br>Copyright (c) 2007 Dmitry Timoshkov for Codeweavers<br>Copyright (c) 2001-2015 Francesco Zappa Nardelli<br>Copyright (c) 2005, 2007, 2008, 2013 George Williams<br>Copyright (c) 2013-2026 Google, Inc. Google Author(s) Behdad Esfahbod and Stuart Gill<br>Copyright (c) 2013-2022 Google, Inc.<br>Copyright (c) 2003 Huw D M Davies for Codeweavers<br>Copyright (c) 2010-2026 Joel Klinghed<br>Copyright (c) 1996-2026 Just van Rossum, David Turner, Robert Wilhelm, and Werner Lemberg<br>Copyright (c) 2003-2026 Masatake YAMATO and Redhat K.K.<br>Copyright (c) 2004-2026 Masatake YAMATO, Redhat K.K, David Turner, Robert Wilhelm, and Werner Lemberg<br>Copyright (c) 2019-2026 Nikhil Ramakrishnan, David Turner, Robert Wilhelm, and Werner Lemberg<br>Copyright (c) 2009-2026 Oran Agra and Mickey Gabel<br>Copyright (c) 2007-2026 Rahul Bhalerao \<rahul.bhalerao@redhat.com\><br>Copyright (c) 2002-2026 Roberto Alameda<br>Copyright (c) 2015-2026 Werner Lemberg<br>Copyright (c) 2004-2026 suzuki toshiya, Masatake YAMATO, Red Hat K.K., David Turner, Robert Wilhelm, and Werner Lemberg<br>Copyright (c) 2019 nyorain<br>Copyright (c) 2022-2026 David Turner, Robert Wilhelm, Werner Lemberg, George Williams, and Dominik Röttsches<br>Copyright (C) 2009, 2023  Red Hat, Inc. | Qt-6.11.3-FTL.txt<br>(FTL elected) | http://www.freetype.org<br>https://download.savannah.gnu.org/releases/freetype/freetype-2.15.0.tar.gz |
| Freetype 2 - Bitmap Distribution Format (BDF) support | QtGui | — | MIT | Copyright (c) 2000 Computing Research Labs, New Mexico State University<br>Copyright (c) 2001-2014 Francesco Zappa Nardelli | Qt-6.11.3-MIT.txt | http://www.freetype.org |
| Freetype 2 - Portable Compiled Format (PCF) support | QtGui | — | MIT AND MIT-open-group | Copyright (c) 2001, 2012 David Turner, Robert Wilhelm, and Werner Lemberg<br>Copyright (c) 2000-2014 Francesco Zappa Nardelli<br>Copyright (c) 1990, 1994, 1998 The Open Group | Qt-6.11.3-MIT.txt<br>Qt-6.11.3-MIT-open-group.txt | http://www.freetype.org |
| HarfBuzz-NG | QtGui | 14.6.0 | MIT | Copyright © 2010-2022  Google, Inc.<br>Copyright © 2015-2020  Ebrahim Byagowi<br>Copyright © 2019,2020  Facebook, Inc.<br>Copyright © 2012,2015  Mozilla Foundation<br>Copyright © 2011  Codethink Limited<br>Copyright © 2008,2010  Nokia Corporation and/or its subsidiary(-ies)<br>Copyright © 2009  Keith Stribley<br>Copyright © 2011  Martin Hosken and SIL International<br>Copyright © 2007  Chris Wilson<br>Copyright © 2005,2006,2020,2021,2022,2023  Behdad Esfahbod<br>Copyright © 2004,2007,2008,2009,2010,2013,2021,2022,2023  Red Hat, Inc.<br>Copyright © 1998-2005  David Turner and Werner Lemberg<br>Copyright © 2016  Igalia S.L.<br>Copyright © 2022  Matthias Clasen<br>Copyright © 2018,2021  Khaled Hosny<br>Copyright © 2018,2019,2020  Adobe, Inc<br>Copyright © 2013-2015  Alexei Podtelezhnikov | Qt-6.11.3-MIT.txt | http://harfbuzz.org<br>https://github.com/harfbuzz/harfbuzz/releases/tag/14.6.0 |
| LibPNG | QtGui | 1.6.59 | Libpng AND libpng-2.0 | Copyright (c) 1995-2026 The PNG Reference Library Authors<br>Copyright (c) 2000-2026 Cosmin Truta<br>Copyright (c) 1998-2018 Glenn Randers-Pehrson<br>Copyright (c) 1996-1997 Andreas Dilger<br>Copyright (c) 1995-1996 Guy Eric Schalnat, Group 42, Inc.<br>Copyright (c) 2000-2017 Simon-Pierre Cadieux<br>Copyright (c) 2000-2017 Eric S. Raymond<br>Copyright (c) 2000-2017 Mans Rullgard<br>Copyright (c) 2000-2017 Gilles Vollant<br>Copyright (c) 2000-2017 James Yu<br>Copyright (c) 2000-2017 Mandar Sahastrabuddhe<br>Copyright (c) 1998-2000 Tom Lane<br>Copyright (c) 1998-2000 Willem van Schaik<br>Copyright (c) 1996-1997 John Bowler<br>Copyright (c) 1996-1997 Kevin Bracey<br>Copyright (c) 1996-1997 Sam Bushell<br>Copyright (c) 1996-1997 Magnus Holmgren<br>Copyright (c) 1996-1997 Greg Roelofs<br>Copyright (c) 1996-1997 Tom Tanner<br>Copyright (c) 1995-1996 Dave Martindale<br>Copyright (c) 1995-1996 Paul Schmidt<br>Copyright (c) 1995-1996 Tim Wegner | Qt-6.11.3-Libpng.txt<br>Qt-6.11.3-libpng-2.0.txt | http://www.libpng.org/pub/png/libpng.html<br>https://download.sourceforge.net/libpng/libpng-1.6.59.tar.xz |
| MD4C | QtGui | 0.6.0 | MIT | Copyright © 2016-2026 Martin Mitáš | Qt-6.11.3-MIT.txt | https://github.com/mity/md4c<br>https://github.com/mity/md4c/releases/tag/v0.6.0 |
| Smooth Scaling Algorithm | QtGui | — | BSD-2-Clause AND Imlib2 | Copyright (C) 2004, 2005 Daniel M. Duley.<br>(C) Carsten Haitzler and various contributors.<br>(C) Willem Monsuwe \<willem@stack.nl\> | Qt-6.11.3-BSD-2-Clause.txt<br>Qt-6.11.3-Imlib2.txt | https://github.com/qt/qtbase/tree/v6.11.3/src/gui/painting (Qt source; no upstream URL in qt_attribution.json) |
| WebGradients | QtGui | — | MIT | Copyright (c) 2017 itmeo | Qt-6.11.3-MIT.txt | https://webgradients.com/ |
| X Server helper | QtGui | — | X11 AND HPND | Copyright (c) 1987, 1988 X Consortium<br>Copyright 1987, 1988 by Digital Equipment Corporation, Maynard, Massachusetts. | Qt-6.11.3-X11.txt<br>Qt-6.11.3-HPND.txt | https://www.x.org/ |
| sRGB color profile icc file | QtGui | — | LicenseRef-ICC-License | Copyright International Color Consortium, 2015 | Qt-6.11.3-LicenseRef-ICC-License.txt | http://www.color.org/ |
| Cocoa Platform Plugin | libqcocoa | — | BSD-3-Clause | Copyright (c) 2007-2008, Apple, Inc. | Qt-6.11.3-BSD-3-Clause.txt | https://github.com/qt/qtbase/tree/v6.11.3/src/plugins/platforms/cocoa (Qt source; no upstream URL in qt_attribution.json) |
| XSVG | QtSvg | — | HPND-sell-variant | Copyright 2002 USC/Information Sciences Institute | Qt-6.11.3-HPND-sell-variant.txt | https://github.com/qt/qtsvg/tree/v6.11.3/src/svg (Qt source; no upstream URL in qt_attribution.json) |

## Dual-licensed components

Where `qt_attribution.json` gives a choice of licenses ("OR"), Spledio uses the following option, and only that license text is included here:

- Freetype 2 and the anti-aliasing rasterizer from FreeType 2 (`FTL OR GPL-2.0-only`): FTL (The FreeType Project License).
- libdbus-1 headers (`AFL-2.1 OR GPL-2.0-or-later`): AFL-2.1 (Academic Free License v2.1).
- BLAKE2 reference implementation (`CC0-1.0 OR Apache-2.0`): CC0-1.0.

## Qt for Python (PySide6/shiboken6 6.11.3)

libshiboken6 contains code derived from Python 3.7.0, listed in the pyside-setup repository at tag v6.11.3:

| Component | Binary | Version | License (as in qt_attribution.json) | Copyright (as in qt_attribution.json) | License file | Upstream / source |
|---|---|---|---|---|---|---|
| Python: Buffer-protocol code copied from Python 3.7.0 (bufferprocs_py37.cpp, bufferprocs_py37.h) | libshiboken6 | 3.7.0 | PSF LICENSE AGREEMENT FOR PYTHON 3.7.0 | © Copyright 2001-2018, Python Software Foundation. | PySide6-6.11.3-PSF-3.7.0.txt | https://www.python.org/<br>https://github.com/qtproject/pyside-pyside-setup/tree/v6.11.3/sources/shiboken6/libshiboken |
| Python: Signature support package adapted from Python 3.7.0 sources (shibokensupport, embedded in libshiboken6) | libshiboken6 | 3.7.0 | Python-2.0 (Python License 2.0) | © Copyright 2001-2018, Python Software Foundation. | PySide6-6.11.3-PSF-3.7.0.txt | https://www.python.org/<br>https://github.com/qtproject/pyside-pyside-setup/tree/v6.11.3/sources/shiboken6/shibokenmodule/files.dir/shibokensupport/signature |

PySide6-6.11.3-PSF-3.7.0.txt is the unmodified file `sources/shiboken6/shibokenmodule/files.dir/shibokensupport/signature/PSF-3.7.0.txt` from pyside-setup at tag v6.11.3. The same PSF License Agreement text is reproduced in `sources/shiboken6/libshiboken/bufferprocs_py37.h`, which the first entry names as its license file.

## Excluded components

These entries have a `qt_attribution.json` in qtbase or qtsvg at v6.11.3 but are not in the binaries shipped in Spledio.app:

| Component (Id) | qt_attribution.json | Reason |
|---|---|---|
| android-native-style | qtbase/src/3rdparty/android/qt_attribution.json | Android platform plugin only. |
| android-gradle-wrapper | qtbase/src/3rdparty/gradle/qt_attribution.json | Android packaging tool only (build-time). |
| dejayvu | qtbase/src/3rdparty/wasm/qt_attribution.json | WebAssembly platform only; the fonts are not in the macOS binaries. |
| wintab | qtbase/src/3rdparty/wintab/qt_attribution.json | Windows platform plugin only. |
| xcb-xinput | qtbase/src/3rdparty/xcb/qt_attribution.json | Linux/X11 (XCB platform plugin) only. |
| (27 Wayland protocol entries) | qtbase/src/3rdparty/wayland/protocols/*/qt_attribution.json | Linux/Wayland only. |
| d3d12memoryallocator | qtbase/src/3rdparty/D3D12MemoryAllocator/qt_attribution.json | Windows (Direct3D 12 backend of QRhi) only. |
| rhi-miniengine-d3d12-mipmap | qtbase/src/gui/rhi/qt_attribution.json | Windows (Direct3D 12 shader) only. |
| opengl-headers | qtbase/src/gui/opengl/qt_attribution.json | Used on Windows and Linux only (per QtUsage); macOS uses the system OpenGL headers. |
| opengl-es2-headers | qtbase/src/gui/opengl/qt_attribution.json | Used on Windows and Linux only (per QtUsage). |
| vulkanmemoryallocator | qtbase/src/3rdparty/VulkanMemoryAllocator/qt_attribution.json | Vulkan backend of QRhi; this build has no Vulkan support (QtGui exports no QVulkan* symbols and contains the message "This build of Qt has no Vulkan support"). |
| vulkan-xml-spec | qtbase/src/gui/vulkan/qt_attribution.json | Generator input for QVulkanFunctions; Vulkan support is not compiled into this build (see above). |
| pixman | qtbase/src/3rdparty/pixman/qt_attribution.json | Not built for arm64 or macOS universal builds (qtbase src/gui/CMakeLists.txt); the shipped QtGui is arm64. |
| pcre2-sljit | qtbase/src/3rdparty/pcre2/qt_attribution.json | PCRE2 JIT is disabled for Apple arm64 (PCRE2_DISABLE_JIT in src/3rdparty/pcre2/CMakeLists.txt), so the sljit compiler is not built; QtCore imports none of the Apple JIT APIs (pthread_jit_write_protect_np, sys_icache_invalidate). |
| zlib | qtbase/src/3rdparty/zlib/qt_attribution.json | Not bundled: QtCore, QtGui and QtSvg link the macOS system zlib (/usr/lib/libz.1.dylib) and import deflate/inflate from it. |
| freetype-zlib | qtbase/src/3rdparty/freetype/qt_attribution.json | Qt builds FreeType with FT_CONFIG_OPTION_SYSTEM_ZLIB, so FreeType's own zlib copy (src/gzip) is not used; the system zlib is used. |
| libjpeg | qtbase/src/3rdparty/libjpeg/qt_attribution.json | Only in the qjpeg image plugin, which was removed from the bundle. |
| psl-data | qtbase/src/3rdparty/libpsl/qt_attribution.json | Only in Qt Network, which was removed from the bundle. |
| libpsl | qtbase/src/3rdparty/libpsl/qt_attribution.json | Only in Qt Network, which was removed from the bundle. |
| sqlite | qtbase/src/3rdparty/sqlite/qt_attribution.json | Only in the Qt SQL SQLite plugin, which is not shipped. |
| extra-cmake-modules | qtbase/cmake/3rdparty/extra-cmake-modules/qt_attribution.json | Build system only. |
| kwin | qtbase/cmake/3rdparty/kwin/qt_attribution.json | Build system only (CMake find-modules). |
| cmake-runcmake-test-modules | qtbase/src/testinternal/3rdparty/cmake/qt_attribution.json | Qt's own test infrastructure only. |
| catch2 | qtbase/src/testlib/3rdparty/catch2/qt_attribution.json | Qt Test only; Qt Test is not shipped. |
| cycle | qtbase/src/testlib/3rdparty/cycle/qt_attribution.json | Qt Test only; Qt Test is not shipped. |
| linuxperf | qtbase/src/testlib/3rdparty/linux/qt_attribution.json | Qt Test on Linux/Android only; not shipped. |
| valgrind | qtbase/src/testlib/3rdparty/valgrind/qt_attribution.json | Qt Test only; Qt Test is not shipped. |
| sbom-test-parent | qtbase/tests/auto/cmake/RunCMake/Sbom/attributions/multi_entry/qt_attribution.json | qtbase autotest data only. |
| sbom-test-child | qtbase/tests/auto/cmake/RunCMake/Sbom/attributions/multi_entry/qt_attribution.json | qtbase autotest data only. |

## License files in this directory

- Qt-6.11.3-AFL-2.1.txt: https://raw.githubusercontent.com/qt/qtbase/v6.11.3/LICENSES/AFL-2.1.txt
- Qt-6.11.3-Apache-2.0.txt: https://raw.githubusercontent.com/qt/qtbase/v6.11.3/LICENSES/Apache-2.0.txt
- Qt-6.11.3-BSD-2-Clause.txt: https://raw.githubusercontent.com/qt/qtbase/v6.11.3/LICENSES/BSD-2-Clause.txt
- Qt-6.11.3-BSD-3-Clause.txt: https://raw.githubusercontent.com/qt/qtbase/v6.11.3/LICENSES/BSD-3-Clause.txt
- Qt-6.11.3-CC0-1.0.txt: https://raw.githubusercontent.com/qt/qtbase/v6.11.3/LICENSES/CC0-1.0.txt
- Qt-6.11.3-FTL.txt: https://raw.githubusercontent.com/qt/qtbase/v6.11.3/LICENSES/FTL.txt
- Qt-6.11.3-HPND-sell-variant.txt: https://raw.githubusercontent.com/qt/qtsvg/v6.11.3/LICENSES/HPND-sell-variant.txt
- Qt-6.11.3-HPND.txt: https://raw.githubusercontent.com/qt/qtbase/v6.11.3/LICENSES/HPND.txt
- Qt-6.11.3-Imlib2.txt: https://raw.githubusercontent.com/qt/qtbase/v6.11.3/LICENSES/Imlib2.txt
- Qt-6.11.3-Libpng.txt: https://raw.githubusercontent.com/qt/qtbase/v6.11.3/LICENSES/Libpng.txt
- Qt-6.11.3-LicenseRef-BSD-3-Clause-with-PCRE2-Binary-Like-Packages-Exception.txt: https://raw.githubusercontent.com/qt/qtbase/v6.11.3/LICENSES/LicenseRef-BSD-3-Clause-with-PCRE2-Binary-Like-Packages-Exception.txt
- Qt-6.11.3-LicenseRef-ICC-License.txt: https://raw.githubusercontent.com/qt/qtbase/v6.11.3/LICENSES/LicenseRef-ICC-License.txt
- Qt-6.11.3-LicenseRef-SHA1-Public-Domain.txt: https://raw.githubusercontent.com/qt/qtbase/v6.11.3/LICENSES/LicenseRef-SHA1-Public-Domain.txt
- Qt-6.11.3-MIT-open-group.txt: https://raw.githubusercontent.com/qt/qtbase/v6.11.3/LICENSES/MIT-open-group.txt
- Qt-6.11.3-MIT.txt: https://raw.githubusercontent.com/qt/qtbase/v6.11.3/LICENSES/MIT.txt
- Qt-6.11.3-Unicode-3.0.txt: https://raw.githubusercontent.com/qt/qtbase/v6.11.3/LICENSES/Unicode-3.0.txt
- Qt-6.11.3-X11.txt: https://raw.githubusercontent.com/qt/qtbase/v6.11.3/LICENSES/X11.txt
- Qt-6.11.3-libpng-2.0.txt: https://raw.githubusercontent.com/qt/qtbase/v6.11.3/LICENSES/libpng-2.0.txt
- PySide6-6.11.3-PSF-3.7.0.txt: https://raw.githubusercontent.com/qtproject/pyside-pyside-setup/v6.11.3/sources/shiboken6/shibokenmodule/files.dir/shibokensupport/signature/PSF-3.7.0.txt
