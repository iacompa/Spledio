"""Spectrogram editor canvas tests (offscreen Qt, pytest-qt).

All audio here is a SYNTHETIC TEST FIXTURE generated in this file (sweeps,
steady tones and seeded noise); no recorded material is used.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import gc
import math
import time

import numpy as np
import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from spledio.dsp.stft import STFTConfig
from spledio.engine.session import EditorSession
from spledio.playback.engine import FakeBackend
from spledio.rendering.analysis import TileStatus
from spledio.rendering.image import hatch_colour, map_grid_to_view
from spledio.rendering.scales import FrequencyScale
from spledio.selections.geometry import BrushShape, EllipseShape, PolygonShape, RectShape
from spledio.selections.selection import Selection, rasterize
from spledio.ui.controller import EditorController
from spledio.ui.spectrogram_view import SpectrogramEditor, ViewGeometry, selection_overlay_values

SR = 48000
DUR = 3.0
NO_MOD = Qt.KeyboardModifier.NoModifier
LEFT = Qt.MouseButton.LeftButton


def synth_signal(dur: float = DUR, sr: int = SR) -> np.ndarray:
    """SYNTHETIC TEST FIXTURE: stereo sweeps + steady tones (440/1000/3000 Hz) + seeded noise."""
    t = np.arange(int(dur * sr)) / sr
    lin = 0.2 * np.sin(2 * np.pi * (100 * t + (8000 - 100) / (2 * dur) * t * t))  # 100 Hz -> 8 kHz
    tones = 0.2 * np.sin(2 * np.pi * 440 * t) + 0.2 * np.sin(2 * np.pi * 1000 * t) + 0.08 * np.sin(2 * np.pi * 3000 * t)
    noise = 0.002 * np.random.default_rng(1234).standard_normal(len(t))
    x = lin + tones + noise
    return np.stack([x, 0.8 * x])


@pytest.fixture(autouse=True)
def _collect_on_gui_thread(qapp):
    """Free this test's Qt objects here, on the GUI thread.

    Objects caught in reference cycles are otherwise freed whenever the cyclic GC
    happens to run, possibly on a worker thread of a later test, and destroying
    QObjects/QWidgets off the GUI thread crashes Qt.
    """
    yield
    QApplication.sendPostedEvents(None, 0)
    gc.collect()


@pytest.fixture
def ctl(qapp):
    c = EditorController(playback_backend=FakeBackend())
    c.set_session(EditorSession.from_array(synth_signal(), SR))
    yield c
    c.shutdown()
    # break the controller's closure cycles (playback/analyser callbacks capture it)
    c.playback.on_error = None
    c.playback.on_finished = None
    c.analyzer.on_tile_ready = None


def settle(qtbot, editor, timeout: float = 20.0):
    cv = editor.canvas
    an = editor.controller.analyzer
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        an.wait_idle(2.0)
        qtbot.wait(15)
        if cv.is_settled():
            qtbot.wait(15)  # flush late tilesReady notifications
            if cv.is_settled():
                return cv.view_data
    raise AssertionError("spectrogram view did not settle")


def dispose(w) -> None:
    """Close and delete an editor on the GUI thread (deterministic teardown)."""
    w.close()
    w.deleteLater()
    QApplication.sendPostedEvents(None, 0)


@pytest.fixture
def editor(qtbot, ctl):
    w = SpectrogramEditor(ctl)
    w.resize(800, 400)
    w.show()
    qtbot.waitExposed(w)
    settle(qtbot, w)
    yield w
    dispose(w)


def drag(widget, p0, p1, mods=NO_MOD, steps: int = 6, button=LEFT):
    QTest.mousePress(widget, button, mods, QPoint(*p0))
    for i in range(1, steps + 1):
        x = p0[0] + (p1[0] - p0[0]) * i / steps
        y = p0[1] + (p1[1] - p0[1]) * i / steps
        QTest.mouseMove(widget, QPoint(round(x), round(y)))
    QTest.mouseRelease(widget, button, mods, QPoint(*p1))


def independent_mapping(ctl, W, H):
    """Pixel <-> audio mapping computed from the view state alone (not via the canvas)."""
    v = ctl.view
    scale = FrequencyScale(v.freq_scale, v.f_lo, v.f_hi, SR / 2.0, v.log_floor_hz)

    def t_of_x(x):
        return v.t0 + x / W * (v.t1 - v.t0)

    def x_of_t(t):
        return (t - v.t0) / (v.t1 - v.t0) * W

    def y_of_f(f):
        return (1.0 - float(scale.to_unit(f))) * H

    def f_of_y(y):
        return float(scale.from_unit(1.0 - y / H))

    return t_of_x, x_of_t, f_of_y, y_of_f, scale


def expected_rect_lit(rect: RectShape, ctl, W, H, cfg: STFTConfig):
    """Pixels whose nearest processing cell lies inside ``rect`` (independent computation)."""
    v = ctl.view
    s = ctl.session
    scale = FrequencyScale(v.freq_scale, v.f_lo, v.f_hi, SR / 2.0, v.log_floor_hz)
    tc = v.t0 + (np.arange(W) + 0.5) * ((v.t1 - v.t0) / W)
    k = np.floor(tc * SR / cfg.hop + 0.5).astype(np.int64)
    u = (np.arange(H) + 0.5) / H  # ascending rows (bottom first)
    fc = np.asarray(scale.from_unit(u))[::-1]  # top first
    b = np.floor(fc * cfg.n_fft / SR + 0.5).astype(np.int64)
    tk = k * cfg.hop / SR
    fb = b * SR / cfg.n_fft
    fk0, fk1 = cfg.full_frame_range(s.project.n_frames)
    col_in = (tk >= rect.t0) & (tk <= rect.t1) & (k >= fk0) & (k < fk1)
    row_in = (fb >= rect.f0) & (fb <= rect.f1) & (b >= 0) & (b < cfg.n_bins)
    mask = (row_in[:, None] & col_in[None, :]).astype(float)
    return mask > 0.5


# --------------------------------------------------------------------- rendering
def test_constructs_renders_and_resolves_pending(qtbot, ctl):
    w = SpectrogramEditor(ctl)
    try:
        _check_constructs_renders_and_resolves_pending(qtbot, w)
    finally:
        dispose(w)


def _check_constructs_renders_and_resolves_pending(qtbot, w):
    w.resize(1366, 768)
    w.show()
    qtbot.waitExposed(w)
    cv = w.canvas
    # first render: tiles are not analysed yet -> PENDING pixels are hatched, never fabricated
    cv.render_now()
    vd = cv.view_data
    assert vd is not None and vd.db.shape == (cv.height(), cv.width())
    pend = vd.status == TileStatus.PENDING
    if pend.any():
        img = cv.image_rgba
        c1 = np.array((40, 40, 48, 255), dtype=np.uint8)
        c2 = np.array(hatch_colour((40, 40, 48, 255)), dtype=np.uint8)
        px = img[pend]
        assert np.all(np.all(px == c1, axis=1) | np.all(px == c2, axis=1))
        assert np.all(np.isnan(vd.db[pend]))
    for size in ((1366, 768), (800, 400)):
        w.resize(*size)
        qtbot.wait(20)
        vd = settle(qtbot, w)
        H, W = cv.height(), cv.width()
        assert W >= 400 and H >= 250
        assert vd.db.shape == (H, W)
        assert not np.any(vd.status == TileStatus.PENDING)
        assert cv.image_rgba.shape == (H, W, 4)
        assert not w.grab().isNull()
    # rulers and strips line up with the canvas column/row
    assert w.time_ruler.width() == cv.width()
    assert w.waveform.width() == cv.width()
    assert w.freq_ruler.height() == cv.height()


def test_display_changes_only_recolour(qtbot, editor, monkeypatch):
    c = editor.controller
    cv = editor.canvas
    layer = c.session.active_layer
    audio_before = layer.audio.copy()
    calls = []
    orig = c.analyzer.view_data

    def counting(*a, **k):
        calls.append(1)
        return orig(*a, **k)

    monkeypatch.setattr(c.analyzer, "view_data", counting)
    img0 = cv.image_rgba.copy()
    renders0 = cv.render_count
    c.view.set_display(colormap="ice")
    qtbot.wait(30)
    img1 = cv.image_rgba.copy()
    assert not np.array_equal(img0, img1)
    c.view.set_display(min_db=-80.0, max_db=-10.0)
    qtbot.wait(30)
    img2 = cv.image_rgba.copy()
    assert not np.array_equal(img1, img2)
    c.view.set_display(gamma=0.6)
    qtbot.wait(30)
    assert not np.array_equal(img2, cv.image_rgba)
    assert cv.render_count > renders0
    assert calls == []
    assert np.array_equal(layer.audio, audio_before)
    # sanity: a viewport change does analyse again
    c.view.zoom_time(0.5)
    qtbot.wait(30)
    assert calls


# --------------------------------------------------------------------- selection tools
def test_rect_time_freq_drags_map_pixels_to_audio(qtbot, editor):
    c = editor.controller
    cv = editor.canvas
    W, H = cv.width(), cv.height()
    t_of_x, _x_of_t, f_of_y, y_of_f, _scale = independent_mapping(c, W, H)
    px_t = (c.view.t1 - c.view.t0) / W

    c.tools.set(tool="rect")
    drag(cv, (100, 60), (300, 180))
    sel = c.session.selection
    assert sel is not None and len(sel.ops) == 1 and sel.ops[0].mode == "replace"
    r = sel.ops[0].shape
    assert isinstance(r, RectShape) and not r.full_band and not r.full_time
    assert abs(r.t0 - t_of_x(100)) <= px_t
    assert abs(r.t1 - t_of_x(300)) <= px_t
    assert abs(y_of_f(r.f1) - 60) <= 1.0  # one pixel's worth, also in a log view
    assert abs(y_of_f(r.f0) - 180) <= 1.0
    assert abs(r.f1 - f_of_y(60)) <= abs(f_of_y(59) - f_of_y(61))

    c.tools.set(tool="time")
    drag(cv, (350, 40), (150, 200))  # right-to-left also works
    r = c.session.selection.ops[0].shape
    assert r.full_band and len(c.session.selection.ops) == 1
    assert abs(r.t0 - t_of_x(150)) <= px_t and abs(r.t1 - t_of_x(350)) <= px_t

    c.tools.set(tool="freq")
    drag(cv, (200, 50), (230, 150))
    r = c.session.selection.ops[0].shape
    assert r.full_time
    assert abs(y_of_f(r.f1) - 50) <= 1.0 and abs(y_of_f(r.f0) - 150) <= 1.0

    c.tools.set(tool="rect")
    drag(cv, (100, 60), (300, 180))
    drag(cv, (400, 80), (500, 160), mods=Qt.KeyboardModifier.ShiftModifier)
    ops = c.session.selection.ops
    assert [o.mode for o in ops] == ["replace", "add"]
    assert abs(ops[1].shape.t0 - t_of_x(400)) <= px_t
    drag(cv, (150, 90), (200, 120), mods=Qt.KeyboardModifier.AltModifier)
    assert c.session.selection.ops[-1].mode == "subtract"
    drag(cv, (90, 50), (450, 190), mods=Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.AltModifier)
    assert c.session.selection.ops[-1].mode == "intersect"

    c.tools.set(tool="ellipse")
    drag(cv, (100, 60), (300, 180))
    e = c.session.selection.ops[0].shape
    assert isinstance(e, EllipseShape)
    assert abs(e.tc - 0.5 * (t_of_x(100) + t_of_x(300))) <= px_t
    assert abs(e.rt - 0.5 * (t_of_x(300) - t_of_x(100))) <= px_t


def test_log_drag_to_bottom_snaps_to_dc(qtbot, editor):
    c = editor.controller
    cv = editor.canvas
    assert c.view.freq_scale == "log" and c.view.f_lo <= c.view.log_floor_hz
    c.tools.set(tool="rect")
    drag(cv, (100, 60), (300, cv.height() - 1))
    assert c.session.selection.ops[0].shape.f0 == 0.0
    c.session.set_selection(None)  # (a press on the old rect's corner would grab its handle)
    drag(cv, (100, 60), (300, cv.height() - 20))
    assert c.session.selection.ops[0].shape.f0 > 0.0


def test_click_seeks_and_keeps_selection(qtbot, editor):
    c = editor.controller
    cv = editor.canvas
    c.commit_selection_shape(RectShape(1.0, 2.0, 500.0, 4000.0), "replace")
    sel = c.session.selection
    c.tools.set(tool="rect")
    QTest.mouseClick(cv, LEFT, NO_MOD, QPoint(200, 100))
    t_of_x, *_ = independent_mapping(c, cv.width(), cv.height())
    assert c.session.selection is sel
    assert abs(c.playback.position / SR - t_of_x(200)) <= (c.view.t1 - c.view.t0) / cv.width() + 1 / SR
    # click on the time ruler seeks too
    QTest.mouseClick(editor.time_ruler, LEFT, NO_MOD, QPoint(400, 8))
    assert abs(c.playback.position / SR - t_of_x(400)) <= (c.view.t1 - c.view.t0) / cv.width() + 1 / SR


def test_lasso_and_polygon_tools(qtbot, editor):
    c = editor.controller
    cv = editor.canvas
    c.tools.set(tool="lasso")
    QTest.mousePress(cv, LEFT, NO_MOD, QPoint(150, 60))
    for k in range(1, 41):
        a = 2 * math.pi * k / 40
        QTest.mouseMove(cv, QPoint(round(250 + 100 * math.sin(a) - 0), round(120 - 60 * math.cos(a))))
    QTest.mouseRelease(cv, LEFT, NO_MOD, QPoint(150, 60))
    shp = c.session.selection.ops[0].shape
    assert isinstance(shp, PolygonShape) and len(shp.points) >= 3

    c.session.set_selection(None)
    c.tools.set(tool="polygon")
    W, H = cv.width(), cv.height()
    t_of_x, _x, f_of_y, y_of_f, _s = independent_mapping(c, W, H)
    pts = [(100, 60), (300, 70), (280, 200), (120, 190)]
    for p in pts:
        QTest.mouseClick(cv, LEFT, NO_MOD, QPoint(*p))
    assert len(cv.polygon_points) == 4
    assert c.session.selection is None
    assert editor.finish_polygon()
    shp = c.session.selection.ops[-1].shape
    assert isinstance(shp, PolygonShape) and len(shp.points) == 4
    for (x, y), (t, f) in zip(pts, shp.points):
        assert abs(t - t_of_x(x)) <= (c.view.t1 - c.view.t0) / W
        assert abs(y_of_f(f) - y) <= 1.0
    assert cv.polygon_points == []

    sel_before = c.session.selection
    QTest.mouseClick(cv, LEFT, NO_MOD, QPoint(150, 100))
    QTest.mouseClick(cv, LEFT, NO_MOD, QPoint(250, 120))
    QTest.mouseClick(cv, LEFT, NO_MOD, QPoint(260, 180))
    assert len(cv.polygon_points) == 3
    QTest.keyClick(cv, Qt.Key.Key_Backspace)
    assert len(cv.polygon_points) == 2
    QTest.keyClick(cv, Qt.Key.Key_Escape)
    assert cv.polygon_points == []
    assert c.session.selection is sel_before
    assert editor.cancel_interaction() is False
    assert editor.finish_polygon() is False


def test_selection_stays_in_audio_units_across_view_changes(qtbot, editor):
    c = editor.controller
    cv = editor.canvas
    v = c.view
    rect = RectShape(1.0, 2.0, 500.0, 4000.0)
    c.commit_selection_shape(rect, "replace")
    sel = c.session.selection
    cfg = c.session.edit_config

    def check():
        settle(qtbot, editor)
        W, H = cv.width(), cv.height()
        _t, x_of_t, _f, y_of_f, _scale = independent_mapping(c, W, H)
        x0, y1 = cv.audio_to_px(rect.t0, rect.f1)
        x1, y0 = cv.audio_to_px(rect.t1, rect.f0)
        assert abs(x0 - x_of_t(rect.t0)) < 1e-6 and abs(x1 - x_of_t(rect.t1)) < 1e-6
        assert abs(y1 - y_of_f(rect.f1)) < 1e-6 and abs(y0 - y_of_f(rect.f0)) < 1e-6
        assert c.session.selection is sel and sel.ops[0].shape == rect
        lit = cv.overlay_rgba[..., 3] > 0
        assert lit.shape == (H, W)
        np.testing.assert_array_equal(lit, expected_rect_lit(rect, c, W, H, cfg))
        assert lit.any()

    check()
    v.zoom_time(0.5, anchor_t=1.4)
    check()
    v.zoom_freq(0.5, anchor_f=1500.0)
    check()
    editor.resize(1000, 520)
    qtbot.wait(30)
    check()
    v.set_display(freq_scale="linear")
    check()
    v.fit_project()
    check()
    v.set_display(freq_scale="log")
    check()


def test_overlay_matches_processing_cells(qtbot, editor):
    c = editor.controller
    cv = editor.canvas
    rect = RectShape(0.7, 2.3, 350.0, 2600.0)
    c.commit_selection_shape(rect, "replace")
    settle(qtbot, editor)
    W, H = cv.width(), cv.height()
    assert cv.overlay_method in ("raster", "lod")
    lit = cv.overlay_rgba[..., 3] > 0
    np.testing.assert_array_equal(lit, expected_rect_lit(rect, c, W, H, c.session.edit_config))
    np.testing.assert_array_equal(lit, cv.overlay_values > 0.5)


def test_overlay_lod_equals_full_raster_mapping():
    n = int(DUR * SR)
    cfg = STFTConfig(512, 64, "hann")
    sel = Selection.of(RectShape(0.5, 2.0, 300.0, 5000.0)).combine("subtract", EllipseShape(1.2, 1000.0, 0.3, 400.0))
    full = rasterize(sel, cfg, SR, n)
    for kind in ("log", "linear"):
        scale = FrequencyScale(kind, 0.0, SR / 2, SR / 2, 20.0)
        g = ViewGeometry(0.0, DUR, 200, 120, scale)
        vals, method = selection_overlay_values(sel, cfg, SR, n, g)
        assert method == "lod"  # 2250 visible frames > 3 x 200 px
        ref = map_grid_to_view(full.data, full.k0, full.b0, 0.0, DUR, 200, scale, 120, SR, cfg)
        np.testing.assert_allclose(vals, ref, rtol=0, atol=1e-12)
        g2 = ViewGeometry(1.0, 1.5, 400, 120, scale)  # zoomed in: raster path
        vals2, method2 = selection_overlay_values(sel, cfg, SR, n, g2)
        assert method2 == "raster"
        ref2 = map_grid_to_view(full.data, full.k0, full.b0, 1.0, 1.5, 400, scale, 120, SR, cfg)
        np.testing.assert_allclose(vals2, ref2, rtol=0, atol=1e-12)


def test_resize_handle_edits_rect_and_keeps_feather(qtbot, editor):
    c = editor.controller
    cv = editor.canvas
    c.commit_selection_shape(RectShape(1.0, 2.0, 500.0, 4000.0), "replace")
    c.session.set_selection(c.session.selection.with_feather(0.01, 25.0).with_channels((0,)))
    qtbot.wait(10)
    c.tools.set(tool="rect")
    W, H = cv.width(), cv.height()
    t_of_x, x_of_t, _f, y_of_f, _s = independent_mapping(c, W, H)
    xe = x_of_t(2.0)
    ym = 0.5 * (y_of_f(4000.0) + y_of_f(500.0))
    drag(cv, (round(xe), round(ym)), (round(xe) + 60, round(ym)))
    sel = c.session.selection
    assert len(sel.ops) == 1 and sel.ops[0].mode == "replace"
    r = sel.ops[0].shape
    assert r.t0 == 1.0 and r.f0 == 500.0 and r.f1 == 4000.0
    assert abs(r.t1 - t_of_x(round(xe) + 60)) <= (c.view.t1 - c.view.t0) / W
    assert sel.feather_t == 0.01 and sel.feather_f == 25.0 and sel.channels == (0,)


def test_transform_moves_mask_only(qtbot, editor):
    c = editor.controller
    cv = editor.canvas
    audio_before = c.session.active_layer.audio.copy()
    c.commit_selection_shape(RectShape(1.0, 2.0, 500.0, 4000.0), "replace")
    c.tools.set(tool="transform")
    W, H = cv.width(), cv.height()
    t_of_x, _x, f_of_y, _y, _s = independent_mapping(c, W, H)
    drag(cv, (200, 120), (260, 100))
    r = c.session.selection.ops[0].shape
    dt = t_of_x(260) - t_of_x(200)
    df = f_of_y(100) - f_of_y(120)  # Hz, not octaves
    assert abs(r.t0 - (1.0 + dt)) < 1e-9 and abs(r.t1 - (2.0 + dt)) < 1e-9
    assert abs(r.f0 - (500.0 + df)) < 1e-6 and abs(r.f1 - (4000.0 + df)) < 1e-6
    assert np.array_equal(c.session.active_layer.audio, audio_before)


def test_selection_brush_modes(qtbot, editor):
    c = editor.controller
    cv = editor.canvas
    c.tools.set(tool="sel_brush", brush_size_mode="screen", brush_size_px=20.0, brush_hardness=0.7)
    drag(cv, (100, 100), (300, 110), steps=10)
    sel = c.session.selection
    assert len(sel.ops) == 1 and sel.ops[0].mode == "replace"
    b = sel.ops[0].shape
    assert isinstance(b, BrushShape) and len(b.path) >= 3 and b.hardness == 0.7
    # screen mode: radius = 10 px converted at the stroke start
    assert abs(b.rt - 10 * (c.view.t1 - c.view.t0) / cv.width()) < 1e-9
    drag(cv, (100, 150), (300, 150), steps=5)
    assert c.session.selection.ops[-1].mode == "add"
    drag(cv, (150, 150), (200, 150), steps=5, mods=Qt.KeyboardModifier.AltModifier)
    assert c.session.selection.ops[-1].mode == "subtract"


def test_reduce_brush_one_stroke_one_undo(qtbot, editor, monkeypatch):
    c = editor.controller
    cv = editor.canvas
    s = c.session
    s.set_selection(None)
    c.tools.set(tool="reduce_brush", brush_size_mode="spectral", brush_width_s=0.2, brush_height_hz=300.0,
                brush_hardness=0.8, reduce_db=-12.0)
    calls = []
    orig = c.brush_stroke

    def counting(brush, kind):
        calls.append((brush, kind))
        return orig(brush, kind)

    monkeypatch.setattr(c, "brush_stroke", counting)
    n_undo = len(s.history.undo_stack)
    before = s.active_layer.audio.astype(np.float64)
    W, H = cv.width(), cv.height()
    _t, x_of_t, _f, y_of_f, _s = independent_mapping(c, W, H)
    y = round(y_of_f(1000.0))
    drag(cv, (round(x_of_t(1.2)), y), (round(x_of_t(2.0)), y), steps=12)
    assert len(calls) == 1 and calls[0][1] == "reduce"
    brush = calls[0][0]
    assert isinstance(brush, BrushShape) and brush.rt == pytest.approx(0.1) and brush.rf == pytest.approx(150.0)
    qtbot.waitUntil(lambda: not c.busy, timeout=30000)
    assert len(s.history.undo_stack) == n_undo + 1
    after = s.active_layer.audio.astype(np.float64)

    def level(sig, f, a, b):
        tt = np.arange(a, b) / SR
        return 2 * abs(np.mean(sig[a:b] * np.exp(-2j * np.pi * f * tt)))

    a, b = int(1.45 * SR), int(1.75 * SR)
    assert level(after[0], 1000.0, a, b) < 0.4 * level(before[0], 1000.0, a, b)
    assert level(after[0], 440.0, a, b) == pytest.approx(level(before[0], 440.0, a, b), rel=0.02)
    np.testing.assert_array_equal(after[:, : int(0.5 * SR)], before[:, : int(0.5 * SR)])
    settle(qtbot, editor)  # the display picks up the edit without errors


# --------------------------------------------------------------------- navigation
def test_wheel_zoom_and_pan(qtbot, editor):
    c = editor.controller
    cv = editor.canvas
    v = c.view
    W = cv.width()
    t_anchor = v.t0 + 200 / W * (v.t1 - v.t0)
    span0 = v.t1 - v.t0

    def wheel(dy, mods, dx=0):
        pos = QPointF(200, 100)
        ev = QWheelEvent(pos, cv.mapToGlobal(pos), QPoint(0, 0), QPoint(dx, dy), Qt.MouseButton.NoButton, mods,
                         Qt.ScrollPhase.NoScrollPhase, False)
        QApplication.sendEvent(cv, ev)

    wheel(120, Qt.KeyboardModifier.ControlModifier)
    assert v.t1 - v.t0 == pytest.approx(span0 * 0.85)
    assert v.t0 + 200 / W * (v.t1 - v.t0) == pytest.approx(t_anchor)
    t0 = v.t0
    wheel(-120, NO_MOD)
    assert v.t0 > t0
    f_hi0 = v.f_hi
    wheel(0, Qt.KeyboardModifier.ShiftModifier, dx=120)  # macOS: Shift+wheel arrives as horizontal
    assert v.f_hi < f_hi0


def test_drag_anchor_stays_in_audio_units_when_zooming_mid_drag(qtbot, editor):
    c = editor.controller
    cv = editor.canvas
    c.tools.set(tool="rect")
    W, H = cv.width(), cv.height()
    t_of_x, _x, f_of_y, _y, _s = independent_mapping(c, W, H)
    t_press, f_press = t_of_x(100), f_of_y(60)
    QTest.mousePress(cv, LEFT, NO_MOD, QPoint(100, 60))
    QTest.mouseMove(cv, QPoint(200, 100))
    pos = QPointF(200, 100)
    ev = QWheelEvent(pos, cv.mapToGlobal(pos), QPoint(0, 0), QPoint(0, 240), Qt.MouseButton.NoButton,
                     Qt.KeyboardModifier.ControlModifier, Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(cv, ev)  # zoom time while dragging
    QTest.mouseMove(cv, QPoint(300, 180))
    QTest.mouseRelease(cv, LEFT, NO_MOD, QPoint(300, 180))
    r = c.session.selection.ops[0].shape
    t_of_x2, _x2, f_of_y2, _y2, _s2 = independent_mapping(c, W, H)  # zoomed mapping
    assert abs(r.t0 - t_press) <= (c.view.t1 - c.view.t0) / W * 2.5  # press point kept in audio units
    assert abs(r.f1 - f_press) <= abs(f_of_y(59) - f_of_y(61))
    assert abs(r.t1 - t_of_x2(300)) <= (c.view.t1 - c.view.t0) / W


def test_playhead_follow_pages_view(qtbot, editor):
    c = editor.controller
    v = c.view
    v.set_time_range(0.0, 1.0)
    c.play_pause()
    if not c.playback.is_playing:
        pytest.skip("fake playback did not start")
    try:
        c.playback.seek(int(1.5 * SR))
        c.playheadChanged.emit(int(1.5 * SR))
        assert v.t0 == pytest.approx(1.5) and v.t1 - v.t0 == pytest.approx(1.0)
        v.set_display(playhead_follow=False)
        c.playheadChanged.emit(int(2.8 * SR))
        assert v.t0 == pytest.approx(1.5)
    finally:
        c.stop()


def test_zoom_and_hand_tools(qtbot, editor):
    c = editor.controller
    cv = editor.canvas
    v = c.view
    c.tools.set(tool="zoom")
    span0 = v.t1 - v.t0
    QTest.mouseClick(cv, LEFT, NO_MOD, QPoint(200, 100))
    assert v.t1 - v.t0 == pytest.approx(span0 * 0.5)
    QTest.mouseClick(cv, LEFT, Qt.KeyboardModifier.AltModifier, QPoint(200, 100))
    assert v.t1 - v.t0 == pytest.approx(span0)
    drag(cv, (100, 50), (300, 150))
    W = cv.width()
    assert v.t1 - v.t0 == pytest.approx(span0 * 200 / W, rel=0.02)
    c.tools.set(tool="hand")
    t0 = v.t0
    drag(cv, (300, 100), (200, 100))
    assert v.t0 > t0


def test_overview_and_waveform(qtbot, editor):
    c = editor.controller
    audio_before = c.session.active_layer.audio.copy()
    assert editor.envelope.wait_ready()
    assert editor.overview.isVisible() and editor.waveform.isVisible()
    c.view.set_display(show_overview=False, show_waveform=False)
    qtbot.wait(20)
    assert not editor.overview.isVisible() and not editor.waveform.isVisible()
    assert np.array_equal(c.session.active_layer.audio, audio_before)
    c.view.set_display(show_overview=True, show_waveform=True)
    qtbot.wait(20)
    assert editor.overview.isVisible() and editor.waveform.isVisible()
    # waveform envelope = exact min/max of the visible samples, cached per view
    c.view.zoom_time(0.25, anchor_t=1.5)
    qtbot.wait(20)
    env = editor.waveform.envelope_data()
    assert env[0] == "envelope"
    _k, lo, hi, exact = env
    assert exact
    data = c.mix_source().data
    W = editor.waveform.width()
    v = c.view
    a = int(math.floor(v.t0 * SR))
    b = int(math.floor(v.t0 * SR + (v.t1 - v.t0) * SR / W))
    assert hi[0] == pytest.approx(float(data[a:b].max()))
    assert lo[0] == pytest.approx(float(data[a:b].min()))
    n = editor.waveform.compute_count
    editor.waveform.envelope_data()
    assert editor.waveform.compute_count == n
    # overview click centres the view there
    ov = editor.overview
    span = v.t1 - v.t0
    QTest.mouseClick(ov, LEFT, NO_MOD, QPoint(ov.width() // 4, 10))
    centre = 0.5 * (v.t0 + v.t1)
    assert centre == pytest.approx((ov.width() // 4) / ov.width() * DUR, abs=span * 0.02)


def test_waveform_block_lod_keeps_every_peak():
    from spledio.ui.overview import MixEnvelope, block_envelope
    from spledio.ui.waveform_strip import visible_envelope

    rng = np.random.default_rng(5)
    data = (0.1 * rng.standard_normal((SR * 4, 2))).astype(np.float32)  # SYNTHETIC noise fixture
    data[123_457, 1] = 0.9
    data[150_001, 0] = -0.95
    blocks = block_envelope(data, MixEnvelope.BLOCK)
    kind, lo, hi, exact = visible_envelope(data, SR, 0.0, 4.0, 300, blocks)
    assert kind == "envelope" and exact
    assert float(np.nanmax(hi)) == pytest.approx(0.9) and float(np.nanmin(lo)) == pytest.approx(-0.95)
    _k, lo_raw, hi_raw, _e = visible_envelope(data, SR, 0.0, 4.0, 300, None)
    # same peaks, at most one column apart
    assert abs(int(np.nanargmax(hi)) - int(np.nanargmax(hi_raw))) <= 1
    assert abs(int(np.nanargmin(lo)) - int(np.nanargmin(lo_raw))) <= 1


def test_cursor_info_readout(qtbot, editor):
    c = editor.controller
    cv = editor.canvas
    got = []
    editor.cursorInfo.connect(got.append)
    QTest.mouseMove(cv, QPoint(250, 120))
    assert got and got[-1]
    info = got[-1]
    assert " s · " in info and "Hz" in info and ("dB" in info or "silent" in info)
    t_of_x, *_ = independent_mapping(c, cv.width(), cv.height())
    assert info.startswith(f"{t_of_x(250):.4f} s")
    assert cv.cursor_info_at(-5, 10) == ""
