"""Adjusted areas through the real main window: arrow / +/− keys on the spectrogram, click an
area to pick it again, Delete removes its adjustment, Highlight toggle, Areas panel."""

from __future__ import annotations

import math
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
import soundfile as sf
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from spledio.playback.engine import FakeBackend
from spledio.selections.geometry import RectShape

SR = 48000


def _fixture(path):
    t = np.arange(4 * SR) / SR
    x = 0.3 * np.sin(2 * np.pi * 440 * t) + 0.2 * np.sin(2 * np.pi * 2500 * t) + 0.1 * np.sin(2 * np.pi * 6000 * t)
    sf.write(path, np.stack([x, 0.8 * x], axis=1).astype(np.float32), SR, subtype="FLOAT")
    return path


def _lvl(sig, f, a, b):
    tt = np.arange(a, b) / SR
    return 2 * abs(np.mean(sig[a:b] * np.exp(-2j * np.pi * f * tt)))


@pytest.fixture
def win(qtbot, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    from PySide6.QtWidgets import QMessageBox

    # closing a window with unsaved edits asks first: answer "Discard" so teardown never blocks
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Discard))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: QMessageBox.Ok))
    from spledio.ui.controller import EditorController
    from spledio.ui.main_window import MainWindow

    w = MainWindow(EditorController(playback_backend=FakeBackend()))
    qtbot.addWidget(w)
    w.resize(1400, 900)
    w.show()
    w.open_path(_fixture(str(tmp_path / "a.wav")))
    qtbot.waitUntil(lambda: w.ctl.session is not None and not w.ctl.busy, timeout=20000)
    w.ctl.view.set_display(freq_scale="linear")
    qtbot.wait(50)
    yield w
    w.ctl.shutdown()


def _idle(qtbot, w):
    qtbot.waitUntil(lambda: not w.ctl.busy, timeout=20000)
    qtbot.wait(20)


def _keys(qtbot, w, key, n=1, mods=Qt.KeyboardModifier.NoModifier):
    cv = w.editor.canvas
    cv.setFocus()
    qtbot.waitUntil(cv.hasFocus, timeout=2000)
    for _ in range(n):
        QTest.keyClick(cv, key, mods)
        qtbot.wait(5)
    _idle(qtbot, w)
    qtbot.wait(50)
    _idle(qtbot, w)


def test_arrow_keys_adjust_areas_click_picks_delete_removes(qtbot, win):
    w = win
    s = w.ctl.session
    orig = s.project.layers[0].audio.astype(np.float64).copy()
    a, b = int(1.0 * SR), int(3.0 * SR)

    w.ctl.commit_selection_shape(RectShape(0.5, 3.5, 2300, 2700))
    _keys(qtbot, w, Qt.Key.Key_Down, 4)  # 1 dB per press; presses during a render are queued, none dropped
    assert len(s.areas) == 1 and s.areas[0].gain_db == -4
    x = s.project.layers[0].audio.astype(np.float64)
    assert 20 * np.log10(_lvl(x[0], 2500, a, b) / _lvl(orig[0], 2500, a, b)) == pytest.approx(-4, abs=0.2)
    assert len(s.history.undo_stack) == 1  # rapid presses = one undo step

    _keys(qtbot, w, Qt.Key.Key_Minus)  # minus works like down
    assert s.areas[0].gain_db == -5
    _keys(qtbot, w, Qt.Key.Key_Down, 2, Qt.KeyboardModifier.ShiftModifier)  # Shift: 6 dB steps
    assert s.areas[0].gain_db == -12

    # a second area, made louder with +
    w.ctl.commit_selection_shape(RectShape(0.5, 3.5, 5700, 6300))
    _keys(qtbot, w, Qt.Key.Key_Plus, 3)
    assert len(s.areas) == 2 and s.areas[1].gain_db == 3
    assert s.current_area().number == 2

    # click inside area 1 picks it; Delete removes only its adjustment
    cv = w.editor.canvas
    g = cv.view_geometry()
    px, py = int(g.x_of_t(2.0)), int(g.y_of_f(2500))
    QTest.mouseClick(cv, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(px, py))
    qtbot.wait(20)
    assert s.current_area() is not None and s.current_area().number == 1
    _keys(qtbot, w, Qt.Key.Key_Delete)
    assert [ar.number for ar in s.areas] == [2]
    x = s.project.layers[0].audio.astype(np.float64)
    assert abs(20 * np.log10(_lvl(x[0], 2500, a, b) / _lvl(orig[0], 2500, a, b))) < 0.01
    assert 20 * np.log10(_lvl(x[0], 6000, a, b) / _lvl(orig[0], 6000, a, b)) == pytest.approx(3, abs=0.2)

    # 0 silences the picked area; Up brings it back
    w.ctl.select_area(s.areas[0].id)
    _keys(qtbot, w, Qt.Key.Key_0)
    assert math.isinf(s.areas[0].gain_db)
    _keys(qtbot, w, Qt.Key.Key_Up)
    assert s.areas[0].gain_db == -48

    # undo everything back to the original audio
    while s.history.undo_stack:
        w.actions["edit.undo"].trigger()
        _idle(qtbot, w)
    assert np.array_equal(s.project.layers[0].audio.astype(np.float64), orig)
    assert s.areas == ()


def test_highlight_toggle_panel_and_list_keys(qtbot, win):
    w = win
    s = w.ctl.session
    w.ctl.commit_selection_shape(RectShape(0.5, 3.5, 2300, 2700))
    _keys(qtbot, w, Qt.Key.Key_Down)
    w.ctl.commit_selection_shape(RectShape(1.0, 2.0, 5700, 6300))
    _keys(qtbot, w, Qt.Key.Key_Down, 2)
    panel = w.docks["areas"].widget().widget()
    assert panel.area_ids() == [ar.id for ar in s.areas]

    # Highlight is on by default and paints the areas; the toggle hides them (display only)
    assert w.ctl.view.show_areas and w.actions["view.show_areas"].isChecked() and panel.highlight.isChecked()
    w.editor.canvas.grab()  # paints without error
    audio = s.project.layers[0].audio.copy()
    w.actions["view.show_areas"].trigger()
    assert not w.ctl.view.show_areas and not panel.highlight.isChecked()
    assert np.array_equal(s.project.layers[0].audio, audio)
    panel.highlight.setChecked(True)
    assert w.ctl.view.show_areas and w.actions["view.show_areas"].isChecked()

    # typing a level in the panel
    row = panel.row_widget(s.areas[0].id)
    row.spin.setValue(-20.0)
    _idle(qtbot, w)
    assert s.areas[0].gain_db == -20

    # arrow keys inside a list widget navigate the list, they do not change any area
    hist = w.docks["history"].widget().widget()
    w.docks["history"].raise_()
    hist.list.setFocus()
    qtbot.waitUntil(hist.list.hasFocus, timeout=2000)
    before = [ar.gain_db for ar in s.areas]
    QTest.keyClick(hist.list, Qt.Key.Key_Up)
    QTest.keyClick(hist.list, Qt.Key.Key_Down)
    _idle(qtbot, w)
    assert [ar.gain_db for ar in s.areas] == before

    # ] / [ step through areas
    _keys(qtbot, w, Qt.Key.Key_BracketRight)
    first = s.current_area()
    _keys(qtbot, w, Qt.Key.Key_BracketRight)
    assert s.current_area() is not None and s.current_area().id != first.id


def test_level_bar_next_to_selection_buttons_and_slider(qtbot, win):
    w = win
    s = w.ctl.session
    cv = w.editor.canvas
    bar = cv.level_bar
    s.clear_selection()
    qtbot.wait(30)
    assert not bar.isVisible()  # nothing selected: no bar
    w.ctl.commit_selection_shape(RectShape(0.5, 3.5, 2300, 2700))
    qtbot.waitUntil(bar.isVisible, timeout=3000)
    assert bar.title.text() == "Selection" and bar.value.text() == "0 dB" and not bar.remove.isVisible()
    # placed next to the selection box (just above it, right edges aligned) and fully inside the canvas
    g = cv.view_geometry()
    assert abs(bar.geometry().right() - g.x_of_t(3.5)) < 3
    assert bar.geometry().bottom() <= g.y_of_f(2700) and cv.rect().contains(bar.geometry())
    busy_signals = []
    w.ctl.busyChanged.connect(lambda b, _l: busy_signals.append(b))

    bar.minus.click()
    _idle(qtbot, w)
    assert len(s.areas) == 1 and s.areas[0].gain_db == -1
    qtbot.waitUntil(lambda: bar.title.text() == "Area 1" and bar.value.text() == "-1 dB", timeout=3000)
    assert bar.remove.isVisible()
    bar.plus.click()
    bar.plus.click()
    _idle(qtbot, w)
    qtbot.wait(50)
    _idle(qtbot, w)
    assert s.areas[0].gain_db == 1
    bar.slider.setValue(-20)  # dragging / clicking the slider sets the level directly
    _idle(qtbot, w)
    assert s.areas[0].gain_db == -20
    bar.silence.click()
    _idle(qtbot, w)
    assert math.isinf(s.areas[0].gain_db) and bar.value.text() == "Silenced"
    assert busy_signals == []  # quick level changes don't flash the toolbar / panels as busy
    assert w.actions["edit.undo"].isEnabled()  # ...and nothing is left greyed out afterwards
    assert w.actions["file.export"].isEnabled() and w.actions["audition.selected"].isEnabled()
    # the bar never takes keyboard focus away from the spectrogram
    cv.setFocus()
    bar.minus.click()
    assert cv.hasFocus() or not w.isActiveWindow()
    _idle(qtbot, w)
    bar.remove.click()
    _idle(qtbot, w)
    assert s.areas == ()


def test_zoom_moved_to_ctrl_and_plus_minus_need_a_selection(qtbot, win):
    w = win
    s = w.ctl.session
    s.clear_selection()
    w.refresh_actions()
    assert not w.actions["area.louder"].isEnabled()
    assert "Select something" in w.actions["area.louder"].statusTip()
    sc = [k.toString() for k in w.actions["view.zoom_in"].shortcuts()]
    assert "+" not in sc and "=" not in sc
