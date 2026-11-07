"""Remove Qt plugins Spledio never uses from a frozen .app, then every Qt framework that
nothing left in the bundle links to.

PyInstaller's PySide6 hook collects all image-format and input-method plugins. Two of them
drag in large frameworks Spledio does not use and must not ship (docs/DEPENDENCY_INVENTORY.md):

* ``imageformats/libqpdf``                     → QtPdf
* ``platforminputcontexts/libqtvirtualkeyboardplugin`` → QtVirtualKeyboard, QtQuick, QtQml, …
* ``generic/libqtuiotouchplugin`` (TUIO touch over the network)    → QtNetwork
* image formats Spledio never reads (JPEG, TIFF, WebP, GIF, …; icons are PNG/SVG)

A framework is removed only if no remaining Mach-O file references it (checked with
``otool -L``), repeated until nothing changes. Run before signing:

    python3 scripts/prune_qt_bundle.py dist/Spledio.app
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys

UNUSED_PLUGINS = (
    "imageformats/libqpdf.dylib",
    "platforminputcontexts/libqtvirtualkeyboardplugin.dylib",
    "generic/libqtuiotouchplugin.dylib",  # TUIO network touch input → QtNetwork
    # Image formats Spledio never reads: its icons are PNG (built into QtGui) with an SVG
    # fallback (imageformats/libqsvg, kept). Dropping these also drops the libjpeg-turbo,
    # libtiff and libwebp code (and their notices) compiled into them.
    *(f"imageformats/libq{n}.dylib" for n in ("gif", "icns", "ico", "jpeg", "macheif", "macjp2",
                                               "tga", "tiff", "wbmp", "webp")),
)
_DEP = re.compile(r"@rpath/(Qt\w+)(?:\.framework/[^\s]*)?\s")


def macho_files(root: str):
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            p = os.path.join(dirpath, name)
            if os.path.islink(p):
                continue
            try:
                with open(p, "rb") as fh:
                    magic = fh.read(4)
            except OSError:
                continue
            if magic in (b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xce\xfa\xed\xfe", b"\xbe\xba\xfe\xca"):
                yield p


def qt_deps(path: str) -> set[str]:
    out = subprocess.run(["otool", "-L", path], capture_output=True, text=True).stdout
    return set(_DEP.findall(out + "\n"))


def main(app: str) -> int:
    contents = os.path.join(app, "Contents")
    qt_root = os.path.join(contents, "Frameworks", "PySide6", "Qt")
    plugins = os.path.join(qt_root, "plugins")
    lib = os.path.join(qt_root, "lib")
    if not os.path.isdir(lib):
        print(f"no Qt lib folder in {app}", file=sys.stderr)
        return 1
    for rel in UNUSED_PLUGINS:
        p = os.path.join(plugins, rel)
        if os.path.exists(p):
            os.remove(p)
            print(f"removed plugin {rel}")
    for d in os.listdir(plugins):
        dp = os.path.join(plugins, d)
        if os.path.isdir(dp) and not os.listdir(dp):
            os.rmdir(dp)
    removed: list[str] = []
    while True:
        present = {n[: -len(".framework")] for n in os.listdir(lib) if n.endswith(".framework")}
        used: set[str] = set()
        for f in macho_files(contents):
            own = next((n for n in present if f"/{n}.framework/" in f), None)
            used |= {d for d in qt_deps(f) if d != own}
        unused = sorted(present - used)
        if not unused:
            break
        for name in unused:
            shutil.rmtree(os.path.join(lib, f"{name}.framework"))
            removed.append(name)
    # PyInstaller also leaves top-level symlinks (Contents/Frameworks/QtPdf -> …); drop the dangling ones
    for dirpath, dirs, files in os.walk(contents):
        for name in files + dirs:
            p = os.path.join(dirpath, name)
            if os.path.islink(p) and not os.path.exists(p):
                os.remove(p)
    print("removed unused Qt frameworks: " + (", ".join(removed) or "none"))
    left = sorted(n[: -len(".framework")] for n in os.listdir(lib) if n.endswith(".framework"))
    print("Qt frameworks kept: " + ", ".join(left))
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1]))
