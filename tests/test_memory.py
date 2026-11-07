"""Memory use of long-file edits: the same samples with fewer and smaller copies."""

import math
import time

import numpy as np
import pytest

from spledio.commands.history import PatchStore
from spledio.dsp.spectral import add_scaled_in_place, apply_gain_to_region, selected_component
from spledio.dsp.stft import DEFAULT_EDIT_CONFIG, STFTConfig
from spledio.engine.session import EditorSession
from spledio.selections.geometry import INF_F, RectShape
from spledio.selections.selection import Selection

SR = 22050


@pytest.fixture
def s():
    rng = np.random.default_rng(5)
    sess = EditorSession.from_array(0.25 * rng.standard_normal((2, 20 * SR)), SR)
    yield sess
    sess.close()


def _sel(t0, t1, f0, f1, ch=None, **kw):
    return Selection.of(RectShape(t0, t1, f0, f1), **kw).with_channels(ch)


@pytest.mark.parametrize("sel", [
    _sel(1.0, 13.0, 300.0, 5000.0, feather_t=0.05, feather_f=80.0),
    _sel(0.0, 20.0, 0.0, 9000.0),
    _sel(18.5, 20.0, 100.0, 900.0, ch=(1,)),
    _sel(2.0, 9.0, 0.0, INF_F, feather_t=0.1),  # sample-domain time path
])
@pytest.mark.parametrize("cfg", [DEFAULT_EDIT_CONFIG, STFTConfig(1024, 256, "hann")])
def test_float32_component_is_the_float64_component_rounded_once(sel, cfg):
    x = (0.3 * np.random.default_rng(1).standard_normal((2, 20 * SR))).astype(np.float32)
    c64 = selected_component(x, sel, cfg, SR)
    for chunk in (5, 384):
        c32 = selected_component(x, sel, cfg, SR, dtype=np.float32, chunk_frames=chunk)
        assert c32.data.dtype == np.float32 and (c32.start, c32.method, c32.channels) == (c64.start, c64.method, c64.channels)
        assert np.array_equal(c32.data, c64.data.astype(np.float32))


@pytest.mark.parametrize("dtype", [np.float32, np.float64])
def test_in_place_gain_equals_the_whole_region_float64_render(dtype):
    rng = np.random.default_rng(2)
    x = rng.standard_normal((2, 300_000)).astype(dtype)
    sel = _sel(1.0, 12.0, 200.0, 4000.0)
    comp = selected_component(x, sel, DEFAULT_EDIT_CONFIG, SR)
    for g in (-6.0, -math.inf, 7.5):
        want = x.copy()
        want[:, comp.start : comp.end] = apply_gain_to_region(x, comp, g)
        got = x.copy()
        add_scaled_in_place(got, comp.start, (0, 1), comp.data, (0.0 if math.isinf(g) else 10 ** (g / 20)) - 1.0)
        assert np.array_equal(got, want)


def test_undo_store_keeps_one_copy_per_edit_and_restores_exactly(s):
    layer = s.project.layers[0]
    orig = layer.audio.copy()
    s.set_selection(_sel(2.0, 10.0, 300.0, 3000.0))
    s.adjust_area(target_db=-6.0)
    a = s.areas[0]
    span = layer.audio[:, a.start : a.end].nbytes
    assert s.history.store.ram_bytes == 2 * span  # the component + the samples from before (no "after" copy)
    edited = layer.audio.copy()
    s.set_selection(_sel(5.0, 15.0, 100.0, 8000.0, ch=(1,)))
    s.apply_gain(4.0)
    p = s.history.undo_stack[-1].patches[0]
    assert s.history.store.get(p.held).shape == (1, p.length)  # only the edited channel is kept
    after = layer.audio.copy()
    s.undo()
    assert np.array_equal(layer.audio, edited)
    s.redo()
    assert np.array_equal(layer.audio, after)
    s.undo(), s.undo()
    assert np.array_equal(layer.audio, orig) and not s.areas
    s.redo(), s.redo()
    assert np.array_equal(layer.audio, after)


def test_merged_area_steps_add_no_undo_memory(s):
    s.set_selection(_sel(2.0, 10.0, 300.0, 3000.0))
    s.adjust_area(target_db=-6.0)
    before = s.history.store.ram_bytes
    now = time.monotonic()
    for _ in range(4):
        s.adjust_area(direction=-1, requested_at=now)
    assert len(s.history.undo_stack) == 1 and s.history.store.ram_bytes == before
    assert s.areas[0].gain_db == -10.0


def test_component_of_an_undone_area_is_freed_once_it_cannot_come_back(s):
    s.set_selection(_sel(2.0, 10.0, 300.0, 3000.0))
    s.adjust_area(target_db=-6.0)
    h = s.areas[0].component
    s.undo()  # still on the redo list: kept
    assert s.history.store.get(h) is not None
    s.set_selection(_sel(12.0, 14.0, 300.0, 3000.0))
    s.apply_gain(-3.0)  # drops the redo list
    with pytest.raises(KeyError):
        s.history.store.get(h)


def test_difference_of_an_older_edit_is_exact_after_later_edits_over_it(s):
    layer = s.project.layers[0]
    s.set_selection(_sel(2.0, 12.0, 300.0, 5000.0))
    pre = layer.audio.astype(np.float64)
    s.apply_gain(-5.0)
    want = pre - layer.audio  # what that edit removed
    s.set_selection(_sel(4.0, 8.0, 1000.0, 3000.0, ch=(0,)))
    s.adjust_area(target_db=-12.0)  # area steps do not count as "the last edit" for the difference
    s.set_selection(_sel(6.0, 14.0, 100.0, 9000.0))
    s.adjust_area(target_db=3.0)
    buf = s.difference_buffer()
    assert np.array_equal(buf.data, want[:, buf.start : buf.end].astype(np.float32))


def test_patch_store_spills_and_swaps_back_exactly(tmp_path):
    rng = np.random.default_rng(9)
    sess = EditorSession.from_array(0.2 * rng.standard_normal((2, 10 * SR)), SR)
    sess.history.store = PatchStore(ram_budget_bytes=1, directory=str(tmp_path))  # everything but the newest on disk
    states = [sess.project.layers[0].audio.copy()]
    for i, (t0, t1) in enumerate([(1.0, 6.0), (3.0, 9.0), (0.5, 2.0)]):
        sess.set_selection(_sel(t0, t1, 200.0 + 100 * i, 4000.0))
        sess.adjust_area(target_db=-4.0 - i)
        states.append(sess.project.layers[0].audio.copy())
    assert sess.history.store.disk_count > 0
    for want in reversed(states[:-1]):
        sess.undo()
        assert np.array_equal(sess.project.layers[0].audio, want)
    for want in states[1:]:
        sess.redo()
        assert np.array_equal(sess.project.layers[0].audio, want)
    sess.close()
