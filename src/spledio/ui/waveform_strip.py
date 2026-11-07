"""Waveform strip: min/max envelope of the *visible* time range of the mix.

Synced to the canvas viewport (``view.t0``/``view.t1``) and drawn in the same
layout column as the canvas, so column ``x`` covers the same time span as
spectrogram column ``x``. All channels are combined (min of mins / max of
maxes). The envelope is cached by ``(project.mix_version, mix source, t0, t1,
width)``.

Level of detail
---------------
* ``< 2`` samples per pixel: the actual samples of every channel are drawn as
  polylines.
* up to ``2 * MixEnvelope.BLOCK`` samples per pixel: exact per-column min/max of
  the raw samples.
* coarser: per-column min/max of the background-computed block envelope
  (:class:`~spledio.ui.overview.MixEnvelope`); column edges are rounded down
  to block boundaries (a shift of less than half a column). Every block belongs
  to exactly one column, so no peak is lost. While that envelope is still
  computing, a strided sample (cheap decimation, may miss peaks; drawn dimmer
  and labelled "approximate") is shown for very long spans.

Clipping (``|x| > 1``) is marked in red at the top/bottom edge. Click or drag
seeks the playhead. Display only.
"""

from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QSizePolicy, QWidget

from spledio.ui.overview import MixEnvelope, envelope_path, reduce_columns
from spledio.ui.rulers import Palette, ruler_font

__all__ = ["WaveformStrip", "visible_envelope"]

_RAW_LIMIT = 16_000_000  # samples*channels scanned synchronously before falling back to decimation


def visible_envelope(data: np.ndarray, sample_rate: float, t0: float, t1: float, width: int, blocks=None,
                     block: int = MixEnvelope.BLOCK):
    """Envelope of ``data`` (``(frames, channels)``) for ``[t0, t1)`` on ``width`` columns.

    Returns ``("samples", xs, values)`` (``values`` ``(n, channels)``, ``xs`` pixel
    positions) when zoomed in below 2 samples per pixel, else
    ``("envelope", mins, maxs, exact)`` with NaN for columns outside the audio.
    """
    n = data.shape[0]
    W = max(1, int(width))
    a_f, b_f = t0 * sample_rate, t1 * sample_rate
    spp = (b_f - a_f) / W
    if spp < 2.0:
        i0 = max(0, int(math.floor(a_f)) - 1)
        i1 = min(n, int(math.ceil(b_f)) + 2)
        if i1 <= i0:
            return ("samples", np.zeros(0), np.zeros((0, data.shape[1])))
        idx = np.arange(i0, i1)
        xs = (idx - a_f) / spp
        return ("samples", xs, np.asarray(data[i0:i1], dtype=np.float32))
    edges = np.floor(a_f + np.arange(W + 1) * spp).astype(np.int64)
    edges = np.clip(edges, 0, n)
    starts, ends = edges[:-1], edges[1:]
    a, b = int(edges[0]), int(edges[-1])
    if b <= a:
        nan = np.full(W, np.nan, dtype=np.float32)
        return ("envelope", nan, nan.copy(), True)
    if spp >= 2 * block and blocks is not None:
        mins, maxs = blocks
        bs = starts // block
        be_last = -(-int(ends[-1]) // block)
        # contiguous columns: each column ends where the next one starts (every block used once)
        be_c = np.concatenate([bs[1:], [be_last]])
        be_c = np.maximum(be_c, bs + 1)
        lo, hi = reduce_columns(mins, maxs, bs, be_c)
        lo[ends <= starts] = np.nan
        hi[ends <= starts] = np.nan
        return ("envelope", lo, hi, True)
    ch = data.shape[1]
    if (b - a) * ch <= _RAW_LIMIT:
        seg = np.asarray(data[a:b])
        lo_s = seg.min(axis=1)
        hi_s = seg.max(axis=1)
        lo, hi = reduce_columns(lo_s, hi_s, starts - a, ends - a)
        return ("envelope", lo, hi, True)
    # very long span and no block envelope yet: cheap strided decimation (approximate)
    step = int(math.ceil((b - a) / (W * 64)))
    sub = np.asarray(data[a:b:step])
    lo_s, hi_s = sub.min(axis=1), sub.max(axis=1)
    lo, hi = reduce_columns(lo_s, hi_s, (starts - a) // step, -(-(ends - a) // step))
    return ("envelope", lo, hi, False)


class WaveformStrip(QWidget):
    """Min/max envelope of the visible range of the mix, with playhead and selection span."""

    HEIGHT = 52

    def __init__(self, controller, envelope: MixEnvelope | None = None, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.envelope = envelope or MixEnvelope(controller, self)
        self.setFixedHeight(self.HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setToolTip("Waveform of the visible range (mix) – click or drag to move the playhead")
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self._cache_key = None
        self._cache = None
        self._path_key = None
        self._path = None
        self._seeking = False
        self._last_ph_x: float | None = None
        self.compute_count = 0  # for tests / diagnostics
        self.envelope.ready.connect(self._on_envelope)
        c = controller
        # bound-method slots (not lambdas): PySide drops them with the widget, so the
        # controller never keeps a closed editor alive
        c.view.changed.connect(self._on_changed)
        c.sessionReplaced.connect(self.update)
        c.audioChanged.connect(self._on_changed)
        c.selectionChanged.connect(self.update)
        c.playheadChanged.connect(self._on_playhead)

    # ------------------------------------------------------------------ data
    def _on_changed(self, *_args) -> None:
        self.update()

    def _on_envelope(self) -> None:
        self._cache_key = None
        self.update()

    def envelope_data(self):
        """Cached envelope for the current view (see :func:`visible_envelope`)."""
        s = self.controller.session
        src = self.envelope.source()
        if s is None or src is None:
            return None
        v = self.controller.view
        W = max(1, self.width())
        blocks = self.envelope.blocks()
        key = (id(s), s.project.mix_version, id(src), self.envelope.serial, v.t0, v.t1, W, blocks is not None)
        if key != self._cache_key:
            self._cache = visible_envelope(src.data, s.sample_rate, v.t0, v.t1, W, blocks)
            self._cache_key = key
            self._path_key = None
            self.compute_count += 1
        return self._cache

    def _x_of_t(self, t: float) -> float:
        v = self.controller.view
        return (t - v.t0) / max(v.t1 - v.t0, 1e-12) * self.width()

    def _on_playhead(self, pos: int) -> None:
        s = self.controller.session
        if s is None:
            return
        x = self._x_of_t(pos / s.sample_rate)
        for xx in (self._last_ph_x, x):
            if xx is not None and -4 <= xx <= self.width() + 4:
                self.update(int(xx) - 2, 0, 5, self.height())
        self._last_ph_x = x

    # ------------------------------------------------------------------ paint
    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        W, H = self.width(), self.height()
        p.fillRect(self.rect(), Palette.BG)
        s = self.controller.session
        if s is None or W < 2:
            return
        mid = H / 2.0
        half = max(1.0, H / 2.0 - 1.5)
        p.setPen(QPen(QColor(52, 56, 66), 1.0))
        p.drawLine(QPointF(0, mid), QPointF(W, mid))
        sel = s.selection
        if sel is not None:
            b = sel.bounds(s.project.duration, s.project.nyquist)
            if b is not None:
                xa, xb = self._x_of_t(b[0]), self._x_of_t(b[1])
                if xb > 0 and xa < W:
                    c = QColor(Palette.SELECTION)
                    c.setAlpha(40)
                    p.fillRect(QRectF(xa, 0, max(1.0, xb - xa), H), c)
        env = self.envelope_data()
        if env is not None:
            if env[0] == "samples":
                _kind, xs, vals = env
                p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                p.setPen(QPen(Palette.WAVE_EDGE, 1.2))
                for c in range(vals.shape[1] if vals.ndim == 2 else 0):
                    ys = mid - np.clip(vals[:, c], -1.0, 1.0) * half
                    poly = QPolygonF([QPointF(x, y) for x, y in zip(xs.tolist(), ys.tolist())])
                    p.drawPolyline(poly)
                if len(xs) and (len(xs) < W / 4):
                    p.setBrush(Palette.WAVE_EDGE)
                    for c in range(vals.shape[1]):
                        ys = mid - np.clip(vals[:, c], -1.0, 1.0) * half
                        for x, y in zip(xs.tolist(), ys.tolist()):
                            p.drawEllipse(QPointF(x, y), 1.6, 1.6)
                    p.setBrush(Qt.BrushStyle.NoBrush)
            else:
                _kind, lo, hi, exact = env
                pk = (self._cache_key, H)
                if pk != self._path_key:
                    self._path = envelope_path(lo, hi, H)
                    self._path_key = pk
                c = QColor(Palette.WAVE)
                c.setAlpha(210 if exact else 140)
                p.fillPath(self._path, c)
                clip_hi = np.flatnonzero(np.nan_to_num(hi, nan=0.0) > 1.0)
                clip_lo = np.flatnonzero(np.nan_to_num(lo, nan=0.0) < -1.0)
                if len(clip_hi) or len(clip_lo):
                    for x in clip_hi.tolist():
                        p.fillRect(QRectF(x, 0, 1, 2), Palette.CLIP)
                    for x in clip_lo.tolist():
                        p.fillRect(QRectF(x, H - 2, 1, 2), Palette.CLIP)
                if not exact:
                    p.setPen(Palette.TEXT_DIM)
                    p.setFont(ruler_font(self, 9))
                    p.drawText(QPointF(4, 11), "approximate (computing envelope…)")
        x = self._x_of_t(self.controller.playback.position / s.sample_rate)
        if -2 <= x <= W + 2:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            p.setPen(QPen(QColor(0, 0, 0, 160), 3.0))
            p.drawLine(QPointF(x, 0), QPointF(x, H))
            p.setPen(QPen(Palette.PLAYHEAD, 1.0))
            p.drawLine(QPointF(x, 0), QPointF(x, H))
        self._last_ph_x = x
        p.setPen(QPen(Palette.PANEL_EDGE, 1.0))
        p.drawLine(0, H - 1, W, H - 1)

    # ------------------------------------------------------------------ input
    def _seek_to(self, x: float) -> None:
        s = self.controller.session
        if s is None:
            return
        v = self.controller.view
        t = v.t0 + min(max(x, 0.0), float(self.width())) / max(self.width(), 1) * (v.t1 - v.t0)
        self.controller.seek_seconds(min(max(t, 0.0), s.project.duration))

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton and self.controller.session is not None:
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
