"""The central spectrogram editor: zoomable display, selection tools, brushes.

Public contract (used by the main window)::

    editor = SpectrogramEditor(controller)
    editor.cursorInfo            # Signal(str): "1.2345 s · 1052.3 Hz (C6 −12¢) · −34.2 dB" or ""
    editor.canvas                # SpectrogramCanvas
    editor.cancel_interaction()  # Esc: cancel a drag / polygon in progress -> True if something was cancelled
    editor.finish_polygon()      # Enter: close the polygon lasso -> True if a polygon was committed

Layout (top to bottom): :class:`~spledio.ui.overview.OverviewBar` (iff
``view.show_overview``), :class:`~spledio.ui.waveform_strip.WaveformStrip`
(iff ``view.show_waveform``), then FrequencyRuler | **SpectrogramCanvas**,
TimeRuler (click/drag = seek) and a horizontal scroll bar for panning time.

Coordinates
-----------
Selections are stored only in audio units (seconds, Hz); screen pixels are
never stored. :class:`ViewGeometry` is the single pixel <-> audio mapping used
by the image, overlay, outlines, rulers and mouse input. Column ``x`` spans
``[x, x+1)`` with its centre at time ``t0 + (x+0.5)·(t1-t0)/W`` (the analyser's
column rule); vertical positions use the same :class:`FrequencyScale` as the
image, ``y = (1 - scale.to_unit(f))·H`` (row 0 = top = highest frequency).
Zoom, resize, DPI and linear/log switches therefore only re-map the stored
geometry.

Rendering
---------
``controller.analyzer.view_data`` (non-blocking; missing tiles are PENDING and
drawn as the hatch from :func:`colorize` – never fabricated) is re-requested
only when the viewport/scale/size changes or the data changed (``tilesReady``
while the last view was incomplete, ``audioChanged``, ``sessionReplaced``,
``structureChanged``, ``propsChanged``, analysis settings). Colour map, dB
range and gamma only re-colour the cached :class:`ViewData`. All triggers are
coalesced through a 0 ms single-shot timer.

Selection overlay (exactly what the DSP processes)
--------------------------------------------------
The overlay is the session's selection sampled on the *processing* grid
``session.edit_config``: each pixel shows the mask value of its nearest
processing cell (``k = round(t·sr/hop)``, ``b = round(f·n_fft/sr)``). Normally
:func:`rasterize` is evaluated on the visible frames (``k_range``) and mapped
with :func:`map_grid_to_view`. **LOD:** when the visible frame span exceeds
3 x the pixel width, the membership is instead evaluated with
:func:`rasterize_on` only at the nearest cell of every column/row – the same
values ``map_grid_to_view`` would pick from the full raster (identical
floating-point node coordinates), without computing the cells no pixel centre
lands on. As with ``map_grid_to_view``, a cell narrower than a pixel can be
skipped by every pixel centre when zoomed far out.

On top, every shape operation is outlined as a two-tone (black + white) dashed
vector line (subtract: dotted, intersect: dash-dot) so selections are readable
without colour; polygon edges and ellipses are drawn as the curves they are in
(seconds, Hz) – in a log view a straight polygon edge in Hz is curved.

Interaction summary
-------------------
Modifiers override ``tools.selection_mode``: Shift = add, Alt/Option =
subtract, Shift+Alt = intersect. Selection commits go through
``controller.commit_selection_shape`` on release; a click (< 4 px) with
time/freq/rect/ellipse/lasso/transform seeks the playhead (selection kept).
Brush tools treat a click as a single dab. Transform translates the selection
mask by ``(dt, df)`` with ``df = f(release) - f(press)`` **in Hz** (in a log
view that is not an octave shift). See :class:`SpectrogramCanvas` for details.
"""

from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetricsF, QImage, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QGridLayout, QScrollBar, QSizePolicy, QWidget

from spledio.rendering.analysis import DB_FLOOR, TileStatus, ViewData
from spledio.rendering.colormaps import get_lut
from spledio.rendering.image import colorize, map_grid_to_view, mask_overlay, nearest_cells
from spledio.rendering.scales import FrequencyScale, note_name
from spledio.selections.geometry import (
    BrushShape,
    EllipseShape,
    PolygonShape,
    RectShape,
    Shape,
    frequency_range,
    time_range,
)
from spledio.selections.selection import (
    ADD,
    INTERSECT,
    INVERT,
    REPLACE,
    SUBTRACT,
    Selection,
    raster_extent,
    rasterize,
    rasterize_on,
)
from spledio.ui.overview import MixEnvelope, OverviewBar
from spledio.ui.rulers import FrequencyRuler, Palette, TimeRuler, ruler_font
from spledio.ui.waveform_strip import WaveformStrip

__all__ = [
    "SpectrogramEditor",
    "SpectrogramCanvas",
    "ViewGeometry",
    "selection_overlay_values",
    "shape_outline",
    "OVERLAY_RGB",
]

OVERLAY_RGB = (80, 200, 255)
CLICK_PX = 4.0  # a press/release closer than this is a click, not a drag
HANDLE_PX = 7.0
HANDLE_HIT_PX = 6.0
DC_SNAP_PX = 3.0
_LIM = 1.0e6  # clamp for screen coordinates handed to QPainter

SHAPE_TOOLS = ("time", "freq", "rect", "ellipse", "lasso")
BRUSH_KINDS = {"sel_brush": None, "reduce_brush": "reduce", "amplify_brush": "amplify", "transfer_brush": "transfer"}
HANDLE_TOOLS = ("time", "freq", "rect", "ellipse", "lasso", "polygon", "transform")
DASHES = {
    REPLACE: [4.0, 4.0],
    ADD: [4.0, 4.0],
    SUBTRACT: [1.5, 3.0],
    INTERSECT: [7.0, 3.0, 1.5, 3.0],
}
BRUSH_FILL = {
    "sel_brush": QColor(80, 200, 255, 70),
    "reduce_brush": QColor(255, 96, 96, 80),
    "amplify_brush": QColor(120, 230, 120, 80),
    "transfer_brush": QColor(200, 140, 255, 80),
}
_MINUS = "−"


def _ret(x):
    a = np.asarray(x)
    return float(a) if a.ndim == 0 else a


def _fmt_signed(v: float, decimals: int) -> str:
    s = f"{v:.{decimals}f}"
    return s.replace("-", _MINUS)


def note_with_cents(f: float) -> str:
    """``'C6 −12¢'`` style note label ('' for f <= 0)."""
    name = note_name(f)
    if not name:
        return ""
    m_exact = 69.0 + 12.0 * math.log2(f / 440.0)
    c = int(round(100.0 * (m_exact - math.floor(m_exact + 0.5))))
    if c == 0:
        return name
    return f"{name} {'+' if c > 0 else _MINUS}{abs(c)}¢"


# ---------------------------------------------------------------------------
# geometry
# ---------------------------------------------------------------------------


class ViewGeometry:
    """Immutable pixel <-> (seconds, Hz) mapping of one view (logical pixels)."""

    __slots__ = ("t0", "t1", "width", "height", "scale")

    def __init__(self, t0: float, t1: float, width: int, height: int, scale: FrequencyScale):
        self.t0 = float(t0)
        self.t1 = float(t1) if t1 > t0 else float(t0) + 1e-6
        self.width = int(max(1, width))
        self.height = int(max(1, height))
        self.scale = scale

    @property
    def key(self):
        return (self.t0, self.t1, self.width, self.height, self.scale)

    @property
    def sec_per_px(self) -> float:
        return (self.t1 - self.t0) / self.width

    def x_of_t(self, t):
        return _ret((np.asarray(t, dtype=np.float64) - self.t0) * (self.width / (self.t1 - self.t0)))

    def t_of_x(self, x):
        return _ret(self.t0 + np.asarray(x, dtype=np.float64) * ((self.t1 - self.t0) / self.width))

    def y_of_f(self, f):
        u = np.asarray(self.scale.to_unit(np.asarray(f, dtype=np.float64)), dtype=np.float64)
        return _ret((1.0 - u) * self.height)

    def f_of_y(self, y):
        u = 1.0 - np.asarray(y, dtype=np.float64) / self.height
        return _ret(np.asarray(self.scale.from_unit(u), dtype=np.float64))

    def hz_per_px(self, f: float) -> float:
        """Local Hz per vertical pixel at frequency ``f`` (derivative of the scale)."""
        sc = self.scale
        if sc.kind == FrequencyScale.LINEAR:
            return (sc.f_hi - sc.f_lo) / self.height
        lspan = math.log10(sc.f_hi) - math.log10(sc.axis_lo)
        return max(float(f), sc.floor_hz) * math.log(10.0) * lspan / self.height

    def column_centres(self) -> np.ndarray:
        return self.t0 + (np.arange(self.width) + 0.5) * self.sec_per_px


def _clip_xy(arr: np.ndarray) -> np.ndarray:
    a = np.nan_to_num(np.asarray(arr, dtype=np.float64), nan=0.0, posinf=_LIM, neginf=-_LIM)
    return np.clip(a, -_LIM, _LIM)


def _qpoly(xy: np.ndarray) -> QPolygonF:
    xy = _clip_xy(xy)
    return QPolygonF([QPointF(x, y) for x, y in xy.tolist()])


def _densify(points: np.ndarray, geom: ViewGeometry, closed: bool, px_step: float = 6.0) -> np.ndarray:
    """Audio-space polyline -> screen points, subdividing edges in log views (straight in Hz = curved on screen)."""
    p = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    if len(p) == 0:
        return np.zeros((0, 2))
    if closed and len(p) > 1:
        p = np.vstack([p, p[:1]])
    if geom.scale.kind == FrequencyScale.LINEAR or len(p) < 2:
        return np.column_stack([geom.x_of_t(p[:, 0]), geom.y_of_f(p[:, 1])])
    a, b = p[:-1], p[1:]
    xa, ya = _clip_xy(geom.x_of_t(a[:, 0])), _clip_xy(geom.y_of_f(a[:, 1]))
    xb, yb = _clip_xy(geom.x_of_t(b[:, 0])), _clip_xy(geom.y_of_f(b[:, 1]))
    seg_len = np.hypot(xb - xa, yb - ya)
    n_sub = np.clip(np.ceil(seg_len / px_step), 1, 64).astype(np.int64)
    rep = np.repeat(np.arange(len(a)), n_sub)
    frac = np.concatenate([np.arange(n) / n for n in n_sub.tolist()])
    pts = a[rep] + (b[rep] - a[rep]) * frac[:, None]
    pts = np.vstack([pts, p[-1:]])
    return np.column_stack([geom.x_of_t(pts[:, 0]), geom.y_of_f(pts[:, 1])])


def shape_outline(shape: Shape, geom: ViewGeometry) -> list[tuple[np.ndarray, bool]]:
    """Screen polylines ``[(xy (N,2), closed)]`` of a rect/ellipse/polygon base geometry.

    Full-band time ranges -> two vertical band edges; whole-duration frequency
    ranges -> two horizontal band edges. (Brushes: see ``brush_outline_path``.)
    """
    W, H = geom.width, geom.height
    if isinstance(shape, RectShape):
        fb, ft = shape.full_band, shape.full_time
        if fb and ft:
            return [(np.array([[0.5, 0.5], [W - 0.5, 0.5], [W - 0.5, H - 0.5], [0.5, H - 0.5]]), True)]
        if fb:
            return [(np.array([[x, -2.0], [x, H + 2.0]]), False) for x in np.atleast_1d(geom.x_of_t([shape.t0, shape.t1]))]
        if ft:
            return [(np.array([[-2.0, y], [W + 2.0, y]]), False) for y in np.atleast_1d(geom.y_of_f([shape.f0, shape.f1]))]
        x0, x1 = geom.x_of_t([shape.t0, shape.t1])
        y_top, y_bot = geom.y_of_f([shape.f1, shape.f0])
        return [(np.array([[x0, y_top], [x1, y_top], [x1, y_bot], [x0, y_bot]]), True)]
    if isinstance(shape, EllipseShape):
        th = np.linspace(0.0, 2.0 * np.pi, 129)[:-1]
        t = shape.tc + shape.rt * np.cos(th)
        f = shape.fc + shape.rf * np.sin(th)
        return [(np.column_stack([geom.x_of_t(t), geom.y_of_f(f)]), True)]
    if isinstance(shape, PolygonShape):
        if len(shape.points) < 2:
            return []
        return [(_densify(np.asarray(shape.points), geom, closed=True), True)]
    return []


def stamp_footprint(ct: float, cf: float, rt: float, rf: float, square: bool, geom: ViewGeometry, n: int = 40) -> np.ndarray:
    """Screen outline of one brush stamp (its support, r <= 1) – exact in log views too."""
    if square:
        t = np.array([ct - rt, ct + rt, ct + rt, ct - rt])
        f = np.array([cf + rf, cf + rf, cf - rf, cf - rf])
    else:
        th = np.linspace(0.0, 2.0 * np.pi, n + 1)[:-1]
        t = ct + rt * np.cos(th)
        f = cf + rf * np.sin(th)
    return np.column_stack([geom.x_of_t(t), geom.y_of_f(np.maximum(f, 0.0))])


def _add_stamps(path: QPainterPath, stamps: np.ndarray, rt: float, rf: float, square: bool, geom: ViewGeometry) -> None:
    if len(stamps) == 0:
        return
    if square or geom.scale.kind == FrequencyScale.LINEAR:
        xs = geom.x_of_t(stamps[:, 0])
        if square:
            ya = geom.y_of_f(stamps[:, 1] + rf)
            yb = geom.y_of_f(np.maximum(stamps[:, 1] - rf, 0.0))
            rx = rt / geom.sec_per_px
            for x, y0, y1 in zip(np.atleast_1d(xs).tolist(), np.atleast_1d(ya).tolist(), np.atleast_1d(yb).tolist()):
                path.addRect(QRectF(x - rx, y0, 2 * rx, max(0.5, y1 - y0)))
        else:
            ys = geom.y_of_f(stamps[:, 1])
            rx = rt / geom.sec_per_px
            ry = rf / geom.hz_per_px(1.0)
            for x, y in zip(np.atleast_1d(xs).tolist(), np.atleast_1d(ys).tolist()):
                path.addEllipse(QPointF(x, y), rx, ry)
        return
    for ct, cf in stamps.tolist():
        path.addPolygon(_qpoly(stamp_footprint(ct, cf, rt, rf, False, geom, 20)))
        path.closeSubpath()


def _subsample_stamps(st: np.ndarray, max_n: int) -> np.ndarray:
    if len(st) <= max_n:
        return st
    idx = np.unique(np.round(np.linspace(0, len(st) - 1, max_n)).astype(np.int64))
    return st[idx]


def brush_outline_path(shape: BrushShape, geom: ViewGeometry, max_stamps: int = 700) -> QPainterPath:
    """Union outline of a brush stroke's stamp envelope (support boundary)."""
    st = _subsample_stamps(shape.stamps(spacing=0.6), max_stamps)
    path = QPainterPath()
    path.setFillRule(Qt.FillRule.WindingFill)
    _add_stamps(path, st, shape.rt, shape.rf, shape.square, geom)
    return path.simplified()


def stamps_between(a, b, rt: float, rf: float, spacing: float) -> np.ndarray:
    """Interpolated stamp centres strictly after ``a`` up to and including ``b`` (BrushShape rule)."""
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    dn = math.hypot((b[0] - a[0]) / rt, (b[1] - a[1]) / rf)
    steps = max(1, int(math.ceil(dn / spacing)))
    s = np.arange(1, steps + 1) / steps
    return a[None, :] + (b - a)[None, :] * s[:, None]


def selection_overlay_values(sel: Selection, cfg, sample_rate: float, n_samples: int, geom: ViewGeometry):
    """Mask value of the nearest processing cell under every pixel: ``float32 (H, W)``, plus the method used.

    Returns ``(values, method)`` with ``method`` in ``{'empty', 'raster', 'lod'}``
    (see the module docstring for the LOD rule).
    """
    W, H = geom.width, geom.height
    out = np.zeros((H, W), dtype=np.float32)
    sr = float(sample_rate)
    ext = raster_extent(sel, cfg, sr, n_samples)
    if ext is None:
        return out, "empty"
    k0, k1, b0, b1 = ext
    k_cols, b_rows = nearest_cells(geom.t0, geom.t1, W, geom.scale, H, sr, cfg)
    cols = np.flatnonzero((k_cols >= k0) & (k_cols < k1))
    rows = np.flatnonzero((b_rows >= b0) & (b_rows < b1))
    if len(cols) == 0 or len(rows) == 0:
        return out, "empty"
    kv0, kv1 = int(k_cols[cols].min()), int(k_cols[cols].max()) + 1
    if kv1 - kv0 <= 3 * W:
        r = rasterize(sel, cfg, sr, n_samples, k_range=(kv0, kv1))
        if r is None:
            return out, "empty"
        vals = map_grid_to_view(r.data, r.k0, r.b0, geom.t0, geom.t1, W, geom.scale, H, sr, cfg)
        return np.ascontiguousarray(vals, dtype=np.float32), "raster"
    uk, kinv = np.unique(k_cols[cols], return_inverse=True)
    ub, binv = np.unique(b_rows[rows], return_inverse=True)
    vals = rasterize_on(sel, uk * (cfg.hop / sr), ub * (sr / cfg.n_fft))  # (len(uk), len(ub))
    out[np.ix_(rows, cols)] = vals[np.ix_(kinv, binv)].T.astype(np.float32)
    return out, "lod"


def _rdp(points: list[tuple[float, float]], eps: float) -> list[int]:
    """Ramer–Douglas–Peucker simplification; returns kept indices (screen space)."""
    n = len(points)
    if n < 3:
        return list(range(n))
    pts = np.asarray(points, dtype=np.float64)
    keep = np.zeros(n, dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        a, b = pts[i], pts[j]
        seg = pts[i + 1 : j]
        ab = b - a
        L = math.hypot(ab[0], ab[1])
        if L < 1e-12:
            d = np.hypot(seg[:, 0] - a[0], seg[:, 1] - a[1])
        else:
            d = np.abs(ab[0] * (seg[:, 1] - a[1]) - ab[1] * (seg[:, 0] - a[0])) / L
        k = int(np.argmax(d))
        if d[k] > eps:
            m = i + 1 + k
            keep[m] = True
            stack.append((i, m))
            stack.append((m, j))
    return np.flatnonzero(keep).tolist()


# ---------------------------------------------------------------------------
# interaction state
# ---------------------------------------------------------------------------


class _Drag:
    """One mouse gesture in progress (kind: select|handle|transform|brush|pan|zoombox)."""

    def __init__(self, kind: str, tool: str, button, press: QPointF, mods, mode: str):
        self.kind = kind
        self.tool = tool
        self.button = button
        self.press = QPointF(press)
        self.cur = QPointF(press)
        self.mods = mods
        self.mode = mode
        self.moved = False
        self.press_audio: tuple[float, float] = (0.0, 0.0)
        # select (lasso)
        self.screen_pts: list[tuple[float, float]] = []
        self.audio_pts: list[tuple[float, float]] = []
        # handle
        self.handle = ""
        self.rect0: RectShape | None = None
        # transform / pan
        self.press_t = 0.0
        self.press_f = 0.0
        self.view0 = None
        # brush
        self.rt = 0.0
        self.rf = 0.0
        self.path: list[tuple[float, float]] = []
        self.live = QPainterPath()
        self.live_key = None
        self.live_n = 0


class SpectrogramCanvas(QWidget):
    """Spectrogram image + selection overlay/outlines + all pointer tools.

    Tools (``controller.tools.tool``):

    * ``time`` / ``freq`` / ``rect`` / ``ellipse``: drag; live outline; commit on
      release. In a log view a drag ending in the bottom 3 px while the view starts
      at/below the log floor sets ``f0 = 0 Hz`` (the bottom row stands for DC..floor).
    * ``lasso``: freehand; points recorded in (s, Hz), simplified (RDP, 0.75 px)
      and committed as a :class:`PolygonShape` (>= 3 points).
    * ``polygon``: click vertices; double-click, click the first vertex or
      :meth:`finish_polygon` (Enter) closes; Backspace removes the last vertex;
      Esc cancels.
    * Resize handles: a selection that is a single REPLACE :class:`RectShape`
      shows 8 handles (time ranges: 2 side handles, frequency ranges: 2 edge
      handles). Dragging one with any shape tool edits that rect and commits a
      REPLACE keeping feather / channels (``Selection.combine``).
    * ``transform``: drag moves the selection mask only:
      ``session.set_selection(selection.transformed(dt, df))`` with ``df`` in Hz.
    * ``sel_brush``: paints a :class:`BrushShape` selection (Alt = subtract, else
      add if a selection exists, replace if none). ``reduce_brush`` /
      ``amplify_brush`` / ``transfer_brush``: one stroke =
      ``controller.brush_stroke(brush, kind)`` = one undo step. Footprint
      ``'screen'``: ``brush_size_px/2`` px (x scaled by ``brush_aspect``) converted
      to (s, Hz) once at the stroke start (log: local Hz/px at that frequency);
      ``'spectral'``: ``brush_width_s/2`` x ``brush_height_hz/2``. Every mouse move
      adds a path point; :class:`BrushShape` interpolates stamps (max accumulation,
      event-rate independent). Live coverage = filled stamp footprints.
    * ``hand``: drag pans time and frequency (middle button pans with any tool).
    * ``zoom``: click zooms in x0.5 (time and frequency) at the cursor, Alt-click
      zooms out x2, drag a box = ``view.fit_box``.

    Wheel: plain/trackpad = pan time (horizontal delta too); Ctrl/Cmd+wheel = zoom
    time at the cursor time; Shift+wheel = zoom frequency at the cursor frequency
    (macOS may deliver Shift+wheel as horizontal delta – handled).
    """

    cursorInfo = Signal(str)
    geometryChanged = Signal()
    rendered = Signal()

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(400, 250)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setCursor(Qt.CursorShape.CrossCursor)
        # rendering state
        self._vd: ViewData | None = None
        self._vd_key = None
        self._vd_serial = 0
        self._data_dirty = True
        self._rgba: np.ndarray | None = None
        self._qimg: QImage | None = None
        self._pm: QPixmap | None = None
        self._color_key = None
        self._ov_values: np.ndarray | None = None
        self._ov_method = "empty"
        self._ov_rgba: np.ndarray | None = None
        self._ov_qimg: QImage | None = None
        self._ov_pm: QPixmap | None = None
        self._ov_key = None
        self._ov_sel = None
        self._ov_alpha_key = None
        self._outline_cache = None  # (sel, geom key, items)
        self._area_cache: dict = {}  # (selection, geom key) -> (items, fill path)
        self.view_data_calls = 0
        self.render_count = 0
        # interaction state
        self._drag: _Drag | None = None
        self._poly: list[tuple[float, float]] = []
        self._poly_mode = REPLACE
        self._mouse: QPointF | None = None
        self._last_info = None
        self._ph_x: float | None = None
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(0)
        self._render_timer.timeout.connect(self.render_now)

        c = controller
        c.tilesReady.connect(self._on_tiles_ready)
        c.audioChanged.connect(self._invalidate_data)  # bound slots only (see waveform_strip)
        c.sessionReplaced.connect(self._on_session_replaced)
        c.structureChanged.connect(self._invalidate_data)
        c.propsChanged.connect(self._invalidate_data)
        c.selectionChanged.connect(self._on_selection_changed)
        c.previewChanged.connect(self.update)
        c.areasChanged.connect(self._on_areas_changed)
        c.playheadChanged.connect(self._on_playhead)
        c.view.changed.connect(self._on_view_changed)
        c.tools.changed.connect(self._on_tools_changed)
        self._on_tools_changed()
        from spledio.ui.level_bar import LevelBar

        self.level_bar = LevelBar(self)  # − / + / slider next to the selection

    # ================================================================ geometry
    def sizeHint(self) -> QSize:
        return QSize(900, 420)

    def minimumSizeHint(self) -> QSize:
        return QSize(400, 250)

    def freq_scale(self) -> FrequencyScale:
        """The frequency scale shared by image, overlay, outlines, input and the ruler."""
        v = self.controller.view
        s = self.controller.session
        nyq = float(s.project.nyquist) if s is not None else float(v.nyquist)
        try:
            return FrequencyScale(v.freq_scale, v.f_lo, v.f_hi, nyq, v.log_floor_hz)
        except ValueError:
            try:
                return FrequencyScale(FrequencyScale.LINEAR, v.f_lo, v.f_hi, nyq, v.log_floor_hz)
            except ValueError:
                return FrequencyScale(FrequencyScale.LINEAR, 0.0, nyq, nyq, v.log_floor_hz)

    def view_geometry(self) -> ViewGeometry:
        v = self.controller.view
        return ViewGeometry(v.t0, v.t1, self.width(), self.height(), self.freq_scale())

    def audio_to_px(self, t, f):
        g = self.view_geometry()
        return g.x_of_t(t), g.y_of_f(f)

    def px_to_audio(self, x, y):
        g = self.view_geometry()
        return g.t_of_x(x), g.f_of_y(y)

    # ================================================================ public state (tests / main window)
    @property
    def view_data(self) -> ViewData | None:
        return self._vd

    @property
    def image_rgba(self) -> np.ndarray | None:
        return self._rgba

    @property
    def overlay_values(self) -> np.ndarray | None:
        return self._ov_values

    @property
    def overlay_rgba(self) -> np.ndarray | None:
        return self._ov_rgba

    @property
    def overlay_method(self) -> str:
        return self._ov_method

    @property
    def polygon_points(self) -> list[tuple[float, float]]:
        return list(self._poly)

    def interaction_in_progress(self) -> bool:
        return self._drag is not None or bool(self._poly)

    def is_settled(self) -> bool:
        """True when the image matches the current view and has no PENDING pixels."""
        vd = self._vd
        return (
            vd is not None
            and vd.complete
            and not self._data_dirty
            and not self._render_timer.isActive()
            and self._vd_key == self.view_geometry().key
        )

    # ================================================================ triggers
    def schedule_render(self) -> None:
        if not self._render_timer.isActive():
            self._render_timer.start()

    def _invalidate_data(self, *_args) -> None:
        self._data_dirty = True
        self.schedule_render()

    def _on_tiles_ready(self) -> None:
        vd = self._vd
        an = self.controller.analyzer
        if vd is not None and vd.complete and vd.version == an.version and vd.settings == an.settings and not self._data_dirty:
            return  # e.g. a prefetched neighbour tile finished: nothing visible changed
        self._invalidate_data()

    def _on_view_changed(self, what: str) -> None:
        if what == "analysis":
            self._data_dirty = True
        self.schedule_render()  # render_now() repaints, so image and outlines move together

    def _on_selection_changed(self) -> None:
        self._outline_cache = None
        self.schedule_render()

    def _on_areas_changed(self) -> None:
        self.update()
        self.level_bar.sync()

    def _on_session_replaced(self) -> None:
        self._drag = None
        self._poly = []
        self._vd = None
        self._vd_key = None
        self._pm = None
        self._ov_values = None
        self._ov_pm = None
        self._ov_key = None
        self._outline_cache = None
        self._invalidate_data()
        self.update()

    def _on_tools_changed(self) -> None:
        tool = self.controller.tools.tool
        if tool != "polygon" and self._poly:
            self._poly = []
        cursors = {
            "hand": Qt.CursorShape.OpenHandCursor,
            "transform": Qt.CursorShape.SizeAllCursor,
            "zoom": Qt.CursorShape.CrossCursor,
        }
        self.setCursor(cursors.get(tool, Qt.CursorShape.CrossCursor))
        self.update()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._outline_cache = None
        self.schedule_render()
        self.geometryChanged.emit()

    # ================================================================ rendering
    def render_now(self) -> None:
        """Bring image + overlay up to date now (normally called by the coalescing timer)."""
        self._render_timer.stop()
        c = self.controller
        s = c.session
        W, H = self.width(), self.height()
        if s is None or W < 2 or H < 2:
            self._vd = None
            self._pm = None
            self._ov_pm = None
            self.update()
            return
        g = self.view_geometry()
        key = g.key
        if self._data_dirty or key != self._vd_key or self._vd is None:
            self._data_dirty = False
            self.view_data_calls += 1
            vd = c.analyzer.view_data(g.t0, g.t1, g.scale.f_lo, g.scale.f_hi, W, H, g.scale, prefetch_tiles=1,
                                      stale_ok=True)  # edits update in place, no blink
            self._vd, self._vd_key = vd, key
            self._vd_serial += 1
        v = c.view
        ck = (v.colormap, v.min_db, v.max_db, v.gamma, self._vd_serial)
        if ck != self._color_key:
            self._recolor()
            self._color_key = ck
        self._update_overlay(g)
        self.render_count += 1
        self.update()
        self.level_bar.sync()
        self.rendered.emit()

    def _recolor(self) -> None:
        vd = self._vd
        v = self.controller.view
        lo, hi = float(v.min_db), float(v.max_db)
        if not hi > lo:
            hi = lo + 1.0
        gamma = float(v.gamma) if v.gamma and v.gamma > 0 else 1.0
        try:
            lut = get_lut(v.colormap)
        except KeyError:
            lut = get_lut("ember")
        H, W = vd.db.shape
        span = vd.t1 - vd.t0
        hx = int(math.floor(vd.t0 / span * W)) % 8 if span > 0 and math.isfinite(vd.t0 / span * W) else 0
        rgba = np.ascontiguousarray(colorize(vd.db, vd.status, lo, hi, gamma, lut, hatch_origin=(hx, 0)))
        self._rgba = rgba  # keeps the buffer alive while the QImage exists
        self._qimg = QImage(rgba.data, W, H, 4 * W, QImage.Format.Format_RGBA8888)
        self._pm = QPixmap.fromImage(self._qimg)

    def _update_overlay(self, g: ViewGeometry) -> None:
        s = self.controller.session
        v = self.controller.view
        sel = s.selection
        if sel is None:
            self._ov_values, self._ov_rgba, self._ov_pm, self._ov_key, self._ov_sel = None, None, None, None, None
            self._ov_method = "empty"
            return
        if not v.show_mask_overlay:
            return  # nothing drawn; recomputed when the overlay is shown again
        ok = (g.key, s.edit_config, s.project.n_frames, s.project.sample_rate)
        if self._ov_values is None or ok != self._ov_key or sel is not self._ov_sel:
            vals, method = selection_overlay_values(sel, s.edit_config, s.project.sample_rate, s.project.n_frames, g)
            self._ov_values, self._ov_method = vals, method
            self._ov_key, self._ov_sel = ok, sel
            self._ov_alpha_key = None
        ak = min(max(float(v.overlay_opacity), 0.0), 1.0)
        if ak != self._ov_alpha_key:
            rgba = np.ascontiguousarray(mask_overlay(self._ov_values, OVERLAY_RGB, opacity=ak))
            H, W = rgba.shape[:2]
            self._ov_rgba = rgba
            self._ov_qimg = QImage(rgba.data, W, H, 4 * W, QImage.Format.Format_RGBA8888)
            self._ov_pm = QPixmap.fromImage(self._ov_qimg)
            self._ov_alpha_key = ak

    # ================================================================ painting
    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        W, H = self.width(), self.height()
        p.fillRect(self.rect(), Palette.BG)
        s = self.controller.session
        if s is None:
            p.setPen(Palette.TEXT_DIM)
            p.setFont(ruler_font(self, 13))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No audio loaded – open a file to see its spectrogram")
            return
        if self._pm is not None:
            if self._pm.width() == W and self._pm.height() == H:
                p.drawPixmap(0, 0, self._pm)
            else:  # resize in flight: stretch the last image until the next render
                p.drawPixmap(self.rect(), self._pm)
        v = self.controller.view
        if self._ov_pm is not None and v.show_mask_overlay:
            if self._ov_pm.width() == W and self._ov_pm.height() == H:
                p.drawPixmap(0, 0, self._ov_pm)
        g = self.view_geometry()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self._paint_areas(p, g)
        self._paint_selection(p, g)
        self._paint_interaction(p, g)
        self._paint_playhead(p, g)
        self._paint_crosshair(p, g)
        self._paint_brush_cursor(p, g)
        self._paint_labels(p, g)
        self._paint_hint(p, g)

    # --- outlines --------------------------------------------------------------
    def _outline_items(self, sel: Selection, g: ViewGeometry):
        cache = self._outline_cache
        if cache is not None and cache[0] is sel and cache[1] == g.key:
            return cache[2]
        items = []
        for op in sel.ops:
            if op.shape is None:
                continue
            items.extend(self._shape_items(op.shape, g, op.mode))
        self._outline_cache = (sel, g.key, items)
        return items

    def _shape_items(self, shape: Shape, g: ViewGeometry, mode: str):
        dash = DASHES.get(mode, DASHES[REPLACE])
        if isinstance(shape, BrushShape):
            if not shape.path:
                return []
            return [(brush_outline_path(shape, g), True, dash)]
        return [(_qpoly(xy), closed, dash) for xy, closed in shape_outline(shape, g)]

    @staticmethod
    def _stroke_items(p: QPainter, items, faint: bool = False) -> None:
        dark = QPen(QColor(0, 0, 0, 120 if faint else 230), 2.2)
        dark.setCosmetic(True)
        for obj, closed, dash in items:
            light = QPen(QColor(255, 255, 255, 120 if faint else 240), 1.0)
            light.setCosmetic(True)
            light.setDashPattern(dash)
            p.setBrush(Qt.BrushStyle.NoBrush)
            for pen in (dark, light):
                p.setPen(pen)
                if isinstance(obj, QPainterPath):
                    p.drawPath(obj)
                elif closed:
                    p.drawPolygon(obj)
                else:
                    p.drawPolyline(obj)

    def _paint_selection(self, p: QPainter, g: ViewGeometry) -> None:
        sel = self.controller.session.selection
        if sel is None:
            return
        d = self._drag
        items = self._outline_items(sel, g)
        if d is not None and d.kind == "transform" and d.moved:
            self._stroke_items(p, items, faint=True)
            dt, df = self._transform_delta(d, g)
            moved = sel.transformed(dt, df)
            items2 = []
            for op in moved.ops:
                if op.shape is not None:
                    items2.extend(self._shape_items(op.shape, g, op.mode))
            self._stroke_items(p, items2)
            return
        if d is not None and d.kind == "handle" and d.moved:
            self._stroke_items(p, items, faint=True)
            self._stroke_items(p, self._shape_items(self._edited_rect(d, g), g, REPLACE))
            return
        self._stroke_items(p, items)
        if any(op.mode == INVERT for op in sel.ops):
            self._label(p, "selection inverted", Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom)
        self._paint_handles(p, g)

    # --- adjusted areas -------------------------------------------------------------
    @staticmethod
    def area_colour(gain_db: float) -> QColor:
        """Cut = amber → red as it gets deeper, boost = green, silenced = grey-white."""
        if math.isinf(gain_db) and gain_db < 0:
            return QColor(215, 220, 230)
        if gain_db > 0.05:
            return QColor(110, 230, 140)
        if gain_db > -0.05:
            return QColor(150, 200, 255)
        k = min(1.0, -gain_db / 30.0)
        return QColor(255, int(200 - 110 * k), int(90 - 50 * k))

    def _area_items(self, a, g: ViewGeometry):
        key = (a.selection, g.key)
        hit = self._area_cache.get(key)
        if hit is not None:
            return hit
        if len(self._area_cache) > 256:
            self._area_cache.clear()
        items = []
        fill = QPainterPath()
        fill.setFillRule(Qt.FillRule.WindingFill)
        for op in a.selection.ops:
            if op.shape is None:
                continue
            its = self._shape_items(op.shape, g, op.mode)
            items.extend(its)
            if op.mode in (REPLACE, ADD):
                for obj, closed, _dash in its:
                    if isinstance(obj, QPainterPath):
                        fill.addPath(obj)
                    elif closed:
                        fill.addPolygon(obj)
                        fill.closeSubpath()
        self._area_cache[key] = (items, fill)
        return items, fill

    def area_label_rect(self, a, g: ViewGeometry) -> QRectF | None:
        """Where the area's level badge is drawn (top-left corner of its bounds), or None if off-screen."""
        s = self.controller.session
        b = a.selection.bounds(s.project.duration, s.project.nyquist)
        if b is None:
            return None
        t0, t1, f0, f1 = b
        x0, x1 = float(g.x_of_t(t0)), float(g.x_of_t(t1))
        y_top, y_bot = float(g.y_of_f(min(f1, g.scale.f_hi))), float(g.y_of_f(max(f0, g.scale.f_lo)))
        if x1 < 0 or x0 > g.width or y_bot < 0 or y_top > g.height:
            return None
        return QRectF(max(0.0, x0), max(0.0, y_top), max(1.0, min(x1, g.width) - max(0.0, x0)),
                      max(1.0, min(y_bot, g.height) - max(0.0, y_top)))

    def _paint_areas(self, p: QPainter, g: ViewGeometry) -> None:
        from spledio.engine.areas import gain_text

        s = self.controller.session
        areas = s.visible_areas()
        if not areas:
            return
        cur = s.current_area()
        show = self.controller.view.show_areas
        f = QFont(self.font())
        f.setPixelSize(11)
        f.setBold(True)
        fm = QFontMetricsF(f)
        for a in areas:
            active = cur is not None and a.id == cur.id
            if not show and not active:
                continue
            col = self.area_colour(a.gain_db)
            items, fill = self._area_items(a, g)
            other_layer = a.layer_id != s.active_layer_id
            fc = QColor(col)
            fc.setAlpha(70 if active else (22 if other_layer else 42))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(fc)
            p.drawPath(fill)
            if math.isinf(a.gain_db):  # silenced: diagonal hatch so it reads as "removed" without colour
                hc = QColor(col)
                hc.setAlpha(90)
                p.setBrush(QBrush(hc, Qt.BrushStyle.BDiagPattern))
                p.drawPath(fill)
            p.setBrush(Qt.BrushStyle.NoBrush)
            pen = QPen(col, 2.4 if active else 1.4)
            pen.setCosmetic(True)
            if other_layer:
                pen.setStyle(Qt.PenStyle.DotLine)
            for obj, closed, _dash in items:
                p.setPen(pen)
                if isinstance(obj, QPainterPath):
                    p.drawPath(obj)
                elif closed:
                    p.drawPolygon(obj)
                else:
                    p.drawPolyline(obj)
            r = self.area_label_rect(a, g)
            if r is None:
                continue
            bar = getattr(self, "level_bar", None)
            if active and bar is not None and bar.isVisible():
                continue  # the level bar next to it already shows this area's name and level
            text = f"{a.name} · {gain_text(a.gain_db)}"
            if r.width() < fm.horizontalAdvance(text) + 14:
                text = gain_text(a.gain_db)
            w, h = fm.horizontalAdvance(text) + 10, fm.height() + 4
            badge = QRectF(r.left() + 3, max(2.0, r.top() - h - 2) if r.top() > h + 4 else r.top() + 3, w, h)
            badge.moveLeft(min(badge.left(), g.width - w - 2))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 200 if active else 160))
            p.drawRoundedRect(badge, 3, 3)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setFont(f)
            p.setPen(col)
            p.drawText(badge, Qt.AlignmentFlag.AlignCenter, text)

    def _paint_hint(self, p: QPainter, g: ViewGeometry) -> None:
        """One-line reminder of what the keys do with the current selection / picked area."""
        from spledio.engine.areas import gain_text

        s = self.controller.session
        if self._drag is not None or self._poly or s.selection is None:
            return
        if any(op.mode == INVERT for op in s.selection.ops):
            return
        a = s.current_area()
        if a is not None:
            text = (f"{a.name}: {gain_text(a.gain_db)}  ·  ↑ ↓ = 1 dB  ·  Shift+↑ ↓ = 6 dB  ·  "
                    "0 silence  ·  Delete removes the adjustment")
        else:
            text = "↑ ↓ make the selection 1 dB louder / quieter  ·  Shift = 6 dB  ·  0 silence  ·  S listen"
        if not self.hasFocus():
            text = "Click the spectrogram, then: " + text
        self._label(p, text, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom, QColor(225, 230, 240))

    def _paint_handles(self, p: QPainter, g: ViewGeometry) -> None:
        if self._drag is not None:
            return
        hp = self._handle_points(g)
        if not hp:
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        h = HANDLE_PX
        for (x, y) in hp.values():
            if -h <= x <= g.width + h and -h <= y <= g.height + h:
                r = QRectF(round(x - h / 2), round(y - h / 2), h, h)
                p.setPen(QPen(QColor(0, 0, 0, 230), 1.0))
                p.setBrush(QColor(255, 255, 255, 235))
                p.drawRect(r)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    # --- live interaction ---------------------------------------------------------
    def _paint_interaction(self, p: QPainter, g: ViewGeometry) -> None:
        d = self._drag
        if d is not None and d.moved and d.kind == "select":
            if d.tool == "lasso":
                if len(d.audio_pts) >= 2:
                    xy = _densify(np.asarray(d.audio_pts), g, closed=False)
                    self._stroke_items(p, [(_qpoly(xy), False, DASHES.get(d.mode, DASHES[REPLACE]))])
                    a, b = d.audio_pts[-1], d.audio_pts[0]
                    xy2 = _densify(np.asarray([a, b]), g, closed=False)
                    self._stroke_items(p, [(_qpoly(xy2), False, [1.5, 3.0])], faint=True)
            else:
                shape = self._shape_from_drag(d, g)
                if shape is not None:
                    self._stroke_items(p, self._shape_items(shape, g, d.mode))
        elif d is not None and d.kind == "zoombox" and d.moved:
            r = QRectF(d.press, d.cur).normalized()
            p.fillRect(r, QColor(255, 255, 255, 25))
            poly = np.array([[r.left(), r.top()], [r.right(), r.top()], [r.right(), r.bottom()], [r.left(), r.bottom()]])
            self._stroke_items(p, [(_qpoly(poly), True, [3.0, 3.0])])
        elif d is not None and d.kind == "brush":
            self._extend_live(d, g)
            p.fillPath(d.live, BRUSH_FILL.get(d.tool, BRUSH_FILL["sel_brush"]))
        if self._poly:
            self._paint_polygon(p, g)

    def _paint_polygon(self, p: QPainter, g: ViewGeometry) -> None:
        pts = np.asarray(self._poly)
        dash = DASHES.get(self._poly_mode, DASHES[REPLACE])
        if len(pts) >= 2:
            self._stroke_items(p, [(_qpoly(_densify(pts, g, closed=False)), False, dash)])
        if self._mouse is not None and len(pts) >= 1:
            m = self._audio_at(self._mouse, g, snap_dc=True)
            rubber = np.asarray([tuple(pts[-1]), m])
            self._stroke_items(p, [(_qpoly(_densify(rubber, g, closed=False)), False, dash)])
            if len(pts) >= 2:
                closing = np.asarray([m, tuple(pts[0])])
                self._stroke_items(p, [(_qpoly(_densify(closing, g, closed=False)), False, [1.5, 3.0])], faint=True)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        xs, ys = g.x_of_t(pts[:, 0]), g.y_of_f(pts[:, 1])
        near_first = self._near_first_vertex(self._mouse, g) if self._mouse is not None else False
        for i, (x, y) in enumerate(zip(np.atleast_1d(xs).tolist(), np.atleast_1d(ys).tolist())):
            big = i == 0 and near_first
            h = 9.0 if big else 6.0
            p.setPen(QPen(QColor(0, 0, 0, 230), 1.0))
            p.setBrush(QColor(255, 220, 90) if big else QColor(255, 255, 255))
            p.drawRect(QRectF(round(x - h / 2), round(y - h / 2), h, h))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    def _extend_live(self, d: _Drag, g: ViewGeometry) -> None:
        """Incrementally build the live stroke coverage (stamp footprints) in screen space."""
        if d.live_key != g.key:
            d.live = QPainterPath()
            d.live.setFillRule(Qt.FillRule.WindingFill)
            d.live_key = g.key
            d.live_n = 0
        if d.live_n >= len(d.path):
            return
        square = bool(self.controller.tools.brush_square)
        new = []
        if d.live_n == 0:
            new.append(np.asarray(d.path[0], dtype=np.float64)[None, :])
            d.live_n = 1
        for i in range(d.live_n, len(d.path)):
            new.append(stamps_between(d.path[i - 1], d.path[i], d.rt, d.rf, 0.5))
        d.live_n = len(d.path)
        st = np.vstack(new) if new else np.zeros((0, 2))
        _add_stamps(d.live, st, d.rt, d.rf, square, g)

    # --- playhead, crosshair, cursor, labels -------------------------------------------
    def _playhead_x(self, g: ViewGeometry | None = None) -> float | None:
        s = self.controller.session
        if s is None:
            return None
        g = g or self.view_geometry()
        return float(g.x_of_t(self.controller.playback.position / s.sample_rate))

    def _paint_playhead(self, p: QPainter, g: ViewGeometry) -> None:
        x = self._playhead_x(g)
        self._ph_x = x
        if x is None or not (-3 <= x <= g.width + 3):
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        p.setPen(QPen(QColor(0, 0, 0, 150), 3.0))
        p.drawLine(QPointF(x, 0), QPointF(x, g.height))
        p.setPen(QPen(Palette.PLAYHEAD, 1.0))
        p.drawLine(QPointF(x, 0), QPointF(x, g.height))
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    def _on_playhead(self, _pos: int) -> None:
        old = self._ph_x
        new = self._playhead_x()
        for x in (old, new):
            if x is not None and -4 <= x <= self.width() + 4:
                self.update(int(math.floor(x)) - 3, 0, 7, self.height())  # narrow strip only
        self._ph_x = new

    def _paint_crosshair(self, p: QPainter, g: ViewGeometry) -> None:
        m = self._mouse
        if m is None or not self.controller.view.show_crosshair:
            return
        d = self._drag
        if d is not None and d.kind == "pan":
            return
        pen = QPen(QColor(255, 255, 255, 80), 1.0)
        pen.setCosmetic(True)
        pen.setDashPattern([3.0, 3.0])
        p.setPen(pen)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        x, y = math.floor(m.x()) + 0.5, math.floor(m.y()) + 0.5
        p.drawLine(QPointF(x, 0), QPointF(x, g.height))
        p.drawLine(QPointF(0, y), QPointF(g.width, y))
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    def _paint_brush_cursor(self, p: QPainter, g: ViewGeometry) -> None:
        tool = self.controller.tools.tool
        m = self._mouse
        if tool not in BRUSH_KINDS or m is None:
            return
        d = self._drag
        if d is not None and d.kind == "brush":
            rt, rf = d.rt, d.rf
        else:
            rt, rf = self.brush_radii(m, g)
        ct, cf = float(g.t_of_x(m.x())), float(g.f_of_y(m.y()))
        tl = self.controller.tools
        xy = stamp_footprint(ct, cf, rt, rf, bool(tl.brush_square), g)
        self._stroke_items(p, [(_qpoly(xy), True, [3.0, 2.0])])
        hard = float(np.clip(tl.brush_hardness, 0.0, 1.0))
        if 0.02 < hard < 0.98:
            xy2 = stamp_footprint(ct, cf, rt * hard, rf * hard, bool(tl.brush_square), g)
            self._stroke_items(p, [(_qpoly(xy2), True, [1.0, 3.0])], faint=True)

    def _label(self, p: QPainter, text: str, align, colour: QColor | None = None) -> None:
        f = QFont(self.font())
        f.setPixelSize(11)
        f.setBold(True)
        p.setFont(f)
        fm = QFontMetricsF(f)
        w, h = fm.horizontalAdvance(text) + 12, fm.height() + 6
        x = 8 if align & Qt.AlignmentFlag.AlignLeft else self.width() - w - 8
        y = 8 if align & Qt.AlignmentFlag.AlignTop else self.height() - h - 8
        r = QRectF(x, y, w, h)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 170))
        p.drawRoundedRect(r, 4, 4)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(colour or Palette.TEXT)
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, text)

    def _paint_labels(self, p: QPainter, g: ViewGeometry) -> None:
        s = self.controller.session
        if s.preview is not None:
            self._label(p, "PREVIEW (not applied)", Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop,
                        QColor(255, 210, 100))
        vd = self._vd
        if vd is not None and vd.failed_tiles:
            self._label(p, "analysis failed for part of the view", Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom,
                        QColor(255, 120, 120))
        elif vd is not None and vd.pending_tiles:
            self._label(p, "analysing…", Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom, Palette.TEXT_DIM)

    # ================================================================ mapping helpers for input
    def _clamp_pos(self, pos: QPointF) -> tuple[float, float]:
        return min(max(pos.x(), 0.0), float(self.width())), min(max(pos.y(), 0.0), float(self.height()))

    def _t_at(self, x: float, g: ViewGeometry) -> float:
        s = self.controller.session
        t = float(g.t_of_x(x))
        dur = s.project.duration if s is not None else t
        return min(max(t, 0.0), dur)

    def _f_at(self, y: float, g: ViewGeometry, snap_dc: bool = True) -> float:
        """Frequency at ``y``; log views snap the bottom 3 px to 0 Hz when the view starts at/below the floor."""
        if snap_dc and g.scale.floor_at_bottom and y >= g.height - DC_SNAP_PX:
            return 0.0
        f = float(g.f_of_y(y))
        return min(max(f, 0.0), g.scale.nyquist)

    def _audio_at(self, pos: QPointF, g: ViewGeometry, snap_dc: bool = True) -> tuple[float, float]:
        x, y = self._clamp_pos(pos)
        return self._t_at(x, g), self._f_at(y, g, snap_dc)

    def _mode_for(self, mods) -> str:
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        alt = bool(mods & Qt.KeyboardModifier.AltModifier)
        if shift and alt:
            return INTERSECT
        if shift:
            return ADD
        if alt:
            return SUBTRACT
        return self.controller.tools.selection_mode

    def brush_radii(self, pos: QPointF, g: ViewGeometry | None = None) -> tuple[float, float]:
        """Stamp semi-axes (s, Hz) for a stroke starting at ``pos`` (see class docstring)."""
        g = g or self.view_geometry()
        tl = self.controller.tools
        if tl.brush_size_mode == "spectral":
            return max(1e-4, float(tl.brush_width_s) / 2.0), max(0.5, float(tl.brush_height_hz) / 2.0)
        ry = max(0.5, float(tl.brush_size_px) / 2.0)
        rx = max(0.5, ry * max(0.02, float(tl.brush_aspect)))
        rt = rx * g.sec_per_px
        f = float(g.f_of_y(min(max(pos.y(), 0.0), float(g.height))))
        rf = max(1e-3, ry * g.hz_per_px(f))
        return rt, rf

    # --- shapes from gestures ---------------------------------------------------------
    def _shape_from_drag(self, d: _Drag, g: ViewGeometry) -> Shape | None:
        # the press point is anchored in audio units (a wheel zoom mid-drag keeps it in place)
        pt, pf = d.press_audio
        ct, cf = self._audio_at(d.cur, g)
        ta, tb = sorted((pt, ct))
        fa, fb = sorted((pf, cf))
        if d.tool == "time":
            return time_range(ta, tb)
        if d.tool == "freq":
            return frequency_range(fa, fb)
        if d.tool == "rect":
            return RectShape(ta, tb, fa, fb)
        if d.tool == "ellipse":
            return EllipseShape(0.5 * (ta + tb), 0.5 * (fa + fb), 0.5 * (tb - ta), 0.5 * (fb - fa))
        if d.tool == "lasso":
            if len(d.audio_pts) < 3:
                return None
            keep = _rdp(d.screen_pts, 0.75)
            pts = tuple(d.audio_pts[i] for i in keep)
            if len(pts) < 3:
                pts = tuple(d.audio_pts)
            return PolygonShape(pts)
        return None

    def _handle_rect(self) -> RectShape | None:
        s = self.controller.session
        if s is None or s.selection is None:
            return None
        if self.controller.tools.tool not in HANDLE_TOOLS or self._poly:
            return None
        ops = s.selection.ops
        if len(ops) != 1 or ops[0].mode != REPLACE or not isinstance(ops[0].shape, RectShape):
            return None
        r = ops[0].shape
        if r.full_band and r.full_time:
            return None
        return r

    def _handle_points(self, g: ViewGeometry) -> dict[str, tuple[float, float]]:
        r = self._handle_rect()
        if r is None:
            return {}
        W, H = g.width, g.height
        if r.full_band:
            x0, x1 = (float(v) for v in g.x_of_t([r.t0, r.t1]))
            return {"w": (x0, H / 2.0), "e": (x1, H / 2.0)}
        if r.full_time:
            yt, yb = (float(v) for v in g.y_of_f([r.f1, r.f0]))
            return {"n": (W / 2.0, yt), "s": (W / 2.0, yb)}
        x0, x1 = (float(v) for v in g.x_of_t([r.t0, r.t1]))
        yt, yb = (float(v) for v in g.y_of_f([r.f1, r.f0]))
        xm, ym = 0.5 * (x0 + x1), 0.5 * (yt + yb)
        return {"nw": (x0, yt), "n": (xm, yt), "ne": (x1, yt), "e": (x1, ym),
                "se": (x1, yb), "s": (xm, yb), "sw": (x0, yb), "w": (x0, ym)}

    def _handle_at(self, pos: QPointF, g: ViewGeometry) -> str:
        best, best_d = "", HANDLE_HIT_PX
        for name, (x, y) in self._handle_points(g).items():
            dd = max(abs(pos.x() - x), abs(pos.y() - y))
            if dd <= best_d:
                best, best_d = name, dd
        return best

    def _edited_rect(self, d: _Drag, g: ViewGeometry) -> RectShape:
        r = d.rect0
        t0, t1, f0, f1 = r.t0, r.t1, r.f0, r.f1
        x, y = self._clamp_pos(d.cur)
        h = d.handle
        if "w" in h:
            t0 = self._t_at(x, g)
        if "e" in h:
            t1 = self._t_at(x, g)
        if "n" in h:
            f1 = self._f_at(y, g)
        if "s" in h:
            f0 = self._f_at(y, g)
        if r.full_band:
            return time_range(min(t0, t1), max(t0, t1))
        if r.full_time:
            return frequency_range(min(f0, f1), max(f0, f1))
        return RectShape(t0, t1, f0, f1)

    def _transform_delta(self, d: _Drag, g: ViewGeometry) -> tuple[float, float]:
        x, y = self._clamp_pos(d.cur)
        dt = float(g.t_of_x(x)) - d.press_t
        df = float(g.f_of_y(y)) - d.press_f
        return dt, df

    def _near_first_vertex(self, pos: QPointF | None, g: ViewGeometry) -> bool:
        if pos is None or len(self._poly) < 3:
            return False
        t, f = self._poly[0]
        x, y = float(g.x_of_t(t)), float(g.y_of_f(f))
        return max(abs(pos.x() - x), abs(pos.y() - y)) <= HANDLE_HIT_PX

    # ================================================================ public interaction API
    def cancel_interaction(self) -> bool:
        """Cancel a drag or polygon in progress (Esc). True if something was cancelled."""
        did = False
        if self._drag is not None:
            self._drag = None
            did = True
        if self._poly:
            self._poly = []
            did = True
        if did:
            self.update()
        return did

    def finish_polygon(self) -> bool:
        """Close the polygon lasso (Enter / double-click). True if a polygon was committed."""
        if len(self._poly) < 3:
            return False
        pts = tuple(self._poly)
        mode = self._poly_mode
        self._poly = []
        self.controller.commit_selection_shape(PolygonShape(pts), mode)
        self.update()
        return True

    def remove_last_vertex(self) -> bool:
        if not self._poly:
            return False
        self._poly.pop()
        self.update()
        return True

    # ================================================================ mouse
    def event(self, e) -> bool:
        r = super().event(e)
        t = e.type()
        bar = getattr(self, "level_bar", None)
        if bar is not None and t in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease,
                                     QEvent.Type.MouseButtonDblClick):
            bar.sync()  # hide while drawing / dragging, come back next to the new selection
        elif bar is not None and t == QEvent.Type.MouseMove and bar.isVisible() and self._drag is not None:
            bar.sync()
        return r

    def mousePressEvent(self, e) -> None:
        s = self.controller.session
        if s is None:
            return super().mousePressEvent(e)
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        pos = e.position()
        self._mouse = QPointF(pos)
        g = self.view_geometry()
        btn = e.button()
        mods = e.modifiers()
        tool = self.controller.tools.tool
        if self._drag is not None:  # a second button during a gesture: ignore
            e.accept()
            return
        if btn == Qt.MouseButton.MiddleButton:
            self._start_pan(pos, btn, mods, g)
            e.accept()
            return
        if btn != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(e)
        e.accept()
        combining = bool(mods & (Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.AltModifier))
        if tool in HANDLE_TOOLS and not self._poly and not combining:  # Shift/Alt = new shape, not a handle edit
            h = self._handle_at(pos, g)
            if h:
                d = _Drag("handle", tool, btn, pos, mods, REPLACE)
                d.handle = h
                d.rect0 = self._handle_rect()
                self._drag = d
                return
        if tool == "polygon":
            self._polygon_click(pos, mods, g)
            return
        if tool in SHAPE_TOOLS:
            d = _Drag("select", tool, btn, pos, mods, self._mode_for(mods))
            d.press_audio = self._audio_at(pos, g)
            if tool == "lasso":
                d.screen_pts.append(self._clamp_pos(pos))
                d.audio_pts.append(self._audio_at(pos, g))
            self._drag = d
        elif tool == "transform":
            d = _Drag("transform", tool, btn, pos, mods, REPLACE)
            x, y = self._clamp_pos(pos)
            d.press_t, d.press_f = float(g.t_of_x(x)), float(g.f_of_y(y))
            self._drag = d
        elif tool in BRUSH_KINDS:
            d = _Drag("brush", tool, btn, pos, mods, REPLACE)
            d.rt, d.rf = self.brush_radii(pos, g)
            d.path.append(self._audio_at(pos, g, snap_dc=False))
            self._drag = d
        elif tool == "hand":
            self._start_pan(pos, btn, mods, g)
        elif tool == "zoom":
            self._drag = _Drag("zoombox", tool, btn, pos, mods, REPLACE)
        self.update()

    def _start_pan(self, pos, btn, mods, g: ViewGeometry) -> None:
        d = _Drag("pan", "hand", btn, pos, mods, REPLACE)
        v = self.controller.view
        d.view0 = (v.t0, v.t1, v.f_lo, v.f_hi, g.scale)
        self._drag = d
        self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def _polygon_click(self, pos: QPointF, mods, g: ViewGeometry) -> None:
        if self._near_first_vertex(pos, g):
            self.finish_polygon()
            return
        if not self._poly:
            self._poly_mode = self._mode_for(mods)
        self._poly.append(self._audio_at(pos, g))
        self.update()

    def mouseDoubleClickEvent(self, e) -> None:
        if self.controller.tools.tool == "polygon" and e.button() == Qt.MouseButton.LeftButton and self._poly:
            # the first click of the double-click already added this vertex
            self.finish_polygon()
            e.accept()
            return
        self.mousePressEvent(e)

    def mouseMoveEvent(self, e) -> None:
        pos = e.position()
        self._mouse = QPointF(pos)
        d = self._drag
        g = self.view_geometry()
        if d is not None:
            d.cur = QPointF(pos)
            if not d.moved and math.hypot(pos.x() - d.press.x(), pos.y() - d.press.y()) >= CLICK_PX:
                d.moved = True
            if d.kind == "select" and d.tool == "lasso":
                sp = self._clamp_pos(pos)
                lx, ly = d.screen_pts[-1]
                if math.hypot(sp[0] - lx, sp[1] - ly) >= 1.0:
                    d.screen_pts.append(sp)
                    d.audio_pts.append(self._audio_at(pos, g))
            elif d.kind == "brush":
                a = self._audio_at(pos, g, snap_dc=False)
                lt, lf = d.path[-1]
                if abs(float(g.x_of_t(a[0])) - float(g.x_of_t(lt))) + abs(float(g.y_of_f(a[1])) - float(g.y_of_f(lf))) >= 0.5:
                    d.path.append(a)
            elif d.kind == "pan":
                self._apply_pan(d, pos)
            self.update()
        else:
            tool = self.controller.tools.tool
            h = self._handle_at(pos, g) if tool in HANDLE_TOOLS else ""
            if h:
                shapes = {"n": Qt.CursorShape.SizeVerCursor, "s": Qt.CursorShape.SizeVerCursor,
                          "e": Qt.CursorShape.SizeHorCursor, "w": Qt.CursorShape.SizeHorCursor,
                          "nw": Qt.CursorShape.SizeFDiagCursor, "se": Qt.CursorShape.SizeFDiagCursor,
                          "ne": Qt.CursorShape.SizeBDiagCursor, "sw": Qt.CursorShape.SizeBDiagCursor}
                self.setCursor(shapes[h])
            else:
                self._on_tools_changed_cursor_only()
            if self.controller.view.show_crosshair or tool in BRUSH_KINDS or self._poly:
                self.update()
        self._emit_cursor_info(pos, g)

    def _on_tools_changed_cursor_only(self) -> None:
        tool = self.controller.tools.tool
        want = {"hand": Qt.CursorShape.OpenHandCursor, "transform": Qt.CursorShape.SizeAllCursor}.get(
            tool, Qt.CursorShape.CrossCursor)
        if self.cursor().shape() != want:
            self.setCursor(want)

    def mouseReleaseEvent(self, e) -> None:
        d = self._drag
        if d is None or e.button() != d.button:
            return super().mouseReleaseEvent(e)
        e.accept()
        d.cur = QPointF(e.position())
        if not d.moved and math.hypot(d.cur.x() - d.press.x(), d.cur.y() - d.press.y()) >= CLICK_PX:
            d.moved = True
        self._drag = None
        g = self.view_geometry()
        c = self.controller
        s = c.session
        if s is None:
            return
        if d.kind == "select":
            if not d.moved:
                if not self._pick_area_at(d.press, g):
                    self._seek_at(d.press, g)
            else:
                if d.tool == "lasso":
                    a = self._audio_at(d.cur, g)
                    if not d.audio_pts or a != d.audio_pts[-1]:
                        d.screen_pts.append(self._clamp_pos(d.cur))
                        d.audio_pts.append(a)
                shape = self._shape_from_drag(d, g)
                if shape is not None:
                    c.commit_selection_shape(shape, d.mode)
        elif d.kind == "handle":
            if d.moved and s.selection is not None:
                s.set_selection(s.selection.combine(REPLACE, self._edited_rect(d, g)))
            elif not d.moved:
                if not self._pick_area_at(d.press, g):
                    self._seek_at(d.press, g)
        elif d.kind == "transform":
            if d.moved and s.selection is not None:
                dt, df = self._transform_delta(d, g)
                s.set_selection(s.selection.transformed(dt, df))
            elif not d.moved:
                self._seek_at(d.press, g)
        elif d.kind == "brush":
            a = self._audio_at(d.cur, g, snap_dc=False)
            if a != d.path[-1]:
                d.path.append(a)
            tl = c.tools
            shape = BrushShape(tuple(d.path), d.rt, d.rf, float(tl.brush_hardness), bool(tl.brush_square))
            kind = BRUSH_KINDS[d.tool]
            if kind is None:
                alt = bool(d.mods & Qt.KeyboardModifier.AltModifier)
                mode = SUBTRACT if alt else (ADD if s.selection is not None else REPLACE)
                c.commit_selection_shape(shape, mode)
            else:
                c.brush_stroke(shape, kind)
        elif d.kind == "pan":
            self._on_tools_changed_cursor_only()
        elif d.kind == "zoombox":
            v = c.view
            if d.moved:
                x0, y0 = self._clamp_pos(d.press)
                x1, y1 = self._clamp_pos(d.cur)
                ta, tb = sorted((float(g.t_of_x(x0)), float(g.t_of_x(x1))))
                fa, fb = sorted((float(g.f_of_y(y0)), float(g.f_of_y(y1))))
                v.fit_box(ta, tb, fa, fb, margin=0.0)
            else:
                factor = 2.0 if bool(d.mods & Qt.KeyboardModifier.AltModifier) else 0.5
                x, y = self._clamp_pos(d.press)
                t, f = float(g.t_of_x(x)), float(g.f_of_y(y))
                v.zoom_time(factor, anchor_t=t)
                v.zoom_freq(factor, anchor_f=f)
        self.update()

    def _pick_area_at(self, pos: QPointF, g: ViewGeometry) -> bool:
        """A plain click inside an adjusted area (or on its badge) picks it, so ↑/↓ and Delete act on it.
        Clicking the area that is already picked moves the playhead instead."""
        s = self.controller.session
        if s is None or not s.areas:
            return False
        show = self.controller.view.show_areas
        x, y = self._clamp_pos(pos)
        t, f = float(g.t_of_x(x)), float(g.f_of_y(y))
        a = s.area_at(t, f)
        if a is None:  # thin areas: accept a click on the badge too
            for cand in reversed(s.visible_areas()):
                r = self.area_label_rect(cand, g)
                if r is not None and r.adjusted(-4, -22, 4, 4).contains(QPointF(x, y)):
                    a = cand
                    break
        cur = s.current_area()
        if a is None or (not show and (cur is None or a.id != cur.id)):
            return False
        if cur is not None and a.id == cur.id:
            return False
        self.controller.select_area(a.id)
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        return True

    def _seek_at(self, pos: QPointF, g: ViewGeometry) -> None:
        x, _y = self._clamp_pos(pos)
        self.controller.seek_seconds(self._t_at(x, g))

    def _apply_pan(self, d: _Drag, pos: QPointF) -> None:
        t0, t1, f_lo0, f_hi0, scale0 = d.view0
        v = self.controller.view
        W, H = max(1, self.width()), max(1, self.height())
        dx, dy = pos.x() - d.press.x(), pos.y() - d.press.y()
        span = t1 - t0
        nt0 = t0 - dx * span / W
        v.set_time_range(nt0, nt0 + span, emit=False)
        du = dy / H  # drag down -> content moves down -> higher frequencies come into view
        nyq = scale0.nyquist
        if scale0.kind == FrequencyScale.LINEAR:
            fspan = f_hi0 - f_lo0
            lo = min(max(f_lo0 + du * fspan, 0.0), max(0.0, nyq - fspan))
            hi = lo + fspan
        else:
            la, lh = math.log10(scale0.axis_lo), math.log10(scale0.f_hi)
            lspan = lh - la
            floor_l, top_l = math.log10(scale0.floor_hz), math.log10(nyq)
            nla = min(max(la + du * lspan, floor_l), top_l - lspan)
            hi = 10.0 ** (nla + lspan)
            if nla <= floor_l + 1e-9:
                lo = 0.0 if f_lo0 <= scale0.floor_hz else scale0.floor_hz
            else:
                lo = 10.0 ** nla
        v.set_freq_range(lo, hi)  # emits 'viewport' once for both axes

    # ================================================================ wheel / keys
    def wheelEvent(self, e) -> None:
        if self.controller.session is None:
            return super().wheelEvent(e)
        v = self.controller.view
        g = self.view_geometry()
        pos = e.position()
        mods = e.modifiers()
        ad, pd = e.angleDelta(), e.pixelDelta()
        dx, dy = ad.x(), ad.y()
        if mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier):
            d = dy if dy else dx
            if d:
                v.zoom_time(0.85 ** (d / 120.0), anchor_t=float(g.t_of_x(pos.x())))
        elif mods & Qt.KeyboardModifier.ShiftModifier:
            d = dy if dy else dx  # macOS turns Shift+wheel into a horizontal delta
            if d:
                v.zoom_freq(0.85 ** (d / 120.0), anchor_f=float(g.f_of_y(pos.y())))
        else:
            span = v.t1 - v.t0
            if not pd.isNull():
                px = pd.x() if abs(pd.x()) > abs(pd.y()) else pd.y()
                dt = -px * span / max(1, self.width())
            else:
                d = dy if abs(dy) >= abs(dx) else dx
                dt = -d / 120.0 * 0.1 * span
            if dt:
                v.pan_time(dt)
        e.accept()

    def event(self, e) -> bool:
        # let Esc / Enter / Backspace reach keyPressEvent instead of window shortcuts
        # while (and only while) a gesture or polygon is in progress
        if e.type() == QEvent.Type.ShortcutOverride and self.interaction_in_progress():
            if e.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Backspace):
                e.accept()
                return True
        return super().event(e)

    def keyPressEvent(self, e) -> None:
        k = e.key()
        if k == Qt.Key.Key_Escape and self.cancel_interaction():
            e.accept()
            return
        if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self._poly:
            self.finish_polygon()
            e.accept()
            return
        if k == Qt.Key.Key_Backspace and self._poly:
            self.remove_last_vertex()
            e.accept()
            return
        e.ignore()  # main-window shortcuts keep working

    def focusInEvent(self, e) -> None:
        super().focusInEvent(e)
        self.update()  # the key hint changes with focus

    def focusOutEvent(self, e) -> None:
        super().focusOutEvent(e)
        self.update()

    def leaveEvent(self, e) -> None:
        self._mouse = None
        if self._last_info != "":
            self._last_info = ""
            self.cursorInfo.emit("")
        self.update()
        super().leaveEvent(e)

    # ================================================================ cursor readout
    def cursor_info_at(self, x: float, y: float) -> str:
        s = self.controller.session
        W, H = self.width(), self.height()
        if s is None or not (0 <= x < W and 0 <= y < H):
            return ""
        g = self.view_geometry()
        t = float(g.t_of_x(x))
        f = float(g.f_of_y(y))
        parts = [f"{t:.4f} s"]
        note = note_with_cents(f)
        parts.append(f"{f:.1f} Hz" + (f" ({note})" if note else ""))
        vd = self._vd
        col, row = int(x), int(y)
        if vd is not None and vd.db.shape == (H, W):
            st = int(vd.status[row, col])
            if st in (TileStatus.READY, TileStatus.STALE):
                db = float(vd.db[row, col])
                parts.append("silent" if db <= DB_FLOOR + 1e-3 else f"{_fmt_signed(db, 1)} dB")
            elif st == TileStatus.PENDING:
                parts.append("pending")
            elif st == TileStatus.FAILED:
                parts.append("analysis failed")
            else:
                parts.append("outside audio")
        else:
            parts.append("pending")
        return " · ".join(parts)

    def _emit_cursor_info(self, pos: QPointF, g: ViewGeometry | None = None) -> None:
        info = self.cursor_info_at(pos.x(), pos.y())
        if info != self._last_info:
            self._last_info = info
            self.cursorInfo.emit(info)


class SpectrogramEditor(QWidget):
    """Overview + waveform + rulers + canvas + scroll bar (see module docstring)."""

    cursorInfo = Signal(str)

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.setAutoFillBackground(True)
        pal = self.palette()
        pal.setColor(self.backgroundRole(), Palette.BG)
        self.setPalette(pal)
        self.canvas = SpectrogramCanvas(controller, self)
        self.envelope = MixEnvelope(controller, self)
        self.overview = OverviewBar(controller, self.envelope, self)
        self.waveform = WaveformStrip(controller, self.envelope, self)
        self.freq_ruler = FrequencyRuler(controller, self.canvas, self)
        self.time_ruler = TimeRuler(controller, self.canvas, self)
        self.hscroll = QScrollBar(Qt.Orientation.Horizontal, self)
        self.hscroll.setToolTip("Scroll through time")
        self.hscroll.setStyleSheet(
            "QScrollBar:horizontal { background: #181a1f; height: 12px; margin: 0; border: none; }"
            "QScrollBar::handle:horizontal { background: #4a4f5a; min-width: 24px; border-radius: 4px; margin: 2px; }"
            "QScrollBar::handle:horizontal:hover { background: #646a77; }"
            "QScrollBar::handle:horizontal:disabled { background: #2a2d34; }"
            "QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; border: none; }"
            "QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: none; }"
        )
        self._syncing = False
        self._units = 1000.0

        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(0)
        grid.addWidget(self.overview, 0, 1)
        grid.addWidget(self.waveform, 1, 1)
        grid.addWidget(self.freq_ruler, 2, 0)
        grid.addWidget(self.canvas, 2, 1)
        grid.addWidget(self.time_ruler, 3, 1)
        grid.addWidget(self.hscroll, 4, 1)
        grid.setColumnStretch(1, 1)
        grid.setRowStretch(2, 1)
        corner = QWidget(self)
        corner.setFixedWidth(FrequencyRuler.WIDTH)
        corner.setAutoFillBackground(True)
        cp = corner.palette()
        cp.setColor(corner.backgroundRole(), Palette.PANEL)
        corner.setPalette(cp)
        grid.addWidget(corner, 3, 0, 2, 1)

        self.canvas.cursorInfo.connect(self.cursorInfo)
        self.hscroll.valueChanged.connect(self._on_scroll)
        controller.view.changed.connect(self._on_view_changed)
        controller.sessionReplaced.connect(self._sync_all)
        controller.structureChanged.connect(self._sync_scroll)
        controller.playheadChanged.connect(self._on_playhead)
        self._sync_all()

    # --------------------------------------------------------------- public API
    def cancel_interaction(self) -> bool:
        return self.canvas.cancel_interaction()

    def finish_polygon(self) -> bool:
        return self.canvas.finish_polygon()

    def minimumSizeHint(self) -> QSize:
        return QSize(FrequencyRuler.WIDTH + 400, 250 + TimeRuler.HEIGHT + 16)

    # --------------------------------------------------------------- sync
    def _sync_all(self) -> None:
        v = self.controller.view
        self.overview.setVisible(bool(v.show_overview))
        self.waveform.setVisible(bool(v.show_waveform))
        self._sync_scroll()

    def _on_view_changed(self, what: str) -> None:
        if what == "display":
            v = self.controller.view
            # display-only toggles: hiding a strip never touches audio or playback
            if self.overview.isHidden() == bool(v.show_overview):
                self.overview.setVisible(bool(v.show_overview))
            if self.waveform.isHidden() == bool(v.show_waveform):
                self.waveform.setVisible(bool(v.show_waveform))
        self._sync_scroll()

    def _sync_scroll(self) -> None:
        v = self.controller.view
        s = self.controller.session
        self._syncing = True
        try:
            if s is None:
                self.hscroll.setRange(0, 0)
                self.hscroll.setEnabled(False)
                return
            span = max(v.t1 - v.t0, 1e-9)
            pages = max(1.0, s.project.duration / span)
            self._units = float(max(1, min(1000, int(2e9 / pages))))
            maxv = int(round(max(0.0, s.project.duration - span) / span * self._units))
            self.hscroll.setEnabled(maxv > 0)
            self.hscroll.setRange(0, maxv)
            self.hscroll.setPageStep(int(self._units))
            self.hscroll.setSingleStep(max(1, int(self._units // 20)))
            self.hscroll.setValue(int(round(v.t0 / span * self._units)))
        finally:
            self._syncing = False

    def _on_scroll(self, value: int) -> None:
        if self._syncing:
            return
        v = self.controller.view
        span = v.t1 - v.t0
        t0 = value / self._units * span
        v.set_time_range(t0, t0 + span)

    def _on_playhead(self, pos: int) -> None:
        c = self.controller
        s = c.session
        v = c.view
        if s is None or not v.playhead_follow or not c.playback.is_playing:
            return
        t = pos / s.sample_rate
        if t >= v.t1 or t < v.t0:  # page so the playhead is at the left edge
            span = v.t1 - v.t0
            v.set_time_range(t, t + span)
