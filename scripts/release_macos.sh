#!/usr/bin/env bash
# Build, sign, notarize and package Spledio for macOS (Apple Silicon).
#
#   UNSIGNED=1 has been run end to end (2026-11-07, see docs/RELEASE_MACOS.md).
#   A Developer ID signing + notarization run has NOT been done yet.
#
# Signed + notarized release (needs a Developer ID; see docs/RELEASE_MACOS.md):
#   SIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)" \
#   NOTARY_PROFILE=spledio-notary \
#   PYTHON=/Library/Frameworks/Python.framework/Versions/3.14/bin/python3.14 \
#   scripts/release_macos.sh
#
# Local test build (ad-hoc signed, NOT notarized, only runs on this Mac):
#   UNSIGNED=1 scripts/release_macos.sh
#
# Output: dist/Spledio-macOS-arm64.dmg and dist/Spledio-macOS-arm64.dmg.sha256
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

APP_NAME="Spledio"
BUNDLE_ID="${BUNDLE_ID:-org.spledio.Spledio}"   # keep in sync with src/spledio/branding.py (APP_ID)
PYTHON="${PYTHON:-python3}"                     # a python.org CPython is recommended (see PACKAGING.md)
BUILD_VENV="${BUILD_VENV:-$ROOT/.venv-build}"
NOTARY_PROFILE="${NOTARY_PROFILE:-spledio-notary}"
UNSIGNED="${UNSIGNED:-0}"
SKIP_TESTS="${SKIP_TESTS:-0}"
DMG_NAME="${APP_NAME}-macOS-arm64.dmg"          # the website links to releases/latest/download/<this name>
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' src/spledio/__init__.py)"

die() { echo "error: $*" >&2; exit 1; }
step() { printf '\n==> %s\n' "$*"; }

[[ "$(uname -s)" == "Darwin" ]] || die "macOS only"
[[ "$(uname -m)" == "arm64" ]] || die "build on an Apple Silicon Mac (numpy/scipy wheels here are arm64)"
[[ -n "$VERSION" ]] || die "could not read __version__ from src/spledio/__init__.py"
if [[ "$UNSIGNED" != "1" ]]; then
  [[ -n "${SIGN_IDENTITY:-}" ]] || die 'set SIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)" (or UNSIGNED=1 for a local test build)'
  security find-identity -v -p codesigning | grep -Fq "$SIGN_IDENTITY" \
    || die "signing identity not found in the keychain: $SIGN_IDENTITY"
  SIGN="$SIGN_IDENTITY"
else
  SIGN="-"   # ad-hoc
fi
echo "Spledio $VERSION · bundle id $BUNDLE_ID · $( [[ "$UNSIGNED" == "1" ]] && echo 'UNSIGNED test build' || echo "signing as: $SIGN" )"

step "Build environment ($BUILD_VENV)"
if [[ ! -x "$BUILD_VENV/bin/python" ]]; then
  "$PYTHON" -m venv "$BUILD_VENV"
fi
"$BUILD_VENV/bin/python" -m pip install --upgrade pip >/dev/null
"$BUILD_VENV/bin/python" -m pip install -r requirements.txt
"$BUILD_VENV/bin/python" -m pip install -r requirements-packaging.txt   # exact PyInstaller pins
"$BUILD_VENV/bin/python" -m pip install . --no-deps
"$BUILD_VENV/bin/python" -m pip freeze > dist-build-freeze.txt

if [[ "$SKIP_TESTS" != "1" ]]; then
  step "Tests (project .venv)"
  [[ -x .venv/bin/python ]] || die "no .venv: run scripts/setup.sh first, or SKIP_TESTS=1"
  QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q
fi

step "Freeze with PyInstaller (onedir .app; never --onefile: Qt and libsndfile must stay replaceable, LGPL)"
rm -rf build dist
"$BUILD_VENV/bin/pyinstaller" --noconfirm --clean --windowed \
  --name "$APP_NAME" \
  --osx-bundle-identifier "$BUNDLE_ID" \
  --icon src/spledio/resources/icons/app/spledio.icns \
  --collect-data spledio \
  --collect-submodules spledio \
  --exclude-module tkinter \
  --exclude-module pytest --exclude-module pytestqt --exclude-module pygments \
  --exclude-module PySide6.QtNetwork --exclude-module PySide6.QtQml \
  --exclude-module PySide6.QtQuick --exclude-module PySide6.QtMultimedia \
  --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtPdf \
  src/spledio/__main__.py
APP="dist/$APP_NAME.app"
[[ -d "$APP" ]] || die "PyInstaller did not produce $APP"

step "Info.plist version + licenses inside the bundle"
PLIST="$APP/Contents/Info.plist"
plutil -replace CFBundleShortVersionString -string "$VERSION" "$PLIST"
plutil -replace CFBundleVersion -string "$VERSION" "$PLIST"
plutil -replace NSHumanReadableCopyright -string "Spledio – free software under GPL-3.0-only" "$PLIST"
mkdir -p "$APP/Contents/Resources/licenses"
cp LICENSE THIRD_PARTY_NOTICES.md "$APP/Contents/Resources/licenses/"
# every license in licenses/ except the development-only tools (not part of the app; see THIRD_PARTY_NOTICES.md)
for f in licenses/*; do
  case "$(basename "$f")" in
    pytest-*|pluggy-*|iniconfig-*|packaging-*|pygments-*|typing_extensions-*) ;;
    *) cp "$f" "$APP/Contents/Resources/licenses/" ;;
  esac
done

step "Remove Qt plugins/frameworks Spledio does not use (PDF, virtual keyboard, TUIO → Pdf/Quick/Qml/Network)"
python3 scripts/prune_qt_bundle.py "$APP"

step "Check what was bundled"
if find "$APP" \( -name "libav*" -o -name "libsw*" -o -name "QtWebEngine*" -o -name "QtPdf*" -o -name "QtMultimedia*" \) | grep -q .; then
  die "unwanted libraries were bundled (FFmpeg / WebEngine / Pdf / Multimedia) – see DEPENDENCY_INVENTORY.md"
fi
for lib in libsndfile libportaudio; do
  find "$APP" -name "${lib}*" | grep -q . || die "$lib missing from the bundle"
done
"$APP/Contents/MacOS/$APP_NAME" --version
SMOKE_WAV="$(mktemp -d)/smoke.wav"
"$BUILD_VENV/bin/python" scripts/make_fixture.py "$SMOKE_WAV" >/dev/null
"$APP/Contents/MacOS/$APP_NAME" --smoke-test "$SMOKE_WAV" || die "smoke test failed (unsigned bundle)"

step "Sign inside-out (hardened runtime, secure timestamp)"
TS=(--timestamp)
ENTITLEMENTS=packaging/entitlements.plist
if [[ "$UNSIGNED" == "1" ]]; then
  TS=()
  # Ad-hoc signatures carry no Team ID, so the hardened runtime's library validation refuses
  # every bundled library ("mapping process and mapped file have different Team IDs").
  # Only for this local test build: keep the hardened runtime (so its memory protections are
  # still exercised by the smoke test) but turn library validation off. A Developer ID build
  # signs every file with the same Team ID and uses packaging/entitlements.plist unchanged.
  ENTITLEMENTS="$(mktemp -d)/entitlements-adhoc.plist"
  cp packaging/entitlements.plist "$ENTITLEMENTS"
  /usr/libexec/PlistBuddy -c "Add :com.apple.security.cs.disable-library-validation bool true" "$ENTITLEMENTS"
fi
# 1) every Mach-O file that is not a framework's main binary
while IFS= read -r -d '' f; do
  if file -b "$f" | grep -q "Mach-O"; then
    codesign --force ${TS[@]+"${TS[@]}"} --options runtime --sign "$SIGN" "$f"
  fi
done < <(find "$APP/Contents" -type f \( -name "*.dylib" -o -name "*.so" -o -perm -u+x \) ! -path "*/MacOS/$APP_NAME" -print0)
# 2) nested frameworks (deepest first)
while IFS= read -r fw; do
  codesign --force ${TS[@]+"${TS[@]}"} --options runtime --sign "$SIGN" "$fw"
done < <(find "$APP/Contents" -name "*.framework" -type d | awk '{ print length, $0 }' | sort -rn | cut -d' ' -f2-)
# 3) the app itself, with entitlements
codesign --force ${TS[@]+"${TS[@]}"} --options runtime --entitlements "$ENTITLEMENTS" --sign "$SIGN" "$APP"
codesign --verify --deep --strict --verbose=2 "$APP"
codesign -d --entitlements - "$APP"
"$APP/Contents/MacOS/$APP_NAME" --smoke-test "$SMOKE_WAV" || die "smoke test failed after signing (hardened runtime / entitlements?)"

step "Disk image"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
DMG="dist/$DMG_NAME"
hdiutil create -volname "$APP_NAME $VERSION" -srcfolder "$STAGE" -ov -format UDZO "$DMG"
codesign --force ${TS[@]+"${TS[@]}"} --sign "$SIGN" "$DMG"

if [[ "$UNSIGNED" == "1" ]]; then
  step "UNSIGNED=1: skipping notarization. This DMG only runs on this Mac; do not publish it."
else
  step "Notarize (Apple notary service; usually a few minutes)"
  OUT="$(xcrun notarytool submit "$DMG" --keychain-profile "$NOTARY_PROFILE" --wait --output-format plist)" || true
  echo "$OUT" > dist/notarization.plist
  STATUS="$(/usr/libexec/PlistBuddy -c 'Print :status' dist/notarization.plist 2>/dev/null || echo unknown)"
  SUB_ID="$(/usr/libexec/PlistBuddy -c 'Print :id' dist/notarization.plist 2>/dev/null || echo '')"
  if [[ "$STATUS" != "Accepted" ]]; then
    [[ -n "$SUB_ID" ]] && xcrun notarytool log "$SUB_ID" --keychain-profile "$NOTARY_PROFILE" dist/notarization-log.json || true
    die "notarization status: $STATUS (details in dist/notarization-log.json)"
  fi
  step "Staple the ticket and check Gatekeeper"
  xcrun stapler staple "$DMG"
  xcrun stapler validate "$DMG"
  spctl -a -vvv -t open --context context:primary-signature "$DMG"
fi

step "Checksum"
(cd dist && shasum -a 256 "$DMG_NAME" | tee "$DMG_NAME.sha256")

cat <<EOF

Done: $DMG

Next (signed builds only):
  1. Test the DMG on a clean Mac or a new user account (open, play, edit, export, save, reopen).
  2. git tag v$VERSION && git push origin v$VERSION
  3. gh release create v$VERSION "$DMG" "$DMG.sha256" --title "Spledio $VERSION" --notes-file <release-notes.md>
     (keep the asset name $DMG_NAME – the website download link depends on it)
EOF
