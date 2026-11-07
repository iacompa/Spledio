"""Render each concept SVG to PNG (1024) and build a contact sheet: every variant at 1024 (shown at
256), 128, 32 and 16 px on a light and a dark desktop, plus the 32 and 16 px renders magnified 4x.

Uses PySide6's QSvgRenderer, the same renderer as scripts/build_icons.py, so what you see here is
what the app ships.

    QT_QPA_PLATFORM=offscreen .venv/bin/python design/icon/tools/contact_sheet.py [A B C ...]
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter  # noqa: E402
from PySide6.QtSvg import QSvgRenderer  # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def render(svg: str, size: int) -> QImage:
    big = max(size * 4, 1024) if size < 1024 else size * 2
    img = QImage(big, big, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    QSvgRenderer(svg).render(p, QRectF(0, 0, big, big))
    p.end()
    return img.scaled(size, size, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)


def main(keys: list[str]) -> None:
    app = QGuiApplication.instance() or QGuiApplication([])  # noqa: F841
    rows = []
    for k in keys:
        svg = os.path.join(HERE, f"variant-{k}", f"Spledio-{k}.svg")
        r = QSvgRenderer(svg)
        if not r.isValid():
            sys.exit(f"invalid SVG: {svg}")
        img = render(svg, 1024)
        img.save(os.path.join(HERE, f"variant-{k}", f"Spledio-{k}-1024.png"))
        rows.append((k, {s: render(svg, s) for s in (256, 128, 32, 16)}))
    W, rowh = 1180, 300
    sheet = QImage(W, rowh * len(rows) + 40, QImage.Format_ARGB32_Premultiplied)
    sheet.fill(QColor("#ECECEF"))
    p = QPainter(sheet)
    p.setRenderHint(QPainter.SmoothPixmapTransform, False)
    f = QFont()
    f.setPointSize(16)
    p.setFont(f)
    for i, (k, imgs) in enumerate(rows):
        y = 20 + i * rowh
        p.fillRect(0, y - 10, W // 2 + 40, rowh - 10, QColor("#F4F4F6"))
        p.fillRect(W // 2 + 40, y - 10, W // 2 - 40, rowh - 10, QColor("#1E1E22"))
        p.setPen(QColor("#222"))
        p.drawText(14, y + 24, k)
        p.drawImage(40, y, imgs[256])
        p.drawImage(320, y + 64, imgs[128])
        p.drawImage(470, y + 112, imgs[32])
        p.drawImage(520, y + 120, imgs[16])
        # dark desktop
        p.drawImage(W // 2 + 60, y + 64, imgs[128])
        p.drawImage(W // 2 + 210, y + 112, imgs[32])
        p.drawImage(W // 2 + 260, y + 120, imgs[16])
        # magnified small sizes
        p.drawImage(W // 2 + 300, y + 40, imgs[32].scaled(128, 128, Qt.IgnoreAspectRatio, Qt.FastTransformation))
        p.drawImage(W // 2 + 450, y + 72, imgs[16].scaled(64, 64, Qt.IgnoreAspectRatio, Qt.FastTransformation))
    p.end()
    out = os.path.join(HERE, "contact-sheet.png")
    sheet.save(out)
    print(out)


if __name__ == "__main__":
    main(sys.argv[1:] or ["A", "B", "C"])
