#!/usr/bin/env bash
# Download the source code of the LGPL libraries inside Spledio.app, at the exact versions the app
# ships, so a release can offer it next to the app download (LGPL-2.1 section 6, LGPL-3.0 section 4).
# Also included: the BSD-licensed codecs that are linked into the same libsndfile library, and
# the recipe that built that library. See licenses/SOURCE-AND-REPLACING-LIBRARIES.md.
#
#   scripts/fetch_lgpl_sources.sh [OUT_DIR]        # default: dist/third-party-sources
#
# Archives are cached in ~/Library/Caches/spledio-build/sources and checked against the SHA-256
# pinned below. OUT_DIR gets copies of them plus SHA256SUMS. Nothing is uploaded anywhere.
#
# How the pins were checked (2026-11-07): Qt archives against download.qt.io's md5sums.txt;
# flac and opus against downloads.xiph.org's SHA256SUMS.txt; the others were pinned from the
# first download over HTTPS from the official site listed with them.
set -euo pipefail

OUT="${1:-dist/third-party-sources}"
CACHE="${SPLEDIO_BUILD_CACHE:-$HOME/Library/Caches/spledio-build}/sources"

# file | URL | SHA-256
SOURCES=(
  "qtbase-everywhere-src-6.11.3.tar.xz|https://download.qt.io/official_releases/qt/6.11/6.11.3/submodules/qtbase-everywhere-src-6.11.3.tar.xz|46de0710b594945a5d446ae44354daa386141c6903c53781b2ca547bc62f8f8f"
  "qtsvg-everywhere-src-6.11.3.tar.xz|https://download.qt.io/official_releases/qt/6.11/6.11.3/submodules/qtsvg-everywhere-src-6.11.3.tar.xz|9bdf6fa4b5a2d91c3eaa2edd12263fdff0ae19d0ac25d3c99946cd4189a07123"
  "pyside-setup-everywhere-src-6.11.3.tar.xz|https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.11.3-src/pyside-setup-everywhere-src-6.11.3.tar.xz|050819cb05e14d6689f42b56e4fa9c8ff63be2bb339e0c094c45b990b7cabde1"
  "libsndfile-1.2.2.tar.xz|https://github.com/libsndfile/libsndfile/releases/download/1.2.2/libsndfile-1.2.2.tar.xz|3799ca9924d3125038880367bf1468e53a1b7e3686a934f098b7e1d286cdb80e"
  "mpg123-1.32.3.tar.bz2|https://mpg123.de/download/mpg123-1.32.3.tar.bz2|2d9913a57d4ee8f497a182c6e82582602409782a4fb481e989feebf4435867b4"
  "lame-3.100.tar.gz|https://downloads.sourceforge.net/project/lame/lame/3.100/lame-3.100.tar.gz|ddfe36cab873794038ae2c1210557ad34857a4b6bdc515785d1da9e175b1da1e"
  "flac-1.4.3.tar.xz|https://downloads.xiph.org/releases/flac/flac-1.4.3.tar.xz|6c58e69cd22348f441b861092b825e591d0b822e106de6eb0ee4d05d27205b70"
  "libogg-1.3.5.tar.gz|https://downloads.xiph.org/releases/ogg/libogg-1.3.5.tar.gz|0eb4b4b9420a0f51db142ba3f9c64b333f826532dc0f48c6410ae51f4799b664"
  "libvorbis-1.3.7.tar.gz|https://downloads.xiph.org/releases/vorbis/libvorbis-1.3.7.tar.gz|0e982409a9c3fc82ee06e08205b1355e5c6aa4c36bca58146ef399621b0ce5ab"
  "opus-1.4.tar.gz|https://downloads.xiph.org/releases/opus/opus-1.4.tar.gz|c9b32b4253be5ae63d1ff16eea06b94b5f0f2951b7a02aceef58e3a3ce49c51f"
  "libsndfile-binaries-804500c476f9f8059b25eafafbe97593ee16452e.tar.gz|https://github.com/bastibe/libsndfile-binaries/archive/804500c476f9f8059b25eafafbe97593ee16452e.tar.gz|3aa6aa40338b8e4345facebd246df9e3ab3690fe97d15ac72c978e061bfe583d"
)

die() { echo "error: $*" >&2; exit 1; }

mkdir -p "$CACHE" "$OUT"
: > "$OUT/SHA256SUMS"
for entry in "${SOURCES[@]}"; do
  IFS='|' read -r name url sha <<<"$entry"
  file="$CACHE/$name"
  if [[ ! -f "$file" ]] || ! echo "$sha  $file" | shasum -a 256 -c --status; then
    echo "downloading $url"
    curl -fL --proto '=https' --tlsv1.2 -o "$file.part" "$url"
    mv "$file.part" "$file"
  fi
  echo "$sha  $file" | shasum -a 256 -c --status || die "SHA-256 of $name does not match the pinned value"
  cp "$file" "$OUT/$name"
  echo "$sha  $name" >> "$OUT/SHA256SUMS"
done
cp licenses/SOURCE-AND-REPLACING-LIBRARIES.md "$OUT/README.md"
echo "$(wc -l < "$OUT/SHA256SUMS" | tr -d ' ') source archives in $OUT ($(du -sh "$OUT" | cut -f1))"
