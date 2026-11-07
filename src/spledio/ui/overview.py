"""Whole-file overview bar and the shared mix envelope cache.

:class:`MixEnvelope` keeps a block min/max envelope (``BLOCK`` samples per
block, all channels combined: min of mins / max of maxes) of the controller's
pre-rendered playback mix (``controller.mix_source().data``, an immutable
``(frames, channels)`` float32 buffer). It is recomputed **on a background
thread** whenever ``project.mix_version`` or the mix source object changes and
delivered to the GUI thread through a queued Qt signal, so the GUI never scans
a long file. Both the overview and the waveform strip read it.

:class:`OverviewBar` draws the envelope of the whole file, the current viewport
as a rectangle, the selection's time extent and the playhead. Click centres
the view at that time; dragging moves the view (keeping the grab offset when
the press was inside the viewport rectangle). Hiding it changes nothing but
the display.
"""

from __future__ import annotations

import threading
import weakref

import numpy as np
from PySide6.QtCore import QObject, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from spledio.ui.rulers import Palette

__all__ = ["MixEnvelope", "OverviewBar", "block_envelope", "reduce_columns", "envelope_path"]


def block_envelope(data: np.ndarray, block: int) -> tuple[np.ndarray, np.ndarray]:
    """Per-block (min, max) over all channels of a ``(frames, channels)`` buffer."""
    data = np.asarray(data)
    if data.ndim == 1:
        data = data[:, None]
    n, ch = data.shape
    if n == 0:
        z = np.zeros(0, dtype=np.float32)
        return z, z.copy()
    nb = -(-n // block)
    full = n // block
    mins = np.empty(nb, dtype=np.float32)
    maxs = np.empty(nb, dtype=np.float32)
    step = max(1, (1 << 22) // (block * ch))  # bounded temporaries
    for i in range(0, full, step):
        j = min(full, i + step)
        v = np.ascontiguousarray(data[i * block : j * block]).reshape(j - i, block * ch)
        mins[i:j] = v.min(axis=1)
        maxs[i:j] = v.max(axis=1)
    if nb > full:
        tail = data[full * block :]
        mins[-1] = tail.min()
        maxs[-1] = tail.max()
    return mins, maxs


def reduce_columns(lo: np.ndarray, hi: np.ndarray, starts: np.ndarray, ends: np.ndarray):
    """``min(lo[s:e])``, ``max(hi[s:e])`` per column (columns contiguous, ``ends[i] == starts[i+1]``).

    Columns with ``e <= s`` or ``s >= len(lo)`` get NaN.
    """
    n = len(lo)
    starts = np.asarray(starts, dtype=np.int64)
    ends = np.minimum(np.asarray(ends, dtype=np.int64), n)
    out_lo = np.full(len(starts), np.nan, dtype=np.float32)
    out_hi = np.full(len(starts), np.nan, dtype=np.float32)
    ok = (ends > starts) & (starts >= 0) & (starts < n)
    if not ok.any():
        return out_lo, out_hi
    s = starts[ok]
    e_last = int(ends[ok][-1])
    out_lo[ok] = np.minimum.reduceat(lo[:e_last], s)
    out_hi[ok] = np.maximum.reduceat(hi[:e_last], s)
    return out_lo, out_hi


def envelope_path(mins: np.ndarray, maxs: np.ndarray, height: float, gain: float = 1.0) -> QPainterPath:
    """Filled min/max envelope (one closed sub-path per run of valid columns)."""
    path = QPainterPath()
    H = float(height)
    mid = H / 2.0
    half = max(1.0, H / 2.0 - 1.5)
    valid = ~(np.isnan(mins) | np.isnan(maxs))
    if not valid.any():
        return path
    top = mid - np.clip(maxs * gain, -1.0, 1.0) * half
    bot = mid - np.clip(mins * gain, -1.0, 1.0) * half
    # always at least one pixel tall so silence still draws a centre line
    bot = np.maximum(bot, top + 1.0)
    idx = np.flatnonzero(valid)
    breaks = np.flatnonzero(np.diff(idx) > 1)
    runs = np.split(idx, breaks + 1)
    for run in runs:
        x = run.astype(np.float64)
        tt, bb = top[run].tolist(), bot[run].tolist()
        xs = x.tolist()
        path.moveTo(xs[0], tt[0])
        for xi, yi in zip(xs, tt):
            path.lineTo(xi + 0.5, yi)
        path.lineTo(xs[-1] + 1.0, tt[-1])
        path.lineTo(xs[-1] + 1.0, bb[-1])
        for xi, yi in zip(reversed(xs), reversed(bb)):
            path.lineTo(xi + 0.5, yi)
        path.lineTo(xs[0], bb[0])
        path.closeSubpath()
    return path


class _ResultBridge(QObject):
    """Process-wide GUI-thread relay for envelope results.

    Worker threads only ever touch this long-lived object and a ``weakref`` to
    the requesting :class:`MixEnvelope`, so no widget-owned Qt object can be
    released (and destroyed) on a worker thread.
    """

    result = Signal(object)


_BRIDGE: _ResultBridge | None = None


def _bridge() -> _ResultBridge:
    global _BRIDGE
    if _BRIDGE is None:  # first use happens on the GUI thread (MixEnvelope.__init__)
        _BRIDGE = _ResultBridge()
        _BRIDGE.result.connect(_deliver)
    return _BRIDGE


def _deliver(payload) -> None:
    ref, key, mins, maxs = payload
    env = ref()
    if env is None:
        return
    try:
        env._on_result((key, mins, maxs))
    except RuntimeError:  # C++ side already deleted
        pass


class MixEnvelope(QObject):
    """Background-computed block envelope of the playback mix (see module docstring)."""

    ready = Signal()

    BLOCK = 256

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self._key = None
        self._mins: np.ndarray | None = None
        self._maxs: np.ndarray | None = None
        self._pending = None
        self.serial = 0  # bumped on every new envelope (cache key for consumers)
        _bridge()
        controller.sessionReplaced.connect(self.refresh)
        controller.structureChanged.connect(self.refresh)
        controller.propsChanged.connect(self.refresh)
        controller.audioChanged.connect(self._on_audio_changed)
        self.refresh()

    def _on_audio_changed(self, _change=None) -> None:
        self.refresh()

    # ----------------------------------------------------------------- access
    def source(self):
        if self.controller.session is None:
            return None
        return self.controller.mix_source()

    def current_key(self):
        s = self.controller.session
        src = self.source()
        if s is None or src is None:
            return None
        return (id(s), s.project.mix_version, id(src), src.buffer_frames)

    def blocks(self):
        """``(mins, maxs)`` for the current mix, or ``None`` while computing."""
        if self._key is not None and self._key == self.current_key():
            return self._mins, self._maxs
        return None

    def stale_blocks(self):
        """The most recent envelope even if a newer one is being computed (for display)."""
        if self._mins is None:
            return None
        return self._mins, self._maxs

    # ---------------------------------------------------------------- compute
    def refresh(self) -> None:
        key = self.current_key()
        if key is None:
            self._key, self._mins, self._maxs, self._pending = None, None, None, None
            self.serial += 1
            self.ready.emit()
            return
        if key == self._key or key == self._pending:
            return
        self._pending = key
        data = self.source().data  # immutable read-only buffer
        block = self.BLOCK
        sig = _bridge().result
        ref = weakref.ref(self)

        def work():
            try:
                mins, maxs = block_envelope(data, block)
            except Exception:  # pragma: no cover - never fabricate; just skip this envelope
                mins = maxs = None
            sig.emit((ref, key, mins, maxs))

        threading.Thread(target=work, name="spledio-envelope", daemon=True).start()

    def _on_result(self, res) -> None:
        key, mins, maxs = res
        if key == self._pending:
            self._pending = None
        if key != self.current_key():
            self.refresh()
            return
        if mins is None:
            return
        self._key, self._mins, self._maxs = key, mins, maxs
        self.serial += 1
        self.ready.emit()

    def wait_ready(self, timeout: float = 5.0) -> bool:
        """Test helper: process Qt events until the envelope for the current mix is ready."""
        import time

        from PySide6.QtCore import QCoreApplication

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.current_key() is None or self.blocks() is not None:
                return True
            QCoreApplication.processEvents()
            time.sleep(0.005)
        return False


class OverviewBar(QWidget):
    """Whole-file envelope with the viewport rectangle. Click centres, drag moves the view."""

    HEIGHT = 34

    def __init__(self, controller, envelope: MixEnvelope | None = None, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.envelope = envelope or MixEnvelope(controller, self)
        self.setFixedHeight(self.HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setToolTip("Overview – click to centre the view there, drag to move it")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self._drag_offset: float | None = None
        self._press_x = 0.0
        self._press_inside = False
        self._moved = False
        self._cache_key = None
        self._cache_path: QPainterPath | None = None
        self._last_ph_x: float | None = None
        self.envelope.ready.connect(self.update)
        c = controller
        c.view.changed.connect(self._on_changed)
        c.sessionReplaced.connect(self.update)
        c.selectionChanged.connect(self.update)
        c.playheadChanged.connect(self._on_playhead)

    def _on_changed(self, *_args) -> None:
        self.update()

    def _duration(self) -> float:
        s = self.controller.session
        return s.project.duration if s is not None else 1.0

    def _x_of_t(self, t: float) -> float:
        return t / max(self._duration(), 1e-12) * self.width()

    def _t_of_x(self, x: float) -> float:
        return x / max(self.width(), 1) * self._duration()

    def _on_playhead(self, pos: int) -> None:
        s = self.controller.session
        if s is None:
            return
        x = self._x_of_t(pos / s.sample_rate)
        for xx in (self._last_ph_x, x):
            if xx is not None:
                self.update(int(xx) - 2, 0, 5, self.height())
        self._last_ph_x = x

    def _envelope_path(self) -> QPainterPath | None:
        blk = self.envelope.stale_blocks()
        if blk is None:
            return None
        W, H = self.width(), self.height() - 2
        key = (self.envelope.serial, W, H)
        if key == self._cache_key:
            return self._cache_path
        mins, maxs = blk
        nb = len(mins)
        n_frames = self.controller.session.project.n_frames if self.controller.session else nb
        # column c covers samples [c*n/W, (c+1)*n/W) -> blocks
        edges = np.floor(np.arange(W + 1) * (n_frames / max(W, 1)) / MixEnvelope.BLOCK).astype(np.int64)
        starts, ends = edges[:-1], np.maximum(edges[1:], edges[:-1] + 1)
        lo, hi = reduce_columns(mins, maxs, starts, ends)
        path = envelope_path(lo, hi, H)
        path.translate(0, 1)
        self._cache_key, self._cache_path = key, path
        return path

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        W, H = self.width(), self.height()
        p.fillRect(self.rect(), Palette.PANEL)
        s = self.controller.session
        if s is None or W < 2:
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        p.setPen(QPen(QColor(60, 64, 74), 1.0))
        p.drawLine(0, H // 2, W, H // 2)
        path = self._envelope_path()
        if path is not None:
            c = QColor(Palette.WAVE)
            c.setAlpha(170)
            p.fillPath(path, c)
        else:
            p.setPen(Palette.TEXT_DIM)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "computing overview…")
        sel = s.selection
        if sel is not None:
            b = sel.bounds(s.project.duration, s.project.nyquist)
            if b is not None:
                xa, xb = self._x_of_t(b[0]), self._x_of_t(b[1])
                c = QColor(Palette.SELECTION)
                c.setAlpha(60)
                p.fillRect(QRectF(xa, 0, max(1.0, xb - xa), H), c)
        v = self.controller.view
        xa, xb = self._x_of_t(v.t0), self._x_of_t(v.t1)
        rect = QRectF(xa, 0.5, max(3.0, xb - xa), H - 1.0)
        shade = QColor(0, 0, 0, 110)
        p.fillRect(QRectF(0, 0, max(0.0, xa), H), shade)
        p.fillRect(QRectF(xb, 0, max(0.0, W - xb), H), shade)
        p.setPen(QPen(QColor(0, 0, 0, 220), 3.0))
        p.drawRect(rect)
        p.setPen(QPen(Palette.VIEWPORT, 1.0))
        p.drawRect(rect)
        x = self._x_of_t(self.controller.playback.position / s.sample_rate)
        p.setPen(QPen(Palette.PLAYHEAD_MARK, 1.0))
        p.drawLine(QPointF(x, 0), QPointF(x, H))
        self._last_ph_x = x
        p.setPen(QPen(Palette.PANEL_EDGE, 1.0))
        p.drawLine(0, H - 1, W, H - 1)

    # ------------------------------------------------------------------ input
    def _move_view(self, x: float) -> None:
        v = self.controller.view
        span = v.t1 - v.t0
        t = self._t_of_x(x) - (self._drag_offset if self._drag_offset is not None else span / 2)
        v.set_time_range(t, t + span)

    def mousePressEvent(self, e) -> None:
        if self.controller.session is None or e.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(e)
        v = self.controller.view
        t = self._t_of_x(e.position().x())
        span = v.t1 - v.t0
        self._press_x = e.position().x()
        self._press_inside = v.t0 <= t <= v.t1
        self._moved = False
        # inside the viewport: grab it (keep the offset); outside: centre there now
        self._drag_offset = (t - v.t0) if self._press_inside else span / 2
        if not self._press_inside:
            self._move_view(e.position().x())
        e.accept()

    def mouseMoveEvent(self, e) -> None:
        if self._drag_offset is not None:
            if abs(e.position().x() - self._press_x) >= 3:
                self._moved = True
            if self._moved or not self._press_inside:
                self._move_view(e.position().x())
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e) -> None:
        if self._drag_offset is not None and e.button() == Qt.MouseButton.LeftButton:
            if self._press_inside and not self._moved:  # plain click inside: centre there too
                self._drag_offset = None
                self._move_view(e.position().x())
            self._drag_offset = None
            e.accept()
            return
        super().mouseReleaseEvent(e)
