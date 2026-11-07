#!/usr/bin/env python3
"""Regenerate the raster app icons (PNG, ICNS, ICO) from the SVG masters.

Run from the project root:

    .venv/bin/python scripts/build_icons.py

Inputs (in src/spledio/resources/icons/app/):
    spledio-icon.svg        master artwork, used for 48 px and up
    spledio-icon-small.svg  simplified, pixel-aligned artwork for 16 and 32 px

Outputs (same directory):
    png/icon_<size>.png     16, 32, 48, 64, 128, 256, 512, 1024
    spledio.icns            built by `iconutil` from a temporary .iconset
                            (falls back to a pure-Python writer when
                            `iconutil` is unavailable, e.g. on Linux CI)
    spledio.ico             16, 32, 48, 64, 128, 256 with PNG payloads
                            (valid for Windows Vista and later)

Rendering uses PySide6.QtSvg.QSvgRenderer into a transparent QImage with
antialiasing. Master-art sizes are supersampled and downscaled; the small
art is rendered 1:1 because its edges already sit on the pixel grid.
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage, QPainter  # noqa: E402
from PySide6.QtSvg import QSvgRenderer  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ICON_DIR = ROOT / "src" / "spledio" / "resources" / "icons" / "app"
MASTER_SVG = ICON_DIR / "spledio-icon.svg"
SMALL_SVG = ICON_DIR / "spledio-icon-small.svg"
PNG_DIR = ICON_DIR / "png"
ICNS_PATH = ICON_DIR / "spledio.icns"
ICO_PATH = ICON_DIR / "spledio.ico"

PNG_SIZES = (16, 32, 48, 64, 128, 256, 512, 1024)
ICO_SIZES = (16, 32, 48, 64, 128, 256)
SMALL_ART_MAX = 32  # pixel sizes up to this use the simplified artwork
MAX_SUPERSAMPLE = 4
MAX_RENDER_EDGE = 2048

# macOS .iconset member -> pixel size
ICONSET_MEMBERS = {
    "icon_16x16.png": 16,
    "icon_16x16@2x.png": 32,
    "icon_32x32.png": 32,
    "icon_32x32@2x.png": 64,
    "icon_128x128.png": 128,
    "icon_128x128@2x.png": 256,
    "icon_256x256.png": 256,
    "icon_256x256@2x.png": 512,
    "icon_512x512.png": 512,
    "icon_512x512@2x.png": 1024,
}

# Fallback ICNS writer: OSType -> pixel size (all PNG payloads)
ICNS_TYPES = (
    (b"icp4", 16),
    (b"ic11", 32),
    (b"icp5", 32),
    (b"ic12", 64),
    (b"ic07", 128),
    (b"ic13", 256),
    (b"ic08", 256),
    (b"ic14", 512),
    (b"ic09", 512),
    (b"ic10", 1024),
)


class IconRenderer:
    """Renders the two SVG sources at arbitrary square pixel sizes (cached)."""

    def __init__(self, master: Path, small: Path) -> None:
        self._master = self._load(master)
        self._small = self._load(small)
        self._cache: dict[int, QImage] = {}

    @staticmethod
    def _load(path: Path) -> QSvgRenderer:
        renderer = QSvgRenderer(str(path))
        if not renderer.isValid():
            raise SystemExit(f"error: cannot parse SVG {path}")
        return renderer

    @staticmethod
    def _paint(renderer: QSvgRenderer, edge: int) -> QImage:
        image = QImage(edge, edge, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        renderer.render(painter, QRectF(0, 0, edge, edge))
        painter.end()
        return image

    def image(self, size: int) -> QImage:
        if size not in self._cache:
            if size <= SMALL_ART_MAX:
                image = self._paint(self._small, size)
            else:
                factor = max(1, min(MAX_SUPERSAMPLE, MAX_RENDER_EDGE // size))
                image = self._paint(self._master, size * factor)
                if factor > 1:
                    image = image.scaled(
                        size,
                        size,
                        Qt.AspectRatioMode.IgnoreAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
            self._cache[size] = image.convertToFormat(QImage.Format.Format_ARGB32)
        return self._cache[size]

    def png_bytes(self, size: int) -> bytes:
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not self.image(size).save(buffer, "PNG"):
            raise SystemExit(f"error: PNG encoding failed for {size}px")
        buffer.close()
        return bytes(data)


def write_pngs(icons: IconRenderer) -> None:
    PNG_DIR.mkdir(parents=True, exist_ok=True)
    for size in PNG_SIZES:
        target = PNG_DIR / f"icon_{size}.png"
        target.write_bytes(icons.png_bytes(size))
        print(f"  {target.relative_to(ROOT)}")


def write_icns_with_iconutil(icons: IconRenderer, iconutil: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "spledio.iconset"
        iconset.mkdir()
        for name, size in ICONSET_MEMBERS.items():
            (iconset / name).write_bytes(icons.png_bytes(size))
        subprocess.run(
            [iconutil, "--convert", "icns", "--output", str(ICNS_PATH), str(iconset)],
            check=True,
        )


def write_icns_fallback(icons: IconRenderer) -> None:
    chunks = b""
    for ostype, size in ICNS_TYPES:
        payload = icons.png_bytes(size)
        chunks += ostype + struct.pack(">I", 8 + len(payload)) + payload
    ICNS_PATH.write_bytes(b"icns" + struct.pack(">I", 8 + len(chunks)) + chunks)


def write_ico(icons: IconRenderer) -> None:
    # ICONDIR + ICONDIRENTRY[] followed by PNG-compressed images.
    payloads = [(size, icons.png_bytes(size)) for size in ICO_SIZES]
    header = struct.pack("<HHH", 0, 1, len(payloads))
    offset = len(header) + 16 * len(payloads)
    entries = b""
    blobs = b""
    for size, png in payloads:
        edge = 0 if size >= 256 else size  # 0 means 256 in ICONDIRENTRY
        entries += struct.pack(
            "<BBBBHHII", edge, edge, 0, 0, 1, 32, len(png), offset + len(blobs)
        )
        blobs += png
    ICO_PATH.write_bytes(header + entries + blobs)


def main() -> int:
    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])  # noqa: F841
    icons = IconRenderer(MASTER_SVG, SMALL_SVG)

    print("PNG:")
    write_pngs(icons)

    iconutil = shutil.which("iconutil")
    if iconutil:
        write_icns_with_iconutil(icons, iconutil)
        print(f"ICNS (iconutil): {ICNS_PATH.relative_to(ROOT)}")
    else:
        write_icns_fallback(icons)
        print(f"ICNS (built-in writer, iconutil not found): {ICNS_PATH.relative_to(ROOT)}")

    write_ico(icons)
    print(f"ICO: {ICO_PATH.relative_to(ROOT)} ({', '.join(map(str, ICO_SIZES))} px)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
