"""Print the lowest macOS version a frozen .app can run on.

That is the highest "minimum OS" recorded in any Mach-O file inside the bundle
(``LC_BUILD_VERSION minos`` or the older ``LC_VERSION_MIN_MACOSX``), read with
``otool -l``. The release script writes it to ``LSMinimumSystemVersion`` in the
app's Info.plist, so older Macs show a clear "needs macOS X" message instead of a crash.

    python3 scripts/bundle_min_macos.py dist/Spledio.app      # prints e.g. 14.0

The files that set the minimum are listed on stderr.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

MAGICS = (b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xce\xfa\xed\xfe", b"\xbe\xba\xfe\xca")


def macho_files(root: str):
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            p = os.path.join(dirpath, name)
            if os.path.islink(p):
                continue
            try:
                with open(p, "rb") as fh:
                    if fh.read(4) in MAGICS:
                        yield p
            except OSError:
                continue


def min_os(path: str) -> tuple[int, ...] | None:
    """The macOS minimum of the arm64 slice of ``path`` (None if it records none)."""
    out = subprocess.run(["otool", "-arch", "arm64", "-l", path], capture_output=True, text=True).stdout
    found: list[tuple[int, ...]] = []
    cmd = None
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("cmd "):
            cmd = line.split()[1]
        elif cmd == "LC_BUILD_VERSION" and line.startswith("minos "):
            found.append(tuple(int(x) for x in re.findall(r"\d+", line.split()[1])))
        elif cmd == "LC_VERSION_MIN_MACOSX" and line.startswith("version "):
            found.append(tuple(int(x) for x in re.findall(r"\d+", line.split()[1])))
    return max(found) if found else None


def fmt(v: tuple[int, ...]) -> str:
    v = tuple(v) + (0,) * (2 - len(v))  # "14" -> "14.0"
    return ".".join(str(x) for x in v)


def main(app: str) -> int:
    by_file = {p: v for p in macho_files(os.path.join(app, "Contents")) if (v := min_os(p)) is not None}
    if not by_file:
        print(f"no Mach-O files with a minimum macOS found in {app}", file=sys.stderr)
        return 1
    top = max(by_file.values())
    culprits = sorted(os.path.relpath(p, app) for p, v in by_file.items() if v == top)
    print(f"minimum macOS {fmt(top)} is set by {len(culprits)} of {len(by_file)} binaries, e.g.:", file=sys.stderr)
    for c in culprits[:5]:
        print(f"  {c}", file=sys.stderr)
    print(fmt(top))
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1]))
