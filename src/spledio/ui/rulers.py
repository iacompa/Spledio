"""Time and frequency rulers for the spectrogram editor (GUI thread only).

Both rulers use the *canvas* geometry (:meth:`SpectrogramCanvas.view_geometry`)
so their ticks line up exactly with the spectrogram pixels: the time ruler sits
in the same layout column as the canvas (same width), the frequency ruler in the
same row (same height) and uses the same :class:`FrequencyScale`.

* :class:`TimeRuler` – round time ticks, the selection's time extent and the
  playhead marker. Click or drag on it to move the playhead (click-to-seek).
  Wheel pans time, Ctrl/Cmd+wheel zooms time at the cursor.
* :class:`FrequencyRuler` – round Hz ticks (linear or log, matching the
  canvas), optional octave (C) note labels when ``view.show_note_labels``.
  Wheel zooms frequency anchored at the cursor frequency.

Display only: nothing here can change audio.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QSizePolicy, QWidget

from spledio.rendering.scales import FrequencyScale, nice_freq_ticks, nice_time_ticks, note_ticks

__all__ = ["Palette", "TimeRuler", "FrequencyRuler", "ruler_font"]


class Palette:
    """Dark editor palette shared by the canvas, rulers and strips."""

    BG = QColor(16, 17, 21)
    PANEL = QColor(24, 26, 31)
    PANEL_EDGE = QColor(46, 49, 58)
    TICK = QColor(128, 134, 146)
    TICK_MINOR = QColor(74, 78, 88)
    TEXT = QColor(206, 209, 216)
    TEXT_DIM = QColor(138, 143, 154)
    NOTE = QColor(150, 200, 170)
    SELECTION = QColor(80, 200, 255)
    PLAYHEAD = QColor(255, 255, 255)
    PLAYHEAD_MARK = QColor(255, 196, 64)
    WAVE = QColor(92, 160, 220)
    WAVE_EDGE = QColor(150, 205, 250)
    CLIP = QColor(255, 80, 80)
    VIEWPORT = QColor(255, 255, 255)


def ruler_font(widget: QWidget, px: int = 10) -> QFont:
    f = QFont(widget.font())
    f.setPixelSize(px)
    return f


def _minor_values(major: np.ndarray, minor: np.ndarray) -> np.ndarray:
    """Minor tick values that do not coincide with a major tick."""
    if len(minor) == 0:
        return minor
    if len(major) == 0:
        return minor
    tol = 1e-9 * max(1.0, float(np.max(np.abs(major))))
    d = np.min(np.abs(minor[:, None] - major[None, :]), axis=1)
    return minor[d > tol]


def _draw_playhead_marker(p: QPainter, x: float, top: bool, h: float) -> None:
    """Small triangle + line, two-tone so it reads on any background."""
    size = 5.0
    if top:
        tri = QPolygonF([QPointF(x - size, 0), QPointF(x + size, 0), QPointF(x, size + 1)])
    else:
        tri = QPolygonF([QPointF(x - size, h), QPointF(x + size, h), QPointF(x, h - size - 1)])
    p.setPen(QPen(QColor(0, 0, 0, 200), 1.0))
    p.setBrush(Palette.PLAYHEAD_MARK)
    p.drawPolygon(tri)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(Palette.PLAYHEAD_MARK, 1.0))
    p.drawLine(QPointF(x, 0), QPointF(x, h))


class TimeRuler(QWidget):
    """Horizontal time ruler below the canvas. Click/drag = seek the playhead."""

    HEIGHT = 24

    def __init__(self, controller, canvas, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.canvas = canvas
        self.setFixedHeight(self.HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Time ruler – click or drag to move the playhead")
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self._seeking = False
        self._last_ph_x: float | None = None
        c = controller
        c.view.changed.connect(self._on_changed)
        c.sessionReplaced.connect(self.update)
        c.selectionChanged.connect(self.update)
        c.structureChanged.connect(self.update)
        c.playheadChanged.connect(self._on_playhead)
        canvas.geometryChanged.connect(self.update)

    # ------------------------------------------------------------------ helpers
    def _on_changed(self, *_args) -> None:
        self.update()

    def _t0_t1(self):
        v = self.controller.view
        return v.t0, v.t1

    def _x_of_t(self, t: float) -> float:
        t0, t1 = self._t0_t1()
        return (t - t0) / max(t1 - t0, 1e-12) * self.width()

    def _t_of_x(self, x: float) -> float:
        t0, t1 = self._t0_t1()
        return t0 + x / max(self.width(), 1) * (t1 - t0)

    def _playhead_x(self) -> float | None:
        s = self.controller.session
        if s is None:
            return None
        return self._x_of_t(self.controller.playback.position / s.sample_rate)

    def _on_playhead(self, _pos: int) -> None:
        x = self._playhead_x()
        for xx in (self._last_ph_x, x):
            if xx is not None and -10 <= xx <= self.width() + 10:
                self.update(int(xx) - 7, 0, 15, self.height())
        self._last_ph_x = x

    # ------------------------------------------------------------------ paint
    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        W, H = self.width(), self.height()
        p.fillRect(self.rect(), Palette.PANEL)
        p.setPen(QPen(Palette.PANEL_EDGE, 1.0))
        p.drawLine(0, 0, W, 0)
        s = self.controller.session
        if s is None or W < 2:
            return
        t0, t1 = self._t0_t1()
        # selection time extent
        sel = s.selection
        if sel is not None:
            b = sel.bounds(s.project.duration, s.project.nyquist)
            if b is not None:
                xa, xb = self._x_of_t(b[0]), self._x_of_t(b[1])
                if xb > 0 and xa < W:
                    c = QColor(Palette.SELECTION)
                    c.setAlpha(46)
                    p.fillRect(QRectF(xa, 1, max(1.0, xb - xa), H - 1), c)
                    p.setPen(QPen(Palette.SELECTION, 1.0))
                    p.drawLine(QPointF(xa, 1), QPointF(xa, H))
                    p.drawLine(QPointF(xb, 1), QPointF(xb, H))
        max_ticks = max(2, W // 90)
        major = nice_time_ticks(t0, t1, max_ticks)
        minor = nice_time_ticks(t0, t1, max_ticks * 5)
        p.setPen(QPen(Palette.TICK_MINOR, 1.0))
        for v in _minor_values(major.values, minor.values):
            x = round(self._x_of_t(float(v))) + 0.5
            p.drawLine(QPointF(x, 1), QPointF(x, 5))
        font = ruler_font(self, 10)
        p.setFont(font)
        fm = QFontMetricsF(font)
        last_right = -1e9
        for val, label in zip(major.values.tolist(), major.labels):
            x = round(self._x_of_t(val)) + 0.5
            p.setPen(QPen(Palette.TICK, 1.0))
            p.drawLine(QPointF(x, 1), QPointF(x, 9))
            w = fm.horizontalAdvance(label)
            lx = x + 3
            if lx + w > W - 1:
                lx = x - 3 - w
            if lx <= last_right + 4:
                continue
            p.setPen(Palette.TEXT)
            p.drawText(QPointF(lx, H - 6), label)
            last_right = lx + w
        x = self._playhead_x()
        if x is not None and -6 <= x <= W + 6:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            _draw_playhead_marker(p, x, True, H)
        self._last_ph_x = x

    # ------------------------------------------------------------------ input
    def _seek_to(self, x: float) -> None:
        s = self.controller.session
        if s is None:
            return
        t = min(max(self._t_of_x(min(max(x, 0.0), float(self.width()))), 0.0), s.project.duration)
        self.controller.seek_seconds(t)

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._seeking = True
            self._seek_to(e.position().x())
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e) -> None:
        if self._seeking:
            self._seek_to(e.position().x())
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e) -> None:
        if self._seeking and e.button() == Qt.MouseButton.LeftButton:
            self._seeking = False
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def wheelEvent(self, e) -> None:
        v = self.controller.view
        if self.controller.session is None:
            return
        d = e.angleDelta().y() or e.angleDelta().x()
        mods = e.modifiers()
        if mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier):
            v.zoom_time(0.85 ** (d / 120.0), anchor_t=self._t_of_x(e.position().x()))
        else:
            v.pan_time(-d / 120.0 * 0.1 * (v.t1 - v.t0))
        e.accept()


class FrequencyRuler(QWidget):
    """Vertical frequency ruler left of the canvas (same scale as the image)."""

    WIDTH = 62

    def __init__(self, controller, canvas, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.canvas = canvas
        self.setFixedWidth(self.WIDTH)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.setToolTip("Frequency ruler – wheel zooms frequency at the cursor")
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        c = controller
        c.view.changed.connect(self._on_changed)
        c.sessionReplaced.connect(self.update)
        c.selectionChanged.connect(self.update)
        canvas.geometryChanged.connect(self.update)

    def _on_changed(self, *_args) -> None:
        self.update()

    def _scale(self) -> FrequencyScale:
        return self.canvas.freq_scale()

    def _y_of_f(self, scale: FrequencyScale, f) -> float:
        return (1.0 - float(scale.to_unit(f))) * self.height()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        W, H = self.width(), self.height()
        p.fillRect(self.rect(), Palette.PANEL)
        p.setPen(QPen(Palette.PANEL_EDGE, 1.0))
        p.drawLine(W - 1, 0, W - 1, H)
        s = self.controller.session
        if s is None or H < 4:
            return
        scale = self._scale()
        # selection frequency extent
        sel = s.selection
        if sel is not None:
            b = sel.bounds(s.project.duration, s.project.nyquist)
            if b is not None:
                ya, yb = self._y_of_f(scale, b[3]), self._y_of_f(scale, b[2])
                ya, yb = max(-1.0, ya), min(H + 1.0, yb)
                if yb > ya:
                    c = QColor(Palette.SELECTION)
                    c.setAlpha(46)
                    p.fillRect(QRectF(W - 6, ya, 5, yb - ya), c)
        max_ticks = max(2, H // 34)
        major = nice_freq_ticks(scale, max_ticks)
        minor = nice_freq_ticks(scale, max_ticks * 4)
        p.setPen(QPen(Palette.TICK_MINOR, 1.0))
        for v in _minor_values(major.values, minor.values):
            y = round(self._y_of_f(scale, float(v))) + 0.5
            p.drawLine(QPointF(W - 4, y), QPointF(W - 1, y))
        font = ruler_font(self, 10)
        p.setFont(font)
        fm = QFontMetricsF(font)
        asc = fm.ascent()
        last_top = 1e9
        for val, label in sorted(zip(major.values.tolist(), major.labels)):  # bottom (low f) first
            y = round(self._y_of_f(scale, val)) + 0.5
            p.setPen(QPen(Palette.TICK, 1.0))
            p.drawLine(QPointF(W - 7, y), QPointF(W - 1, y))
            w = fm.horizontalAdvance(label)
            ty = min(max(y + asc / 2 - 1, asc + 1), H - 2)
            if ty > last_top - 2:  # overlaps the label below
                continue
            p.setPen(Palette.TEXT)
            p.drawText(QPointF(W - 9 - w, ty), label)
            last_top = ty - asc
        if self.controller.view.show_note_labels:
            notes = note_ticks(scale.axis_lo, scale.f_hi, "C", scale)
            nf = ruler_font(self, 9)
            p.setFont(nf)
            p.setPen(Palette.NOTE)
            nfm = QFontMetricsF(nf)
            for pos, label in zip(notes.positions.tolist(), notes.labels):
                y = (1.0 - pos) * H
                if 6 <= y <= H - 3:
                    p.drawText(QPointF(2, y + nfm.ascent() / 2 - 1), label)
        if scale.floor_at_bottom:
            nf = ruler_font(self, 8)
            p.setFont(nf)
            p.setPen(Palette.TEXT_DIM)
            p.drawText(QPointF(2, H - 2), "DC")

    def wheelEvent(self, e) -> None:
        if self.controller.session is None:
            return
        d = e.angleDelta().y() or e.angleDelta().x()
        scale = self._scale()
        f = float(scale.from_unit(1.0 - e.position().y() / max(self.height(), 1)))
        self.controller.view.zoom_freq(0.85 ** (d / 120.0), anchor_f=f)
        e.accept()
