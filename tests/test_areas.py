"""Adjusted areas: re-adjustable, removable, overlapping gain regions (engine level)."""

import math
import time

import numpy as np
import pytest
import soundfile as sf

from spledio.engine import areas as areas_mod
from spledio.engine.areas import step_gain
from spledio.engine.session import EditError, EditorSession
from spledio.selections.geometry import RectShape
from spledio.selections.selection import Selection

SR = 48000


def tone(sig, f, a, b):
    tt = np.arange(a, b) / SR
    return 2 * abs(np.mean(sig[a:b] * np.exp(-2j * np.pi * f * tt)))


def db(x, y):
    return 20 * np.log10(x / y)


@pytest.fixture
def s():
    t = np.arange(4 * SR) / SR
    x = 0.3 * np.sin(2 * np.pi * 440 * t) + 0.2 * np.sin(2 * np.pi * 2500 * t) + 0.1 * np.sin(2 * np.pi * 6000 * t)
    sess = EditorSession.from_array(np.stack([x, 0.8 * x]), SR)
    yield sess
    sess.close()


A = (0.5, 3.5)


def sel(f0, f1):
    return Selection.of(RectShape(A[0], A[1], f0, f1))


def test_step_sequence_reaches_silence_and_back():
    g, seq = 0.0, []
    while not math.isinf(g):
        g = step_gain(g, -1)
        seq.append(g)
    assert seq[:4] == [-1, -2, -3, -4] and seq[-2] == -48 and len(seq) == 49  # 1 dB steps, then silence
    assert step_gain(-math.inf, +1) == -48 and step_gain(24.0, +1) == 24.0 and step_gain(23.5, +1) == 24.0
    # Shift: to the next multiple of 6 dB
    assert step_gain(-4.0, -1, big=True) == -6 and step_gain(-6.0, -1, big=True) == -12
    assert step_gain(-4.0, +1, big=True) == 0 and step_gain(0.0, +1, big=True) == 6
    assert math.isinf(step_gain(-48.0, -1, big=True)) and step_gain(-12.5, +1) == -11.5


def test_adjust_down_to_silence_and_back_up_is_exact(s):
    orig = s.project.layers[0].audio.astype(np.float64).copy()
    a, b = int(1.0 * SR), int(3.0 * SR)
    s.set_selection(sel(2300, 2700))
    s.adjust_area(-1)
    area = s.current_area()
    assert area is not None and area.gain_db == -1
    for _ in range(9):  # Shift steps: -6, -12, ... -48 (8 steps), then silence
        s.adjust_area(-1, big=True)
    assert math.isinf(s.current_area().gain_db)
    x = s.project.layers[0].audio.astype(np.float64)
    assert db(tone(x[0], 2500, a, b) + 1e-12, tone(orig[0], 2500, a, b)) < -60
    assert abs(db(tone(x[0], 440, a, b), tone(orig[0], 440, a, b))) < 0.05
    s.adjust_area(+1)  # back from silence: exact because the component is kept
    x = s.project.layers[0].audio.astype(np.float64)
    assert db(tone(x[0], 2500, a, b), tone(orig[0], 2500, a, b)) == pytest.approx(-48, abs=0.3)
    s.adjust_area(target_db=-6.0)
    x = s.project.layers[0].audio.astype(np.float64)
    assert db(tone(x[0], 2500, a, b), tone(orig[0], 2500, a, b)) == pytest.approx(-6, abs=0.05)
    # outside the processed span nothing changed
    assert np.array_equal(x[:, : int(0.3 * SR)], orig[:, : int(0.3 * SR)])


def test_two_areas_are_independent_and_removal_restores(s):
    orig = s.project.layers[0].audio.astype(np.float64).copy()
    a, b = int(1.0 * SR), int(3.0 * SR)
    s.set_selection(sel(2300, 2700))
    s.adjust_area(target_db=-12)
    s.set_selection(sel(5700, 6300))
    s.adjust_area(target_db=+6)
    assert len(s.areas) == 2 and s.current_area().number == 2
    x = s.project.layers[0].audio.astype(np.float64)
    assert db(tone(x[0], 2500, a, b), tone(orig[0], 2500, a, b)) == pytest.approx(-12, abs=0.1)
    assert db(tone(x[0], 6000, a, b), tone(orig[0], 6000, a, b)) == pytest.approx(6, abs=0.1)
    # pick area 1 by clicking inside it, change it; area 2 is untouched
    hit = s.area_at(2.0, 2500)
    assert hit is not None and hit.number == 1
    s.select_area(hit.id)
    s.adjust_area(+1)
    x = s.project.layers[0].audio.astype(np.float64)
    assert db(tone(x[0], 2500, a, b), tone(orig[0], 2500, a, b)) == pytest.approx(-11, abs=0.1)
    assert db(tone(x[0], 6000, a, b), tone(orig[0], 6000, a, b)) == pytest.approx(6, abs=0.1)
    s.remove_area(hit.id)
    x = s.project.layers[0].audio.astype(np.float64)
    assert abs(db(tone(x[0], 2500, a, b), tone(orig[0], 2500, a, b))) < 0.01
    assert db(tone(x[0], 6000, a, b), tone(orig[0], 6000, a, b)) == pytest.approx(6, abs=0.1)
    s.remove_all_areas()
    assert s.areas == ()
    assert np.max(np.abs(s.project.layers[0].audio - orig)) < 1e-6  # float32 storage rounding only


def test_overlapping_areas_remove_in_any_order(s):
    orig = s.project.layers[0].audio.astype(np.float64).copy()
    s.set_selection(sel(2000, 3000))
    s.adjust_area(target_db=-6)
    s.set_selection(Selection.of(RectShape(1.5, 2.5, 2400, 2600)))
    s.adjust_area(target_db=-12)
    first = s.areas[0].id
    s.remove_area(first)
    s.remove_area(s.areas[0].id)
    assert s.areas == ()
    assert np.max(np.abs(s.project.layers[0].audio - orig)) < 1e-6


def test_rapid_presses_merge_into_one_undo_step_and_undo_is_exact(s):
    orig = s.project.layers[0].audio.copy()
    s.set_selection(sel(2300, 2700))
    for _ in range(4):
        s.adjust_area(-1)
    assert s.current_area().gain_db == -4
    assert len(s.history.undo_stack) == 1
    after = s.project.layers[0].audio.copy()
    s.undo()
    assert np.array_equal(s.project.layers[0].audio, orig) and s.areas == ()
    s.redo()
    assert np.array_equal(s.project.layers[0].audio, after) and s.areas[0].gain_db == -4


def test_presses_after_a_pause_are_separate_steps(s, monkeypatch):
    s.set_selection(sel(2300, 2700))
    s.adjust_area(-1)
    monkeypatch.setattr(areas_mod, "MERGE_WINDOW_S", 0.0)
    import spledio.engine.session as sm

    monkeypatch.setattr(sm, "MERGE_WINDOW_S", 0.0)
    time.sleep(0.01)
    s.adjust_area(-1)
    assert len(s.history.undo_stack) == 2
    s.undo()
    assert s.current_area().gain_db == -1


def test_zero_db_new_area_is_refused_and_locked_layer_protected(s):
    s.set_selection(sel(2300, 2700))
    with pytest.raises(EditError):
        s.adjust_area(target_db=0.0)
    assert s.areas == ()
    s.adjust_area(-1)
    s.set_layer_props(s.project.layers[0].id, locked=True)
    with pytest.raises(EditError):
        s.adjust_area(-1)
    with pytest.raises(EditError):
        s.remove_area()


def test_areas_survive_save_and_reopen(s, tmp_path):
    s.set_selection(sel(2300, 2700))
    s.adjust_area(target_db=-15)
    s.set_selection(sel(5700, 6300))
    s.adjust_area(target_db=-math.inf)
    mix = s.render_full_mix()
    p = str(tmp_path / "areas.spledio")
    s.save(p)
    r = EditorSession.open_project(p)
    try:
        assert [(a.number, a.gain_db) for a in r.areas] == [(1, -15), (2, -math.inf)]
        assert np.array_equal(r.render_full_mix(), mix)
        # still adjustable after reopening: bring area 2 back from silence, then remove area 1
        r.select_area(r.areas[1].id)
        r.adjust_area(target_db=0.0)
        r.remove_area(r.areas[0].id)
        x = r.project.layers[0].audio.astype(np.float64)
        t = np.arange(4 * SR) / SR
        ref = 0.3 * np.sin(2 * np.pi * 440 * t) + 0.2 * np.sin(2 * np.pi * 2500 * t) + 0.1 * np.sin(2 * np.pi * 6000 * t)
        assert np.max(np.abs(x[0] - ref)) < 1e-5
    finally:
        r.close()


def test_adjusting_a_removed_area_by_id_is_refused(s):
    s.set_selection(sel(2300, 2700))
    s.adjust_area(-1)
    aid = s.current_area().id
    s.remove_area(aid)
    with pytest.raises(EditError, match="no longer exists"):
        s.adjust_area(target_db=-6, area_id=aid)
    assert s.areas == ()


def test_time_operation_freezes_areas_and_undo_restores_them(s):
    s.set_selection(sel(2300, 2700))
    s.adjust_area(-1)
    s.set_selection(Selection.of(RectShape(3.8, 3.9, 0, 1e12)))
    res = s.delete_time()
    assert s.areas == () and any("Adjusted areas" in w for w in res.warnings)
    s.undo()
    assert len(s.areas) == 1
