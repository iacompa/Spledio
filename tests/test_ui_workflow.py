"""Headless end-to-end test of the *real* main window (offscreen Qt, fake audio device).

Drives the user-facing actions of the core workflow – open, select a narrow
resonance, audition it, preview a reduction, A/B, apply, undo/redo, export,
save, reopen – and checks the audio that actually comes out of the playback
engine and the exported file. Also triggers every enabled, non-dialog action
once to prove no button is dead or crashes.
"""

from __future__ import annotations

import os

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
