#!/usr/bin/env bash
# Get the python.org CPython used to build the Mac release – without installing it.
#
# Spledio's .app carries its own Python inside; users never install Python. The build Mac needs
# a python.org CPython (not Homebrew's, which only runs on the newest macOS), so the app runs on
# older macOS too. This script downloads the official installer, checks its SHA-256 and Apple
# signature, and only *unpacks* the framework into a private folder (no admin password, nothing
# changes outside that folder). Run it, then build:
#
#   PYTHON="$(scripts/get_build_python.sh)" UNSIGNED=1 scripts/release_macos.sh
#
# Prints the path of the python executable on stdout; progress goes to stderr.
set -euo pipefail

PY_VERSION="3.14.10"
PY_SHA256="286c7018aa2730f6747ca433dccdc2b31be45f078a9ea0ffa44d5f06368fd2d7"   # from python.org's release file list
PY_SIGNER="Developer ID Installer: Python Software Foundation (BMM5U3QVKW)"
PY_MINOR="${PY_VERSION%.*}"
CACHE="${SPLEDIO_BUILD_CACHE:-$HOME/Library/Caches/spledio-build}"
PKG_NAME="python-${PY_VERSION}-macos11.pkg"
URL="https://www.python.org/ftp/python/${PY_VERSION}/${PKG_NAME}"
DEST="$CACHE/python-$PY_VERSION"
FW="$DEST/Python.framework"
PY="$FW/Versions/$PY_MINOR/bin/python$PY_MINOR"

say() { echo "$*" >&2; }
die() { echo "error: $*" >&2; exit 1; }

if [[ -x "$PY" ]] && "$PY" -c 'import sys, ssl, ctypes, venv' 2>/dev/null; then
  say "using $PY (already prepared)"
  echo "$PY"
  exit 0
fi

mkdir -p "$CACHE/download"
PKG="$CACHE/download/$PKG_NAME"
if [[ ! -f "$PKG" ]] || ! echo "$PY_SHA256  $PKG" | shasum -a 256 -c --status; then
  say "downloading $URL"
  curl -fL --proto '=https' --tlsv1.2 -o "$PKG.part" "$URL"
  mv "$PKG.part" "$PKG"
fi
echo "$PY_SHA256  $PKG" | shasum -a 256 -c --status || die "SHA-256 of $PKG does not match python.org's"
pkgutil --check-signature "$PKG" | grep -Fq "$PY_SIGNER" || die "$PKG is not signed by $PY_SIGNER"
say "checksum and signature OK ($PY_SIGNER)"

WORK="$(mktemp -d "$CACHE/expand.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT
pkgutil --expand-full "$PKG" "$WORK/pkg" >/dev/null
[[ -d "$WORK/pkg/Python_Framework.pkg/Payload/Versions/$PY_MINOR" ]] || die "unexpected installer layout"
rm -rf "$DEST"
mkdir -p "$DEST"
mv "$WORK/pkg/Python_Framework.pkg/Payload" "$FW"

# The installer's binaries refer to each other by their install location
# (/Library/Frameworks/Python.framework/...). Point those references at the unpacked copy with
# @loader_path-relative names, then re-sign the changed files ad hoc (an edited file's signature is
# no longer valid). PyInstaller copies and re-signs everything again when it builds the app.
/usr/bin/python3 -I - "$FW" "$PY_MINOR" >&2 <<'PYCODE'
import os, subprocess, sys

fw, minor = sys.argv[1], sys.argv[2]
old_root = f"/Library/Frameworks/Python.framework/Versions/{minor}"
new_root = os.path.join(fw, "Versions", minor)
magics = (b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xce\xfa\xed\xfe", b"\xbe\xba\xfe\xca")

def macho(root):
    for d, _, files in os.walk(root):
        for n in files:
            p = os.path.join(d, n)
            if os.path.islink(p):
                continue
            with open(p, "rb") as fh:
                if fh.read(4) in magics:
                    yield p

changed = 0
for f in macho(new_root):
    out = subprocess.run(["otool", "-L", f], capture_output=True, text=True).stdout.splitlines()
    deps = {ln.strip().split(" (")[0] for ln in out[1:] if ln.startswith("\t")}
    ident = subprocess.run(["otool", "-D", f], capture_output=True, text=True).stdout.splitlines()[1:]
    args = []
    for dep in sorted(deps):
        if dep.startswith(old_root + "/"):
            target = os.path.join(new_root, dep[len(old_root) + 1:])
            rel = os.path.relpath(target, os.path.dirname(f))
            if dep in {i.strip() for i in ident}:
                args += ["-id", "@loader_path/" + os.path.basename(f)]
            else:
                args += ["-change", dep, "@loader_path/" + rel]
    if args:
        subprocess.run(["install_name_tool", *args, f], check=True, capture_output=True)
        subprocess.run(["codesign", "--force", "--sign", "-", f], check=True, capture_output=True)
        changed += 1
print(f"relocated {changed} binaries into {new_root}")
PYCODE

"$PY" -c 'import sys, ssl, ctypes, venv; print("Python", sys.version.split()[0], "ready at", sys.base_prefix)' >&2 \
  || die "the unpacked Python does not start"
echo "$PY"
