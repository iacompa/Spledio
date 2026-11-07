"""Headless end-to-end test of the *real* main window (offscreen Qt, fake audio device).

Drives the user-facing actions of the core workflow – open, select a narrow
resonance, audition it, preview a reduction, A/B, apply, undo/redo, export,
save, reopen – and checks the audio that actually comes out of the playback
engine and the exported file. Also triggers every enabled, non-dialog action
once to prove no button is dead or crashes.
"""

from __future__ import annotations

import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
import soundfile as sf

pytest.importorskip("pytestqt")

from PySide6.QtWidgets import QDialog, QFileDialog, QInputDialog, QMessageBox  # noqa: E402

from spledio.audio.export import ExportSettings  # noqa: E402
from spledio.playback.engine import FakeBackend  # noqa: E402
from spledio.selections.geometry import RectShape  # noqa: E402

SR = 48000
F_RES = 2370.0


def _fixture(path):
    n = 6 * SR
    t = np.arange(n) / SR
    rng = np.random.default_rng(1)
    env = ((t > 1.5) & (t < 4.5)).astype(float)
    x = 0.05 * rng.normal(size=n) + 0.2 * np.sin(2 * np.pi * 440 * t) + 0.15 * env * np.sin(2 * np.pi * F_RES * t)
    sf.write(path, np.stack([x, 0.9 * x], axis=1).astype(np.float32), SR, subtype="FLOAT")
    return path


def _tone_level(sig, f, a, b):
    tt = np.arange(a, b) / SR
    return 2 * abs(np.mean(sig[a:b] * np.exp(-2j * np.pi * f * tt)))


@pytest.fixture
def no_dialogs(monkeypatch):
    """Make every modal dialog return immediately (as if cancelled / confirmed)."""
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: QMessageBox.Yes))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: QMessageBox.Ok))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: QMessageBox.Ok))
    monkeypatch.setattr(QMessageBox, "about", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: ("", "")))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: ("", "")))
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: ""))
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("", False)))
    monkeypatch.setattr(QInputDialog, "getDouble", staticmethod(lambda *a, **k: (0.0, False)))
    monkeypatch.setattr(QDialog, "exec", lambda self: 0)


@pytest.fixture
def win(qtbot, no_dialogs, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))  # keep QSettings / recovery out of the real home
    from spledio.ui.controller import EditorController
    from spledio.ui.main_window import MainWindow

    backend = FakeBackend()
    w = MainWindow(EditorController(playback_backend=backend))
    w._fake_backend = backend
    qtbot.addWidget(w)
    w.resize(1400, 900)
    w.show()
    errors = []
    w.ctl.error.connect(lambda title, msg: errors.append((title, msg)))
    w._errors = errors
    yield w
    w.ctl.shutdown()


def _idle(qtbot, w, timeout=20000):
    qtbot.waitUntil(lambda: not w.ctl.busy, timeout=timeout)
    qtbot.wait(30)


def _open(qtbot, w, path):
    w.open_path(path)
    qtbot.waitUntil(lambda: w.ctl.session is not None, timeout=20000)
    _idle(qtbot, w)


def _trigger(w, aid):
    w.refresh_actions()
    act = w.actions[aid]
    assert act.isEnabled(), f"{aid} disabled: {act.statusTip()}"
    act.trigger()


def _pull(w, seconds):
    """Pull audio from the fake device like the sound card would."""
    out = []
    n = int(seconds * SR)
    while n > 0:
        b = w._fake_backend.pull(min(n, 2048))
        out.append(np.asarray(b))
        n -= len(b)
    return np.concatenate(out, axis=0)


def test_resonance_workflow_through_main_window(qtbot, win, tmp_path):
    w = win
    src = _fixture(str(tmp_path / "res.wav"))
    _open(qtbot, w, src)
    s = w.ctl.session
    orig = s.project.layers[0].audio.astype(np.float64).copy()
    a, b = int(1.6 * SR), int(4.4 * SR)
    lvl0 = _tone_level(orig[0], F_RES, a, b)

    # select a narrow box around the resonance (as the canvas / resonance panel does)
    w.ctl.commit_selection_shape(RectShape(1.5, 4.5, F_RES - 60, F_RES + 60))
    qtbot.wait(20)
    assert s.selection is not None

    # audition: selected only -> resonance present, 440 Hz mostly gone
    _trigger(w, "audition.selected")
    _idle(qtbot, w)
    assert w.ctl.playback.is_playing
    w.ctl.playback.seek(int(2.0 * SR))
    heard = _pull(w, 1.0)[:, 0]
    assert _tone_level(heard, F_RES, 0, len(heard)) > 0.5 * 0.15
    assert _tone_level(heard, 440, 0, len(heard)) < 0.01
    _trigger(w, "transport.stop")

    # preview -12 dB, bypass toggles back to original, apply commits exactly what was previewed
    w.ctl.tools.set(gain_db=-12.0)
    _trigger(w, "module.preview")
    _idle(qtbot, w)
    assert s.preview is not None
    previewed = s.preview
    _trigger(w, "module.bypass")
    _trigger(w, "audition.ab")
    _trigger(w, "module.apply")
    _idle(qtbot, w)
    assert s.preview is None
    edited = s.project.layers[0].audio.astype(np.float64)
    lvl1 = _tone_level(edited[0], F_RES, a, b)
    assert 20 * np.log10(lvl1 / lvl0) == pytest.approx(-12.0, abs=0.3)
    # time is not cut and the 440 Hz content is untouched
    assert edited.shape == orig.shape
    assert abs(20 * np.log10(_tone_level(edited[0], 440, a, b) / _tone_level(orig[0], 440, a, b))) < 0.05
    # outside the processed span samples are bit-identical
    assert np.array_equal(edited[:, : int(1.0 * SR)], orig[:, : int(1.0 * SR)])
    assert previewed is not None

    # undo / redo through the actions
    _trigger(w, "edit.undo")
    _idle(qtbot, w)
    assert np.array_equal(s.project.layers[0].audio.astype(np.float64), orig)
    _trigger(w, "edit.redo")
    _idle(qtbot, w)
    assert np.array_equal(s.project.layers[0].audio.astype(np.float64), edited)

    # export: the file equals the playback mix
    out = str(tmp_path / "out.wav")
    s.export(out, ExportSettings(format="WAV", subtype="FLOAT"))
    data, sr = sf.read(out, dtype="float64", always_2d=True)
    assert sr == SR
    assert np.allclose(data.T, s.render_full_mix(), atol=1e-7)

    # save + reopen through the window
    proj = str(tmp_path / "p.spledio")
    s.view_state = w.ctl.view.to_dict()
    s.save(proj)
    w.open_project(proj)
    qtbot.waitUntil(lambda: w.ctl.session is not s, timeout=20000)
    _idle(qtbot, w)
    assert np.array_equal(w.ctl.session.project.layers[0].audio.astype(np.float64), edited)
    assert not w._errors, w._errors


SKIP_PREFIXES = ("file.", "help.", "layer.export")
SKIP = {"edit.delete_time", "edit.crop_time", "transport.play_pause"}


def test_every_enabled_action_runs_without_error(qtbot, win, tmp_path):
    w = win
    _open(qtbot, w, _fixture(str(tmp_path / "a.wav")))
    w.ctl.commit_selection_shape(RectShape(1.0, 2.0, 1000, 3000))
    ran, disabled = [], []
    for aid, spec in list(w.actions.specs.items()):
        if aid.startswith(SKIP_PREFIXES) or aid in SKIP:
            continue
        w.refresh_actions()
        act = w.actions[aid]
        if not act.isEnabled():
            disabled.append(aid)
            continue
        if w.ctl.session is not None and w.ctl.session.selection is None:
            w.ctl.commit_selection_shape(RectShape(1.0, 2.0, 1000, 3000))
        act.trigger()
        _idle(qtbot, w)
        ran.append(aid)
    assert len(ran) > 40, (ran, disabled)
    assert not w._errors, w._errors
    # disabled actions must explain why
    w.refresh_actions()
    for aid in disabled:
        if aid in w.actions.actions and not w.actions[aid].isEnabled():
            assert "unavailable" in w.actions[aid].statusTip().lower(), aid


def test_actions_disabled_without_project_explain_reason(qtbot, win):
    w = win
    w.refresh_actions()
    for aid in ("edit.apply_gain", "edit.mute", "audition.selected", "file.export", "edit.undo"):
        act = w.actions[aid]
        assert not act.isEnabled(), aid
        assert "unavailable" in act.statusTip().lower(), aid


def test_smoke_test_mode_passes_on_a_real_window(qtbot, win, tmp_path):
    """``spledio --smoke-test FILE`` (used to verify packaged builds) runs all its checks."""
    from PySide6.QtWidgets import QApplication

    from spledio import smoke_test

    done = {}
    smoke_test.run(QApplication.instance(), win, _fixture(str(tmp_path / "s.wav")),
                   on_done=lambda code, results: done.update(code=code, results=results), play_silence=False)
    qtbot.waitUntil(lambda: "code" in done, timeout=60000)
    assert done["code"] == 0, [r for r in done["results"] if not r[1]]
    names = [r[0] for r in done["results"]]
    assert "all dock panels loaded" in names and "WAV export equals the playback mix" in names
    assert len(names) == 8



def test_smoke_test_reports_a_refused_file_at_once(qtbot, win, tmp_path, monkeypatch):
    """A file the app cannot open fails the smoke test with the app's own message, without waiting
    for the timeout behind the error box."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from spledio import smoke_test

    boxes = []

    def critical(parent, title, text, *a, **k):  # like the real box: stays up until something closes it
        box = QMessageBox(QMessageBox.Critical, title, text, QMessageBox.Ok, parent)
        box.setWindowModality(Qt.ApplicationModal)
        box.show()
        boxes.append(box)
        return QMessageBox.Ok

    monkeypatch.setattr(QMessageBox, "critical", staticmethod(critical))

    bad = tmp_path / "not-audio.wav"
    bad.write_bytes(b"this is not audio" * 100)
    done = {}
    t0 = time.monotonic()
    smoke_test.run(QApplication.instance(), win, str(bad),
                   on_done=lambda code, results: done.update(code=code, results=results), play_silence=False)
    qtbot.waitUntil(lambda: "code" in done, timeout=30000)
    assert time.monotonic() - t0 < 20
    assert done["code"] == 1
    name, ok, detail = done["results"][-1]
    assert name == "open audio file" and not ok
    assert detail.startswith("refused: Cannot import 'not-audio.wav'")
    assert len(boxes) == 1 and not boxes[0].isVisible()  # the error box was closed


def test_mp3_edit_save_reopen_and_time_readout(qtbot, win, tmp_path):
    """An MP3 opens (the website and manual promise it), the time readout shows the new file's
    length right away, and a saved project reopens with exactly the same audio."""
    from spledio.ui.main_window import AUDIO_FILTER

    for ext in ("*.mp3", "*.ogg", "*.opus", "*.wav", "*.flac", "*.aiff"):  # File ▸ Open lists them
        assert ext in AUDIO_FILTER.split(";;")[0], ext
    w = win
    _open(qtbot, w, _fixture(str(tmp_path / "first.wav")))  # 6 s
    assert w.time_label.text() == "0:00.000 / 0:06.000"
    t = np.arange(9 * 44100) / 44100
    x = 0.3 * np.sin(2 * np.pi * 330 * t) + 0.1 * np.sin(2 * np.pi * 2500 * t)
    mp3 = str(tmp_path / "song.mp3")
    sf.write(mp3, np.stack([x, x], axis=1), 44100, format="MP3", subtype="MPEG_LAYER_III")
    _open(qtbot, w, mp3)
    s = w.ctl.session
    assert s.project.sample_rate == 44100 and s.project.channels == 2
    assert w.time_label.text().endswith("/ 0:09.000")  # not the previous file's 0:06.000
    w.ctl.commit_selection_shape(RectShape(2.0, 7.0, 2300, 2700))
    w.ctl.adjust_area(-1, True)  # Shift+Down: -6 dB
    _idle(qtbot, w)
    edited = s.project.layers[0].audio.astype(np.float64).copy()
    proj = str(tmp_path / "song.spledio")
    s.save(proj)
    w.open_project(proj)
    qtbot.waitUntil(lambda: w.ctl.session is not s, timeout=20000)
    _idle(qtbot, w)
    assert np.array_equal(w.ctl.session.project.layers[0].audio.astype(np.float64), edited)
    assert [a.gain_db for a in w.ctl.session.areas] == [-6.0]
    assert not w._errors, w._errors


def test_no_leftover_panel_tab_bars_over_the_panels(qtbot, win, tmp_path):
    """Qt 6 leaves the tab bars of earlier dock layouts on screen (after tabifyDockWidget and every
    restoreState): a loose "Resonance Workflow" tab drawn over the Reduce panel's buttons. Only the
    bars Qt uses may stay visible, right under their panel, and the panels keep their width. Checked
    at first show, after Focus mode on/off, and in a second window that restores the saved layout
    (every launch after the first)."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QTabBar

    from spledio.ui.controller import EditorController
    from spledio.ui.main_window import MainWindow

    def check(w):
        def bars():
            return [tb for tb in w.findChildren(QTabBar, options=Qt.FindChildOption.FindDirectChildrenOnly)
                    if tb.isVisible()]

        qtbot.waitUntil(lambda: len(bars()) == 2, timeout=5000)
        qtbot.wait(50)
        assert len(bars()) == 2
        for tb in bars():
            tabs = [tb.tabText(i) for i in range(tb.count())]
            under = [d for d in w.docks.values() if d.isVisible() and d.windowTitle() in tabs
                     and abs(tb.x() - d.x()) <= 3 and abs(tb.width() - d.width()) <= 3
                     and 0 <= tb.y() - d.geometry().bottom() <= 6]
            assert under, (tabs, tb.geometry(), [(d.windowTitle(), d.geometry()) for d in w.docks.values()])
        assert w.docks["layers"].width() >= 250  # the cleanup must not squeeze the panels

    check(win)
    # a leftover the way Qt leaves one (seen in the real app with the real audio device; in this test
    # setup Qt happens to clear its own leftovers): the cleanup hides it and keeps the real bars
    stray = QTabBar(win)
    for t in ("Reduce Selected Sound", "Adjusted Areas", "Resonance Workflow"):
        stray.addTab(t)
    stray.setGeometry(842, 238, 518, 31)
    stray.show()
    assert stray.isVisible()
    win._hide_stale_tab_bars()
    qtbot.wait(20)
    assert not stray.isVisible()
    check(win)

    win.actions["view.focus"].trigger()  # hides the panels (saveState)
    qtbot.wait(50)
    win.actions["view.focus"].trigger()  # back (restoreState)
    check(win)

    win.close()  # saves the layout; the next window restores it, as the next launch does
    w2 = MainWindow(EditorController(playback_backend=FakeBackend()))
    qtbot.addWidget(w2)
    w2.resize(1400, 900)
    w2.show()
    try:
        check(w2)
    finally:
        w2.ctl.shutdown()


def test_theme_is_always_dark(qtbot, monkeypatch):
    """No light theme: the palette is dark and the system is asked for its dark appearance too,
    so the title bar, menus and Open/Save panels stay dark on a Mac set to light mode. (The
    offscreen test platform ignores that request; on macOS the app's NSAppearance becomes
    DarkAqua, checked in a real window on 2026-11-07.)"""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPalette, QStyleHints
    from PySide6.QtWidgets import QApplication

    from spledio.ui import theme

    app = QApplication.instance()
    asked = []
    monkeypatch.setattr(QStyleHints, "setColorScheme", lambda self, scheme: asked.append(scheme))
    saved = (app.palette(), app.styleSheet(), app.style().name(), app.font())
    try:
        theme.apply_theme(app)
        assert asked == [Qt.ColorScheme.Dark]
        for role in (QPalette.Window, QPalette.Base, QPalette.Button):
            assert app.palette().color(role).lightness() < 60, role
        assert app.palette().color(QPalette.WindowText).lightness() > 180
        assert not [n for n in dir(theme) if "light" in n.lower()]  # no light variant to switch to
    finally:
        pal, sheet, style, font = saved
        app.setStyleSheet(sheet)
        app.setStyle(style)
        app.setPalette(pal)
        app.setFont(font)


@pytest.mark.parametrize("channels", [3, 6])
def test_file_with_more_channels_than_the_output_plays_folded_to_stereo(qtbot, no_dialogs, monkeypatch, tmp_path,
                                                                        channels):
    """A 3- or 6-channel file on a stereo output (built-in speakers, headphones) could not be played
    at all: the stream was opened with one output per file channel and the device refused it
    ("Invalid number of channels"). Monitoring now folds such files down to the device's two
    outputs; the project audio and exports keep every channel."""
    monkeypatch.setenv("HOME", str(tmp_path))
    from spledio.ui.controller import EditorController
    from spledio.ui.main_window import MainWindow

    backend = FakeBackend(max_output_channels=2)
    w = MainWindow(EditorController(playback_backend=backend))
    qtbot.addWidget(w)
    w.show()
    n = SR
    t = np.arange(n) / SR
    tones = [0.1 * (c + 1) * np.sin(2 * np.pi * (300 + 200 * c) * t) for c in range(channels)]
    path = str(tmp_path / "multi.wav")
    sf.write(path, np.stack(tones, axis=1).astype(np.float32), SR, subtype="FLOAT")
    w.open_path(path)
    qtbot.waitUntil(lambda: w.ctl.session is not None and not w.ctl.busy, timeout=20000)
    assert w.ctl.session.project.channels == channels
    w.ctl.play_pause()
    assert w.ctl.playback.is_playing, w.ctl.playback.state().error
    assert backend.channels == 2
    out = backend.pull(n)
    assert np.all(np.isfinite(out))
    # every file channel is heard on at least one side, and nothing exceeds the loudest input
    for c in range(channels):
        f = 300 + 200 * c
        lv = [_tone_level(out[:, side], f, 0, n) for side in (0, 1)]
        assert max(lv) > 0.02 * (c + 1), (c, lv)
    assert np.max(np.abs(out)) <= 0.1 * channels + 1e-6
    w.ctl.stop()
    # the project keeps all channels
    exp = str(tmp_path / "out.wav")
    w.ctl.session.export(exp, ExportSettings(format="WAV", subtype="FLOAT"))
    assert sf.info(exp).channels == channels
    w.ctl.shutdown()


@pytest.mark.parametrize("action", ["apply_gain", "preview", "mute", "audition", "area"])
def test_channel_scope_is_applied_to_the_selection_on_the_gui_thread(qtbot, win, tmp_path, monkeypatch, action):
    """When the Channels scope differs from the selection's channels, the edit narrows the
    selection first. That happened inside the worker job, so the selection (and the UI listening
    to it) was changed from a background thread while the GUI could be reading it."""
    from spledio.selections.selection import Selection

    w = win
    _open(qtbot, w, _fixture(str(tmp_path / "a.wav")))
    s = w.ctl.session
    w.ctl.set_channel_scope((0,))
    s.set_selection(Selection.of(RectShape(1.0, 2.0, 2300, 2700)))  # set directly (e.g. a loaded project): all channels
    on_gui = []
    real = s._set_selection_raw

    def spy(sel):
        on_gui.append(threading.current_thread() is threading.main_thread())
        real(sel)

    monkeypatch.setattr(s, "_set_selection_raw", spy)
    {
        "apply_gain": lambda: w.ctl.apply_gain(-6.0),
        "preview": lambda: w.ctl.preview_gain(-6.0, then_play=False),
        "mute": w.ctl.mute_selection,
        "audition": lambda: w.ctl.audition_mode("selected"),
        "area": lambda: w.ctl.adjust_area(-1),
    }[action]()
    _idle(qtbot, w)
    assert on_gui and all(on_gui), on_gui
    assert s.selection is None or s.selection.channels == (0,)
    assert not w._errors

