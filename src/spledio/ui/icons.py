"""Original line icons drawn with QPainter (no image files, no vendor artwork).

Every glyph is drawn on a 24 x 24 unit grid with round caps and joins:
light-grey strokes (#d8dde6) on a transparent background, the accent blue
(#4fb3ff) marking the part that matters (the selected band, the new layer,
the plus/minus of a brush ...). Each icon is rendered at several pixel sizes
(16 ... 64 px, i.e. 1x and 2x for the usual 16/20/24/32 px toolbar sizes) so
Qt picks a sharp pixmap on HiDPI screens instead of scaling a small one up.

``icon(name)`` returns a cached :class:`QIcon`, or ``None`` for an unknown
name so callers can fall back to text. ``app_icon()`` loads the application
icon files from ``resources/icons/app`` (missing files are tolerated).
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QBrush, QColor, QGuiApplication, QIcon, QPainter, QPainterPath, QPen, QPixmap

FG = "#d8dde6"
ACCENT = "#4fb3ff"
GRID = 24.0
STROKE = 1.7
PIXEL_SIZES = (16, 20, 24, 32, 40, 48, 64)

_cache: dict[str, QIcon] = {}
_app_icon: QIcon | None = None


class _Pen:
    """Small drawing helper working in 24-unit grid coordinates."""

    def __init__(self, p: QPainter, px: int):
        self.p = p
        self.px = px
        # slightly heavier strokes at the smallest sizes so they stay legible
        self.wscale = 1.18 if px <= 20 else 1.0

    def _pen(self, color: str, w: float, dash: tuple[float, ...] | None = None) -> QPen:
        pen = QPen(QColor(color), w * self.wscale, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        if dash:
            pen.setDashPattern([d / max(w * self.wscale, 0.1) for d in dash])
        return pen

    def stroke(self, path: QPainterPath, color: str = FG, w: float = STROKE, dash=None) -> None:
        self.p.setPen(self._pen(color, w, dash))
        self.p.setBrush(Qt.NoBrush)
        self.p.drawPath(path)

    def fill(self, path: QPainterPath, color: str = FG, alpha: float = 1.0) -> None:
        c = QColor(color)
        c.setAlphaF(alpha)
        self.p.setPen(Qt.NoPen)
        self.p.setBrush(QBrush(c))
        self.p.drawPath(path)

    def fill_stroke(self, path: QPainterPath, color: str = FG, w: float = STROKE, alpha: float = 1.0) -> None:
        self.fill(path, color, alpha)
        self.stroke(path, color, w)

    def line(self, *pts: tuple[float, float], color: str = FG, w: float = STROKE, dash=None) -> None:
        self.stroke(poly(pts), color, w, dash)

    def dot(self, x: float, y: float, r: float, color: str = FG) -> None:
        path = QPainterPath()
        path.addEllipse(QPointF(x, y), r, r)
        self.fill(path, color)


# ------------------------------------------------------------------ path helpers
def poly(pts, closed: bool = False) -> QPainterPath:
    path = QPainterPath()
    pts = list(pts)
    path.moveTo(*pts[0])
    for pt in pts[1:]:
        path.lineTo(*pt)
    if closed:
        path.closeSubpath()
    return path


def rrect(x: float, y: float, w: float, h: float, r: float = 0.0) -> QPainterPath:
    path = QPainterPath()
    if r > 0:
        path.addRoundedRect(QRectF(x, y, w, h), r, r)
    else:
        path.addRect(QRectF(x, y, w, h))
    return path


def ellipse(cx: float, cy: float, rx: float, ry: float | None = None) -> QPainterPath:
    path = QPainterPath()
    path.addEllipse(QPointF(cx, cy), rx, rx if ry is None else ry)
    return path


def arc(cx: float, cy: float, r: float, start_deg: float, span_deg: float) -> QPainterPath:
    """Arc (Qt convention: degrees counter-clockwise from 3 o'clock)."""
    path = QPainterPath()
    rect = QRectF(cx - r, cy - r, 2 * r, 2 * r)
    path.arcMoveTo(rect, start_deg)
    path.arcTo(rect, start_deg, span_deg)
    return path


def head(tip: tuple[float, float], angle_deg: float, size: float = 4.0, spread: float = 42.0) -> QPainterPath:
    """Open arrow head with its tip at ``tip`` pointing along ``angle_deg`` (screen coords, 0 = right, 90 = down)."""
    tx, ty = tip
    pts = []
    for s in (-spread, spread):
        a = math.radians(angle_deg + 180 + s)
        pts.append((tx + size * math.cos(a), ty + size * math.sin(a)))
    return poly([pts[0], tip, pts[1]])


def bezier(start, *segments) -> QPainterPath:
    """segments: ('L', x, y) | ('Q', cx, cy, x, y) | ('C', c1x, c1y, c2x, c2y, x, y) | ('M', x, y) | ('Z',)."""
    path = QPainterPath()
    path.moveTo(*start)
    for seg in segments:
        kind, vals = seg[0], seg[1:]
        if kind == "L":
            path.lineTo(*vals)
        elif kind == "Q":
            path.quadTo(*vals)
        elif kind == "C":
            path.cubicTo(*vals)
        elif kind == "M":
            path.moveTo(*vals)
        elif kind == "Z":
            path.closeSubpath()
    return path


# ------------------------------------------------------------------ shared glyph parts
def _speaker(d: _Pen) -> None:
    d.stroke(poly([(2.8, 9.4), (6.0, 9.4), (10.2, 5.6), (10.2, 18.4), (6.0, 14.6), (2.8, 14.6)], closed=True))


def _magnifier(d: _Pen) -> None:
    d.stroke(ellipse(10.2, 10.2, 6.2))
    d.line((14.8, 14.8), (20.6, 20.6), w=2.4)


def _layers(d: _Pen, ox: float = -2.0, oy: float = 1.0) -> None:
    def pt(x, y):
        return (x + ox, y + oy)

    d.stroke(poly([pt(12, 4.5), pt(20, 8.5), pt(12, 12.5), pt(4, 8.5)], closed=True))
    d.line(pt(4, 12.2), pt(12, 16.2), pt(20, 12.2))
    d.line(pt(4, 15.9), pt(12, 19.9), pt(20, 15.9))


def _brush(d: _Pen) -> None:
    d.line((21.0, 3.2), (13.6, 10.6), w=2.3)
    tip = bezier((13.8, 10.4), ("C", 12.0, 8.6, 9.0, 9.4, 8.3, 12.2), ("C", 7.7, 14.6, 6.4, 16.6, 3.6, 17.6),
                 ("C", 6.8, 20.6, 11.6, 20.2, 13.4, 17.0), ("C", 14.6, 14.9, 15.4, 12.2, 13.8, 10.4), ("Z",))
    d.fill_stroke(tip, FG, w=1.2, alpha=0.9)


def _corners(d: _Pen, x0=3.0, y0=3.5, x1=21.0, y1=20.5, arm=4.6, color=FG) -> None:
    d.line((x0, y0 + arm), (x0, y0), (x0 + arm, y0), color=color)
    d.line((x1 - arm, y0), (x1, y0), (x1, y0 + arm), color=color)
    d.line((x1, y1 - arm), (x1, y1), (x1 - arm, y1), color=color)
    d.line((x0 + arm, y1), (x0, y1), (x0, y1 - arm), color=color)


def _mode_squares() -> tuple[QPainterPath, QPainterPath]:
    return rrect(3.5, 3.5, 11.5, 11.5, 1.6), rrect(9.0, 9.0, 11.5, 11.5, 1.6)


# ------------------------------------------------------------------ glyphs
def _open(d):
    d.stroke(poly([(3.0, 18.8), (3.0, 6.2), (8.8, 6.2), (10.8, 8.4), (18.6, 8.4), (18.6, 11.2)]))
    d.stroke(poly([(3.0, 18.8), (6.4, 11.4), (21.6, 11.4), (18.2, 18.8)], closed=True))


def _save(d):
    d.line((4.0, 13.5), (4.0, 19.8), (20.0, 19.8), (20.0, 13.5))
    d.line((12.0, 3.6), (12.0, 14.6), color=ACCENT)
    d.stroke(head((12.0, 15.0), 90, 4.6), ACCENT)


def _export(d):
    d.line((12.5, 4.5), (6.0, 4.5), (4.5, 6.0), (4.5, 18.0), (6.0, 19.5), (18.0, 19.5), (19.5, 18.0), (19.5, 11.5))
    d.line((10.5, 13.5), (20.2, 3.8), color=ACCENT)
    d.line((14.4, 3.8), (20.2, 3.8), (20.2, 9.6), color=ACCENT)


def _undo(d):
    d.stroke(bezier((5.2, 9.0), ("L", 14.0, 9.0), ("C", 17.6, 9.0, 20.0, 11.6, 20.0, 14.6),
                    ("C", 20.0, 17.7, 17.6, 20.0, 14.0, 20.0), ("L", 9.5, 20.0)))
    d.stroke(head((4.8, 9.0), 180, 4.4))


def _redo(d):
    d.stroke(bezier((18.8, 9.0), ("L", 10.0, 9.0), ("C", 6.4, 9.0, 4.0, 11.6, 4.0, 14.6),
                    ("C", 4.0, 17.7, 6.4, 20.0, 10.0, 20.0), ("L", 14.5, 20.0)))
    d.stroke(head((19.2, 9.0), 0, 4.4))


def _play(d):
    d.fill_stroke(poly([(7.2, 4.6), (19.4, 12.0), (7.2, 19.4)], closed=True), FG, w=1.4)


def _pause(d):
    d.fill(rrect(6.2, 4.8, 4.0, 14.4, 1.0))
    d.fill(rrect(13.8, 4.8, 4.0, 14.4, 1.0))


def _stop(d):
    d.fill(rrect(5.8, 5.8, 12.4, 12.4, 1.6))


def _to_start(d):
    d.line((5.6, 5.0), (5.6, 19.0), w=2.3)
    d.fill_stroke(poly([(18.6, 5.2), (8.6, 12.0), (18.6, 18.8)], closed=True), FG, w=1.4)


def _loop(d):
    d.stroke(bezier((4.6, 13.4), ("L", 4.6, 10.2), ("Q", 4.6, 7.0, 7.8, 7.0), ("L", 18.0, 7.0)))
    d.stroke(head((18.6, 7.0), 0, 3.8))
    d.stroke(bezier((19.4, 10.6), ("L", 19.4, 13.8), ("Q", 19.4, 17.0, 16.2, 17.0), ("L", 6.0, 17.0)))
    d.stroke(head((5.4, 17.0), 180, 3.8))


def _hear_selected(d):
    _speaker(d)
    d.stroke(rrect(13.2, 4.6, 8.0, 14.8, 1.2), FG, w=1.2)
    d.fill(rrect(13.2, 9.6, 8.0, 4.8, 0.6), ACCENT)


def _hear_all(d):
    _speaker(d)
    d.stroke(arc(10.6, 12.0, 4.4, -48, 96))
    d.stroke(arc(10.6, 12.0, 8.2, -52, 104))


def _hear_remainder(d):
    _speaker(d)
    d.fill(rrect(13.2, 4.6, 8.0, 4.4, 0.8), FG, alpha=0.75)
    d.fill(rrect(13.2, 15.0, 8.0, 4.4, 0.8), FG, alpha=0.75)
    d.stroke(rrect(13.2, 9.8, 8.0, 4.4, 0.4), ACCENT, w=1.2, dash=(1.6, 1.4))


def _hear_removed(d):
    _speaker(d)
    d.stroke(ellipse(17.0, 12.0, 4.6), ACCENT)
    d.line((14.6, 12.0), (19.4, 12.0), color=ACCENT, w=2.0)


def _ab(d):
    d.line((2.6, 19.0), (6.8, 5.0), (11.0, 19.0))
    d.line((4.2, 14.2), (9.4, 14.2))
    b = bezier((13.8, 5.0), ("L", 13.8, 19.0), ("L", 17.8, 19.0), ("C", 20.2, 19.0, 21.4, 17.6, 21.4, 15.6),
               ("C", 21.4, 13.6, 19.8, 12.0, 17.6, 12.0), ("L", 13.8, 12.0), ("M", 13.8, 5.0), ("L", 17.4, 5.0),
               ("C", 19.4, 5.0, 20.6, 6.4, 20.6, 8.5), ("C", 20.6, 10.6, 19.2, 12.0, 17.6, 12.0))
    d.stroke(b, ACCENT)


def _gain(d):
    d.stroke(poly([(3.0, 19.0), (21.0, 19.0), (21.0, 5.0)], closed=True))
    d.line((14.0, 18.6), (14.0, 8.4), color=ACCENT, w=2.2)


def _mute(d):
    _speaker(d)
    d.line((14.0, 9.0), (20.4, 15.4), w=1.9)
    d.line((20.4, 9.0), (14.0, 15.4), w=1.9)


def _delete(d):
    d.line((4.0, 6.6), (20.0, 6.6))
    d.line((9.4, 6.6), (9.4, 4.2), (14.6, 4.2), (14.6, 6.6))
    d.line((6.2, 6.6), (7.2, 20.2), (16.8, 20.2), (17.8, 6.6))
    d.line((10.2, 10.2), (10.2, 16.8), w=1.3)
    d.line((13.8, 10.2), (13.8, 16.8), w=1.3)


def _extract(d):
    d.stroke(rrect(3.0, 15.0, 18.0, 5.2, 1.2))
    d.stroke(rrect(3.0, 3.4, 18.0, 5.2, 1.2), ACCENT)
    d.line((12.0, 13.6), (12.0, 10.0), color=ACCENT)
    d.stroke(head((12.0, 9.8), -90, 3.4), ACCENT)


def _copy_layer(d):
    d.line((8.2, 7.8), (8.2, 3.6), (20.4, 3.6), (20.4, 15.8), (16.2, 15.8))
    d.stroke(rrect(3.6, 8.2, 12.4, 12.4, 1.4))
    d.line((9.8, 11.4), (9.8, 17.4), color=ACCENT, w=1.9)
    d.line((6.8, 14.4), (12.8, 14.4), color=ACCENT, w=1.9)


def _preview(d):
    d.stroke(bezier((5.0, 14.0), ("L", 5.0, 12.0), ("C", 5.0, 7.4, 8.0, 4.4, 12.0, 4.4),
                    ("C", 16.0, 4.4, 19.0, 7.4, 19.0, 12.0), ("L", 19.0, 14.0)))
    d.fill(rrect(3.2, 12.8, 4.4, 7.0, 1.6))
    d.fill(rrect(16.4, 12.8, 4.4, 7.0, 1.6))
    d.fill(poly([(10.4, 10.0), (14.4, 12.6), (10.4, 15.2)], closed=True), ACCENT)


def _bypass(d):
    d.line((2.6, 17.0), (8.6, 17.0))
    d.stroke(rrect(8.6, 13.8, 6.8, 6.4, 1.0))
    d.line((15.4, 17.0), (21.4, 17.0))
    d.stroke(bezier((4.6, 17.0), ("L", 4.6, 9.6), ("Q", 4.6, 6.6, 7.6, 6.6), ("L", 16.4, 6.6),
                    ("Q", 19.4, 6.6, 19.4, 9.6), ("L", 19.4, 13.6)), ACCENT)
    d.stroke(head((19.4, 14.2), 90, 3.4), ACCENT)


def _apply(d):
    d.line((4.4, 12.6), (9.6, 17.8), (19.8, 6.4), color=ACCENT, w=2.4)


def _cancel(d):
    d.line((6.2, 6.2), (17.8, 17.8), w=2.2)
    d.line((17.8, 6.2), (6.2, 17.8), w=2.2)


def _zoom_in(d):
    _magnifier(d)
    d.line((10.2, 7.2), (10.2, 13.2), color=ACCENT)
    d.line((7.2, 10.2), (13.2, 10.2), color=ACCENT)


def _zoom_out(d):
    _magnifier(d)
    d.line((7.2, 10.2), (13.2, 10.2), color=ACCENT)


def _zoom(d):
    _magnifier(d)


def _fit_selection(d):
    _corners(d)
    d.stroke(rrect(7.8, 9.0, 8.4, 6.0, 0.8), ACCENT, w=1.5)


def _fit_project(d):
    _corners(d)
    d.line((7.0, 12.0), (17.0, 12.0), color=ACCENT)
    d.stroke(head((6.6, 12.0), 180, 3.2), ACCENT)
    d.stroke(head((17.4, 12.0), 0, 3.2), ACCENT)


def _reset_view(d):
    d.stroke(arc(12.0, 12.0, 7.6, 100, 290))
    a = math.radians(100 + 290)
    tip = (12.0 + 7.6 * math.cos(a), 12.0 - 7.6 * math.sin(a))
    # tangent of a counter-clockwise arc, in screen coordinates (y down)
    d.stroke(head(tip, math.degrees(math.atan2(-math.cos(a), -math.sin(a))), 3.6))
    d.stroke(rrect(9.4, 9.8, 5.2, 4.4, 0.6), ACCENT, w=1.4)


def _focus(d):
    d.stroke(ellipse(12.0, 12.0, 5.8))
    d.line((12.0, 2.6), (12.0, 6.4))
    d.line((12.0, 17.6), (12.0, 21.4))
    d.line((2.6, 12.0), (6.4, 12.0))
    d.line((17.6, 12.0), (21.4, 12.0))
    d.dot(12.0, 12.0, 1.8, ACCENT)


def _layer_new(d):
    _layers(d)
    d.line((19.4, 1.8), (19.4, 8.2), color=ACCENT, w=1.9)
    d.line((16.2, 5.0), (22.6, 5.0), color=ACCENT, w=1.9)


def _layer_dup(d):
    _layers(d)
    d.stroke(rrect(16.8, 1.6, 4.6, 4.6, 0.6), ACCENT, w=1.3)
    d.stroke(rrect(18.6, 3.6, 4.6, 4.6, 0.6), ACCENT, w=1.3)


def _layer_delete(d):
    _layers(d)
    d.line((16.8, 2.2), (22.0, 7.4), color=ACCENT, w=1.9)
    d.line((22.0, 2.2), (16.8, 7.4), color=ACCENT, w=1.9)


def _up(d):
    d.line((12.0, 20.0), (12.0, 5.0))
    d.stroke(head((12.0, 4.4), -90, 6.4, 45))


def _down(d):
    d.line((12.0, 4.0), (12.0, 19.0))
    d.stroke(head((12.0, 19.6), 90, 6.4, 45))


def _merge(d):
    d.stroke(bezier((5.0, 3.6), ("L", 5.0, 7.0), ("C", 5.0, 11.0, 12.0, 11.0, 12.0, 14.6)))
    d.stroke(bezier((19.0, 3.6), ("L", 19.0, 7.0), ("C", 19.0, 11.0, 12.0, 11.0, 12.0, 14.6)))
    d.line((12.0, 14.0), (12.0, 20.0), color=ACCENT)
    d.stroke(head((12.0, 20.6), 90, 3.8), ACCENT)


def _time(d):
    d.line((3.6, 6.0), (3.6, 18.0))
    d.line((20.4, 6.0), (20.4, 18.0))
    d.line((6.6, 12.0), (17.4, 12.0), color=ACCENT)
    d.stroke(head((6.2, 12.0), 180, 3.4), ACCENT)
    d.stroke(head((17.8, 12.0), 0, 3.4), ACCENT)


def _freq(d):
    d.line((6.0, 3.6), (18.0, 3.6))
    d.line((6.0, 20.4), (18.0, 20.4))
    d.line((12.0, 6.6), (12.0, 17.4), color=ACCENT)
    d.stroke(head((12.0, 6.2), -90, 3.4), ACCENT)
    d.stroke(head((12.0, 17.8), 90, 3.4), ACCENT)


def _rect(d):
    d.stroke(rrect(4.4, 6.2, 15.2, 11.6))
    for x, y in ((4.4, 6.2), (19.6, 6.2), (19.6, 17.8), (4.4, 17.8)):
        d.fill(rrect(x - 1.6, y - 1.6, 3.2, 3.2, 0.4), ACCENT)


def _ellipse(d):
    d.stroke(ellipse(12.0, 12.0, 8.6, 6.2))
    for x, y in ((3.4, 12.0), (20.6, 12.0)):
        d.dot(x, y, 1.6, ACCENT)


def _lasso(d):
    d.stroke(bezier((12.0, 4.0), ("C", 17.6, 4.0, 21.2, 6.6, 21.2, 9.8), ("C", 21.2, 13.2, 16.8, 15.0, 12.0, 15.0),
                    ("C", 7.2, 15.0, 2.8, 13.2, 2.8, 9.8), ("C", 2.8, 6.6, 6.4, 4.0, 12.0, 4.0)))
    d.stroke(bezier((7.4, 14.2), ("C", 6.2, 16.6, 7.6, 18.2, 9.8, 18.6), ("C", 11.8, 19.0, 11.6, 21.2, 9.2, 21.6)),
             ACCENT)
    d.dot(7.4, 14.2, 1.5, ACCENT)


def _polygon(d):
    pts = [(5.0, 17.6), (3.6, 7.4), (12.6, 3.8), (20.4, 9.6), (15.6, 19.6)]
    d.stroke(poly(pts, closed=True))
    for x, y in pts:
        d.dot(x, y, 1.7, ACCENT)


def _sel_brush(d):
    _brush(d)
    d.stroke(rrect(2.2, 2.2, 7.0, 7.0, 0.6), ACCENT, w=1.3, dash=(1.5, 1.3))


def _transform(d):
    d.line((12.0, 3.4), (12.0, 20.6))
    d.line((3.4, 12.0), (20.6, 12.0))
    for tip, ang in (((12.0, 3.0), -90), ((12.0, 21.0), 90), ((3.0, 12.0), 180), ((21.0, 12.0), 0)):
        d.stroke(head(tip, ang, 3.4), ACCENT)


def _reduce_brush(d):
    _brush(d)
    d.line((2.4, 5.4), (9.0, 5.4), color=ACCENT, w=2.1)


def _amplify_brush(d):
    _brush(d)
    d.line((2.4, 5.4), (9.0, 5.4), color=ACCENT, w=2.1)
    d.line((5.7, 2.1), (5.7, 8.7), color=ACCENT, w=2.1)


def _transfer_brush(d):
    _brush(d)
    d.line((2.0, 5.4), (9.0, 5.4), color=ACCENT, w=1.8)
    d.stroke(head((9.6, 5.4), 0, 3.0), ACCENT, w=1.8)


def _hand(d):
    path = bezier(
        (7.6, 20.6),
        ("C", 6.4, 19.0, 4.8, 17.0, 3.6, 14.8), ("C", 3.0, 13.8, 3.5, 12.7, 4.5, 12.5),
        ("C", 5.4, 12.3, 6.0, 12.9, 6.6, 13.7), ("L", 8.0, 15.6), ("L", 8.0, 6.6),
        ("C", 8.0, 5.8, 8.6, 5.2, 9.3, 5.2), ("C", 10.0, 5.2, 10.6, 5.8, 10.6, 6.6), ("L", 10.6, 11.4),
        ("L", 10.6, 4.6), ("C", 10.6, 3.8, 11.2, 3.2, 11.9, 3.2), ("C", 12.6, 3.2, 13.2, 3.8, 13.2, 4.6),
        ("L", 13.2, 11.4), ("L", 13.2, 5.6), ("C", 13.2, 4.8, 13.8, 4.2, 14.5, 4.2),
        ("C", 15.2, 4.2, 15.8, 4.8, 15.8, 5.6), ("L", 15.8, 11.8), ("L", 15.8, 8.0),
        ("C", 15.8, 7.2, 16.4, 6.6, 17.1, 6.6), ("C", 17.8, 6.6, 18.4, 7.2, 18.4, 8.0), ("L", 18.4, 15.0),
        ("C", 18.4, 18.4, 16.4, 20.6, 13.4, 20.6), ("Z",),
    )
    d.stroke(path, w=1.5)


def _mode_replace(d):
    d.fill_stroke(rrect(5.0, 5.0, 14.0, 14.0, 1.8), ACCENT, w=1.4, alpha=0.55)


def _mode_add(d):
    a, b = _mode_squares()
    u = a.united(b)
    d.fill(u, ACCENT, alpha=0.55)
    d.stroke(u, FG, w=1.4)


def _mode_subtract(d):
    a, b = _mode_squares()
    d.fill(a.subtracted(b), ACCENT, alpha=0.55)
    d.stroke(a.subtracted(b), FG, w=1.4)
    d.stroke(b, FG, w=1.1, dash=(1.6, 1.4))


def _mode_intersect(d):
    a, b = _mode_squares()
    d.stroke(a, FG, w=1.1, dash=(1.6, 1.4))
    d.stroke(b, FG, w=1.1, dash=(1.6, 1.4))
    i = a.intersected(b)
    d.fill(i, ACCENT, alpha=0.8)
    d.stroke(i, FG, w=1.4)


def _invert(d):
    sq = rrect(3.6, 3.6, 16.8, 16.8, 2.0)
    hole = ellipse(12.0, 12.0, 4.8)
    d.fill(sq.subtracted(hole), ACCENT, alpha=0.55)
    d.stroke(sq, FG, w=1.4)
    d.stroke(hole, FG, w=1.4)


def _lock(d):
    d.stroke(bezier((8.0, 10.8), ("L", 8.0, 8.0), ("C", 8.0, 5.5, 9.8, 3.8, 12.0, 3.8),
                    ("C", 14.2, 3.8, 16.0, 5.5, 16.0, 8.0), ("L", 16.0, 10.8)))
    d.stroke(rrect(5.2, 10.8, 13.6, 9.6, 1.6))
    d.dot(12.0, 14.8, 1.4, ACCENT)
    d.line((12.0, 15.2), (12.0, 17.6), color=ACCENT, w=1.6)


def _solo(d):
    d.stroke(rrect(3.4, 3.4, 17.2, 17.2, 3.2))
    s = bezier((15.4, 8.6), ("C", 14.8, 7.4, 13.6, 6.9, 12.0, 6.9), ("C", 10.0, 6.9, 8.6, 7.9, 8.6, 9.4),
               ("C", 8.6, 12.6, 15.6, 11.2, 15.6, 14.7), ("C", 15.6, 16.3, 14.0, 17.2, 12.0, 17.2),
               ("C", 10.2, 17.2, 8.8, 16.5, 8.3, 15.3))
    d.stroke(s, ACCENT, w=1.9)


def _mute_layer(d):
    d.stroke(rrect(3.4, 3.4, 17.2, 17.2, 3.2))
    d.line((7.6, 16.8), (7.6, 7.2), (12.0, 12.8), (16.4, 7.2), (16.4, 16.8), color=ACCENT, w=1.9)


def _polarity(d):
    d.stroke(ellipse(12.0, 12.0, 6.6))
    d.line((5.0, 19.0), (19.0, 5.0), color=ACCENT, w=1.9)


def _eye(d):
    d.stroke(bezier((2.4, 12.0), ("C", 5.0, 7.6, 8.4, 5.6, 12.0, 5.6), ("C", 15.6, 5.6, 19.0, 7.6, 21.6, 12.0),
                    ("C", 19.0, 16.4, 15.6, 18.4, 12.0, 18.4), ("C", 8.4, 18.4, 5.0, 16.4, 2.4, 12.0), ("Z",)))
    d.fill(ellipse(12.0, 12.0, 3.2), ACCENT)


def _commands(d):
    d.stroke(rrect(2.8, 4.8, 18.4, 14.4, 2.2))
    d.line((7.0, 9.4), (10.2, 12.0), (7.0, 14.6), color=ACCENT, w=1.9)
    d.line((12.4, 14.6), (17.2, 14.6))


def _help(d):
    d.stroke(ellipse(12.0, 12.0, 8.8))
    d.stroke(bezier((9.2, 9.6), ("C", 9.2, 7.9, 10.5, 6.8, 12.1, 6.8), ("C", 13.8, 6.8, 15.0, 7.9, 15.0, 9.4),
                    ("C", 15.0, 11.7, 12.0, 11.8, 12.0, 14.2)), ACCENT, w=1.9)
    d.dot(12.0, 17.3, 1.2, ACCENT)


_GLYPHS: dict[str, Callable[[_Pen], None]] = {
    "open": _open, "save": _save, "export": _export, "undo": _undo, "redo": _redo,
    "play": _play, "pause": _pause, "stop": _stop, "to_start": _to_start, "loop": _loop,
    "hear_selected": _hear_selected, "hear_all": _hear_all, "hear_remainder": _hear_remainder,
    "hear_removed": _hear_removed, "ab": _ab,
    "gain": _gain, "mute": _mute, "delete": _delete, "extract": _extract, "copy_layer": _copy_layer,
    "preview": _preview, "bypass": _bypass, "apply": _apply, "cancel": _cancel,
    "zoom_in": _zoom_in, "zoom_out": _zoom_out, "fit_selection": _fit_selection, "fit_project": _fit_project,
    "reset_view": _reset_view, "focus": _focus,
    "layer_new": _layer_new, "layer_dup": _layer_dup, "layer_delete": _layer_delete, "up": _up, "down": _down,
    "merge": _merge,
    "time": _time, "freq": _freq, "rect": _rect, "ellipse": _ellipse, "lasso": _lasso, "polygon": _polygon,
    "sel_brush": _sel_brush, "transform": _transform, "reduce_brush": _reduce_brush,
    "amplify_brush": _amplify_brush, "transfer_brush": _transfer_brush, "hand": _hand, "zoom": _zoom,
    "mode_replace": _mode_replace, "mode_add": _mode_add, "mode_subtract": _mode_subtract,
    "mode_intersect": _mode_intersect, "invert": _invert,
    "lock": _lock, "solo": _solo, "mute_layer": _mute_layer, "polarity": _polarity, "eye": _eye,
    "commands": _commands, "help": _help,
}

ICON_NAMES: tuple[str, ...] = tuple(_GLYPHS)


def render_pixmap(name: str, px: int) -> QPixmap | None:
    """Render one glyph to a transparent ``px`` x ``px`` pixmap (device pixel ratio 1)."""
    fn = _GLYPHS.get(name)
    if fn is None or QGuiApplication.instance() is None:
        return None
    pm = QPixmap(px, px)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    try:
        p.setRenderHint(QPainter.Antialiasing, True)
        p.scale(px / GRID, px / GRID)
        fn(_Pen(p, px))
    finally:
        p.end()
    return pm


def icon(name: str) -> QIcon | None:
    """Cached QIcon for ``name``; ``None`` if the name is unknown (callers fall back to text)."""
    if name in _cache:
        return _cache[name]
    if name not in _GLYPHS or QGuiApplication.instance() is None:
        return None
    ic = QIcon()
    for px in PIXEL_SIZES:
        pm = render_pixmap(name, px)
        if pm is not None:
            ic.addPixmap(pm, QIcon.Normal, QIcon.Off)
    _cache[name] = ic
    return ic


def icon_provider(name: str) -> QIcon | None:
    """Signature-compatible with ``ActionRegistry.build(icon_provider=...)``."""
    return icon(name)


# ------------------------------------------------------------------ application icon
def app_icon_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "resources" / "icons" / "app"


def app_icon() -> QIcon:
    """Application icon from ``resources/icons/app`` (PNG set, else the master SVG).

    Returns an empty QIcon (``isNull()``) when no files are present yet."""
    global _app_icon
    if _app_icon is not None and not _app_icon.isNull():
        return _app_icon
    ic = QIcon()
    d = app_icon_dir()
    try:
        pngs = sorted(d.glob("png/icon_*.png"), key=lambda p: _size_of(p) or 0)
    except OSError:
        pngs = []
    for p in pngs:
        n = _size_of(p)
        if n:
            ic.addFile(str(p), QSize(n, n))
    if ic.isNull():
        for svg in ("spledio-icon.svg", "spledio-icon-small.svg"):
            path = d / svg
            if path.is_file():
                cand = QIcon(str(path))
                if not cand.isNull():
                    ic = cand
                    break
    _app_icon = ic
    return ic


def _size_of(p: Path) -> int | None:
    try:
        return int(p.stem.split("_", 1)[1])
    except (IndexError, ValueError):
        return None


__all__ = ["icon", "icon_provider", "app_icon", "render_pixmap", "ICON_NAMES", "FG", "ACCENT"]
