"""Tests for spledio.playback (sources + engine).  No audio hardware needed except the
opt-in smoke test at the bottom (SPLEDIO_AUDIO_TEST=1)."""

from __future__ import annotations

import math
import os
import sys
import threading
import time
import tracemalloc
import warnings

import numpy as np
import pytest

from spledio.playback import (
    DEFAULT_SCRATCH_FRAMES,
    MAX_VOLUME,
    ArraySource,
    FakeBackend,
    OverlaySource,
    PlaybackBackend,
    PlaybackEngine,
    PlaybackSource,
    SilenceSource,
    SoundDeviceBackend,
)

SR = 48000

with warnings.catch_warnings():  # marker is not registered in pyproject; keep output clean
    warnings.simplefilter("ignore", pytest.PytestUnknownMarkWarning)
    audio_device = pytest.mark.audio_device


def _audio(ch=2, n=5000, seed=0):
    return np.random.default_rng(seed).uniform(-0.5, 0.5, (ch, n))  # float64 on purpose


def _exp(audio):
    """What the engine should emit: (frames, channels) float32."""
    return np.ascontiguousarray(np.asarray(audio).T.astype(np.float32))


def _engine(**kw):
    backend = FakeBackend()  # realtime=False: no stall watchdog, pull at leisure
    return PlaybackEngine(backend, **kw), backend


def _pull_many(backend, sizes):
    return np.concatenate([backend.pull(s) for s in sizes], axis=0)


def _dc(value, ch=2, n=SR):
    return np.full((ch, n), value, dtype=np.float64)


# =========================================================================== sources


def test_array_source_layout_copy_and_immutability():
    audio = _audio(2, 100)
    src = ArraySource(audio, SR, label="Mix")
    assert (src.sample_rate, src.channels, src.n_frames, src.label) == (SR, 2, 100, "Mix")
    assert src.data.shape == (100, 2) and src.data.dtype == np.float32
    assert src.data.flags.c_contiguous and not src.data.flags.writeable
    np.testing.assert_array_equal(src.data, _exp(audio))
    audio[:] = 0.0
    assert np.any(src.data != 0.0)  # private copy
    with pytest.raises(AttributeError):
        src.label = "x"
    with pytest.raises(ValueError):
        src.data[0, 0] = 1.0

    # A (channels, frames) float32 view of a (frames, channels) buffer is still copied.
    base = np.zeros((50, 2), dtype=np.float32)
    src2 = ArraySource(base.T, SR)
    base[:] = 1.0
    assert np.all(src2.data == 0.0)


def test_array_source_reads_inside_boundaries_before_zero_and_past_end():
    audio = _audio(2, 100)
    exp = _exp(audio)
    src = ArraySource(audio, SR)
    out = np.full((32, 2), np.nan, dtype=np.float32)

    assert src.read_into(10, out) == 32
    np.testing.assert_array_equal(out, exp[10:42])

    assert src.read_into(90, out) == 10  # straddles the end
    np.testing.assert_array_equal(out[:10], exp[90:])
    assert np.all(out[10:] == 0.0)

    assert src.read_into(-5, out) == 27  # straddles 0
    assert np.all(out[:5] == 0.0)
    np.testing.assert_array_equal(out[5:], exp[:27])

    for start in (100, 1000, -32, -1000):
        out.fill(np.nan)
        assert src.read_into(start, out) == 0
        assert np.all(out == 0.0)

    whole = np.empty((100, 2), dtype=np.float32)
    assert src.read_into(0, whole) == 100
    np.testing.assert_array_equal(whole, exp)

    with pytest.raises(ValueError):
        src.read_into(0, np.empty((8, 1), dtype=np.float32))


def test_array_source_offset_and_total_frames():
    buf = _audio(2, 50, seed=3)
    exp = _exp(buf)
    out = np.full((32, 2), np.nan, dtype=np.float32)

    src = ArraySource(buf, SR, offset=100)
    assert src.n_frames == 150 and src.offset == 100 and src.buffer_frames == 50
    assert src.read_into(90, out) == 32
    assert np.all(out[:10] == 0.0)
    np.testing.assert_array_equal(out[10:], exp[:22])
    assert src.read_into(140, out) == 10
    np.testing.assert_array_equal(out[:10], exp[40:])
    assert np.all(out[10:] == 0.0)
    assert src.read_into(0, out) == 32 and np.all(out == 0.0)  # silent lead-in is timeline

    longer = ArraySource(buf, SR, offset=100, total_frames=1000)
    assert longer.n_frames == 1000
    assert longer.read_into(140, out) == 32
    np.testing.assert_array_equal(out[:10], exp[40:])
    assert np.all(out[10:] == 0.0)

    shorter = ArraySource(buf, SR, offset=100, total_frames=120)
    assert shorter.read_into(110, out) == 10
    np.testing.assert_array_equal(out[:10], exp[10:20])
    assert np.all(out[10:] == 0.0)


def test_overlay_source_replaces_only_the_overlay_span():
    base_audio = _audio(2, 200, seed=1)
    ov_audio = _audio(2, 30, seed=2)
    eb, eo = _exp(base_audio), _exp(ov_audio)
    base = ArraySource(base_audio, SR, label="Mix")
    base_before = base.data.copy()

    o = OverlaySource(base, 50, ov_audio)
    assert o.n_frames == 200 and o.label == "Mix" and o.channels == 2
    out = np.full((64, 2), np.nan, dtype=np.float32)
    assert o.read_into(40, out) == 64
    np.testing.assert_array_equal(out[:10], eb[40:50])
    np.testing.assert_array_equal(out[10:40], eo)
    np.testing.assert_array_equal(out[40:], eb[80:104])

    tail = OverlaySource(base, 190, ov_audio, label="Patched")  # extends the timeline
    assert tail.n_frames == 220 and tail.label == "Patched"
    assert tail.read_into(180, out) == 40
    np.testing.assert_array_equal(out[:10], eb[180:190])
    np.testing.assert_array_equal(out[10:40], eo)
    assert np.all(out[40:] == 0.0)

    head = OverlaySource(base, -10, ov_audio)
    small = np.empty((32, 2), dtype=np.float32)
    assert head.read_into(0, small) == 32
    np.testing.assert_array_equal(small[:20], eo[10:])
    np.testing.assert_array_equal(small[20:], eb[20:32])

    nested = OverlaySource(o, 0, ov_audio[:, :5])
    assert nested.read_into(0, small) == 32
    np.testing.assert_array_equal(small[:5], eo[:5])
    np.testing.assert_array_equal(small[5:], eb[5:32])

    np.testing.assert_array_equal(base.data, base_before)
    with pytest.raises(ValueError):
        OverlaySource(base, 0, _audio(1, 10))


def test_silence_source_and_validation():
    s = SilenceSource(SR, 2, 100, label="Silence")
    out = np.full((64, 2), np.nan, dtype=np.float32)
    assert s.read_into(80, out) == 20 and np.all(out == 0.0)
    with pytest.raises(ValueError):
        SilenceSource(0, 2, 10)
    with pytest.raises(ValueError):
        SilenceSource(SR, 0, 10)
    with pytest.raises(TypeError):
        ArraySource(np.zeros((2, 10), dtype=np.int16), SR)
    with pytest.raises(ValueError):
        ArraySource(np.zeros((2, 3, 4)), SR)


def test_mono_1d_input_and_non_finite_samples_are_silenced():
    a = np.array([0.1, np.nan, np.inf, -np.inf, 0.2])
    src = ArraySource(a, SR)
    assert src.channels == 1 and src.n_frames == 5
    np.testing.assert_array_equal(src.data[:, 0], np.array([0.1, 0, 0, 0, 0.2], dtype=np.float32))


def test_read_into_does_not_allocate():
    big = ArraySource(_audio(2, SR * 10), SR, offset=1000)
    ov = OverlaySource(big, 5000, _audio(2, 5000, seed=9))
    silence = SilenceSource(SR, 2, SR)
    out = np.empty((512, 2), dtype=np.float32)
    starts = [0, 900, 5100, 9800, SR * 10 + 900, -300, 10**9]
    for src in (big, ov, silence):
        src.read_into(0, out)  # warm up
        tracemalloc.start()
        try:
            base = tracemalloc.get_traced_memory()[0]
            tracemalloc.reset_peak()
            for s in starts:
                src.read_into(s, out)
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        assert peak - base < 4096, f"{type(src).__name__} allocated {peak - base} bytes"


# =========================================================================== transport


def test_play_from_position_and_state():
    audio = _audio(2, 5000)
    exp = _exp(audio)
    eng, be = _engine()
    st = eng.state()
    assert st.device_name == "Default output" and not st.playing and st.error is None
    assert eng.play() is False  # no source yet

    eng.set_source(ArraySource(audio, SR, label="Mix"))
    eng.seek(1000)
    assert eng.play() is True and eng.is_playing
    assert (be.sample_rate, be.channels) == (SR, 2)
    got = _pull_many(be, [256, 100, 512])
    np.testing.assert_array_equal(got, exp[1000:1868])
    assert eng.position == 1868
    st = eng.state()
    assert st.playing and st.position == 1868 and st.source_label == "Mix"
    assert st.n_frames == 5000 and st.sample_rate == SR and st.device_name == "Fake output"
    assert st.device_sample_rate == SR and st.volume == 1.0 and st.loop is None

    assert eng.play(start=4000) is True  # retarget while playing
    np.testing.assert_array_equal(be.pull(64), exp[4000:4064])


def test_pause_resume_is_sample_continuous():
    audio = _audio(2, 6000)
    exp = _exp(audio)
    eng, be = _engine()
    eng.set_source(ArraySource(audio, SR))
    blocks = []
    eng.play()
    blocks += [be.pull(300), be.pull(77)]
    eng.pause()
    assert not eng.is_playing and eng.position == 377
    assert np.all(be.pull(128) == 0.0) and eng.position == 377  # stream stopped
    for sizes in ([1000, 5], [1], [513, 2048 + 3]):
        eng.play()
        blocks += [be.pull(s) for s in sizes]
        eng.pause()
    got = np.concatenate(blocks)
    np.testing.assert_array_equal(got, exp[: got.shape[0]])
    assert eng.position == got.shape[0]


def test_stop_keeps_position_unless_return_to():
    audio = _audio(2, 3000)
    exp = _exp(audio)
    eng, be = _engine()
    eng.set_source(ArraySource(audio, SR))
    eng.play()
    be.pull(500)
    eng.stop()
    assert not eng.is_playing and eng.position == 500
    eng.play()
    np.testing.assert_array_equal(be.pull(100), exp[500:600])
    eng.stop(return_to=0)
    assert eng.position == 0
    eng.stop(return_to=10**9)
    assert eng.position == 3000


def test_seek_while_playing_and_clamping():
    audio = _audio(2, 5000)
    exp = _exp(audio)
    eng, be = _engine()
    eng.set_source(ArraySource(audio, SR))
    eng.play()
    be.pull(100)
    eng.seek(3000)
    assert eng.position == 3000  # pending seek reported before the callback runs
    np.testing.assert_array_equal(be.pull(64), exp[3000:3064])
    assert eng.position == 3064
    eng.seek(-5)
    np.testing.assert_array_equal(be.pull(10), exp[0:10])
    eng.pause()
    eng.seek(10**9)
    assert eng.position == 5000
    eng.seek(42)
    assert eng.position == 42


def test_end_of_source_stops_and_calls_on_finished():
    audio = _audio(2, 1000)
    exp = _exp(audio)
    eng, be = _engine()
    finished = []
    eng.on_finished = lambda: finished.append(eng.position)
    eng.set_source(ArraySource(audio, SR))
    eng.play(start=900)
    out = be.pull(256)
    np.testing.assert_array_equal(out[:100], exp[900:])
    assert np.all(out[100:] == 0.0)
    assert finished == [1000] and not eng.is_playing and eng.position == 1000
    assert not be.active
    assert np.all(be.pull(256) == 0.0) and finished == [1000]

    # A block that ends exactly on the last frame finishes in that same callback.
    eng.play(start=744)
    np.testing.assert_array_equal(be.pull(256), exp[744:])
    assert finished == [1000, 1000] and not eng.is_playing

    # Play at the end restarts from the top.
    assert eng.play() is True
    np.testing.assert_array_equal(be.pull(64), exp[:64])


def test_play_range_stops_at_end_frame():
    audio = _audio(2, 2000)
    exp = _exp(audio)
    eng, be = _engine()
    finished = []
    eng.on_finished = lambda: finished.append(True)
    eng.set_source(ArraySource(audio, SR))
    assert eng.play(start=100, end=300)
    assert eng.state().play_end == 300
    first, second = be.pull(128), be.pull(128)
    np.testing.assert_array_equal(first, exp[100:228])
    np.testing.assert_array_equal(second[:72], exp[228:300])
    assert np.all(second[72:] == 0.0)
    assert eng.position == 300 and not eng.is_playing and finished == [True]

    # Resuming after a finished range plays on from there (range cleared).
    assert eng.play()
    np.testing.assert_array_equal(be.pull(500), exp[300:800])
    eng.stop()
    assert eng.play(start=500, end=400) is False  # nothing to play
    assert not eng.is_playing


def test_pause_resume_keeps_play_range_and_stop_clears_it():
    audio = _audio(2, 2000)
    exp = _exp(audio)
    eng, be = _engine()
    eng.set_source(ArraySource(audio, SR))
    eng.play(0, 500)
    a = be.pull(200)
    eng.pause()
    eng.play()
    b = be.pull(400)
    np.testing.assert_array_equal(np.concatenate([a, b[:300]]), exp[:500])
    assert np.all(b[300:] == 0.0) and not eng.is_playing

    eng.play(0, 500)
    be.pull(100)
    eng.stop()
    assert eng.state().play_end is None
    eng.play()
    np.testing.assert_array_equal(be.pull(1000), exp[100:1100])


def test_on_finished_not_called_on_pause_or_stop():
    eng, be = _engine()
    called = []
    eng.on_finished = lambda: called.append(1)
    eng.set_source(ArraySource(_audio(2, 1000), SR))
    eng.play()
    be.pull(10)
    eng.pause()
    eng.play()
    be.pull(10)
    eng.stop()
    assert called == []


# =========================================================================== loop


def test_loop_wraps_sample_accurately():
    audio = _audio(2, 5000)
    exp = _exp(audio)
    eng, be = _engine()
    eng.set_source(ArraySource(audio, SR))
    eng.set_loop(1000, 1300)
    assert eng.loop_enabled and eng.loop == (1000, 1300)
    eng.play(start=1000)
    sizes = [64, 200, 1, 333, 4096, 3000, 17, 1024, DEFAULT_SCRATCH_FRAMES * 2 + 5]
    got = _pull_many(be, sizes)
    total = sum(sizes)
    region = exp[1000:1300]
    expected = np.tile(region, (math.ceil(total / 300) + 1, 1))[:total]
    np.testing.assert_array_equal(got, expected)
    assert eng.is_playing and eng.position == 1000 + total % 300


def test_loop_entered_from_before_and_resume_outside_loop_jumps_to_start():
    audio = _audio(2, 5000)
    exp = _exp(audio)
    eng, be = _engine()
    eng.set_source(ArraySource(audio, SR))
    eng.set_loop(1000, 1100)
    eng.play(start=900)
    got = be.pull(400)
    np.testing.assert_array_equal(got[:100], exp[900:1000])
    np.testing.assert_array_equal(got[100:], np.tile(exp[1000:1100], (3, 1)))
    eng.stop()
    eng.seek(3000)
    eng.play()  # no explicit start, playhead outside loop
    np.testing.assert_array_equal(be.pull(150), np.concatenate([exp[1000:1100], exp[1000:1050]]))

    # Loop end beyond the source is clipped to the source length.
    eng.stop()
    eng.set_loop(4900, 10**9)
    eng.play(start=4900)
    np.testing.assert_array_equal(be.pull(250), np.tile(exp[4900:5000], (3, 1))[:250])

    # Clearing the loop while playing runs on to the end.
    eng.stop()
    eng.set_loop(1000, 1100)
    eng.play(start=1050)
    be.pull(25)
    eng.clear_loop()
    assert not eng.loop_enabled
    np.testing.assert_array_equal(be.pull(100), exp[1075:1175])

    with pytest.raises(ValueError):
        eng.set_loop(10, 10)
    with pytest.raises(ValueError):
        eng.set_loop(-1, 10)


# =========================================================================== A/B swap


def test_set_source_while_playing_crossfades_at_same_position():
    n = 20000
    a_audio, b_audio = _audio(2, n, seed=10), _audio(2, n, seed=11)
    ea, eb = _exp(a_audio), _exp(b_audio)
    eng, be = _engine()
    eng.set_source(ArraySource(a_audio, SR, label="A"))
    eng.play()
    pre = be.pull(1000)
    np.testing.assert_array_equal(pre, ea[:1000])
    eng.set_source(ArraySource(b_audio, SR, label="B"), crossfade_ms=10.0)
    assert eng.position == 1000 and eng.state().source_label == "B"
    blk = _pull_many(be, [128] * 16)  # the fade spans several callbacks
    L = round(10.0 * SR / 1000)
    fade = blk[:L]
    a, b = ea[1000 : 1000 + L], eb[1000 : 1000 + L]
    assert np.all(fade >= np.minimum(a, b) - 1e-6)
    assert np.all(fade <= np.maximum(a, b) + 1e-6)
    w = (np.arange(1, L + 1) / (L + 1))[:, None]
    np.testing.assert_allclose(fade, a * (1 - w) + b * w, atol=1e-6)
    np.testing.assert_array_equal(blk[L:], eb[1000 + L : 1000 + 16 * 128])  # exact after fade
    assert eng.position == 1000 + 16 * 128


def test_toggle_back_mid_fade_is_continuous():
    eng, be = _engine()
    a = ArraySource(_dc(0.5), SR, label="A")
    b = ArraySource(_dc(-0.5), SR, label="B")
    eng.set_source(a)
    eng.play()
    be.pull(64)
    eng.set_source(b, crossfade_ms=10.0)  # 480-frame fade
    x = be.pull(100)[:, 0]
    eng.set_source(a, crossfade_ms=10.0)  # back to A before the fade finished
    y = _pull_many(be, [100] * 10)[:, 0]
    seq = np.concatenate([[0.5], x, y])
    step = 1.0 / 481
    assert np.max(np.abs(np.diff(seq))) <= 1.5 * step  # no jump anywhere
    assert np.all(seq >= -0.5 - 1e-6) and np.all(seq <= 0.5 + 1e-6)
    assert np.all(y[-200:] == 0.5)  # back to exactly A


def test_set_source_when_not_playing_switches_without_fade():
    a_audio, b_audio = _audio(2, 3000, seed=1), _audio(2, 3000, seed=2)
    eng, be = _engine()
    eng.set_source(ArraySource(a_audio, SR))
    eng.play()
    be.pull(200)
    eng.pause()
    eng.set_source(ArraySource(b_audio, SR))
    assert eng.position == 200
    eng.play()
    np.testing.assert_array_equal(be.pull(300), _exp(b_audio)[200:500])


def test_set_source_keep_position_false_restarts_at_zero():
    a_audio, b_audio = _audio(2, 3000, seed=1), _audio(2, 3000, seed=2)
    eng, be = _engine()
    eng.set_source(ArraySource(a_audio, SR))
    eng.play()
    be.pull(700)
    eng.set_source(ArraySource(b_audio, SR), keep_position=False)
    np.testing.assert_array_equal(be.pull(100), _exp(b_audio)[:100])


def test_set_source_format_change_reopens_stream_and_keeps_position():
    eng, be = _engine()
    eng.set_source(ArraySource(_audio(2, 50000, seed=1), SR))
    eng.play()
    be.pull(1000)
    b_audio = _audio(2, 50000, seed=2)
    eng.set_source(ArraySource(b_audio, 44100))
    assert be.open_count == 2 and be.sample_rate == 44100 and eng.is_playing
    assert eng.position == 1000
    np.testing.assert_array_equal(be.pull(100), _exp(b_audio)[1000:1100])
    mono = _audio(1, 50000, seed=3)
    eng.set_source(ArraySource(mono, 44100))
    assert be.open_count == 3 and be.channels == 1 and eng.position == 1100
    np.testing.assert_array_equal(be.pull(50), _exp(mono)[1100:1150])

    eng.set_source(None)
    assert not eng.is_playing and eng.source is None and eng.position == 0


def test_large_callback_blocks_are_chunked():
    a, b = _audio(2, 50000, seed=4), _audio(2, 50000, seed=5)
    eng, be = _engine()
    eng.set_source(ArraySource(a, SR))
    eng.play()
    be.pull(10)
    eng.set_volume(0.5)
    eng.set_source(ArraySource(b, SR), crossfade_ms=5.0)
    n = DEFAULT_SCRATCH_FRAMES * 3 + 7
    out = be.pull(n)
    assert np.all(np.isfinite(out))
    np.testing.assert_array_equal(out[2000:], _exp(b)[10 + 2000 : 10 + n] * np.float32(0.5))


def test_callback_does_not_allocate_much():
    eng, be = _engine(volume_ramp_ms=100.0)
    eng.set_source(ArraySource(_audio(2, SR * 2, seed=6), SR))
    eng.play()
    out = np.empty((512, 2), dtype=np.float32)
    be.pull(512, out=out)
    eng.set_source(ArraySource(_audio(2, SR * 2, seed=7), SR), crossfade_ms=100.0)
    eng.set_volume(0.3)  # fade + volume ramp both active while measuring
    tracemalloc.start()
    try:
        base = tracemalloc.get_traced_memory()[0]
        tracemalloc.reset_peak()
        for _ in range(8):
            be.pull(512, out=out)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert peak - base < 8192, f"callback allocated {peak - base} bytes"
    assert eng._cb_fade_src is not None  # the fade really was running


# =========================================================================== volume


def test_volume_ramps_to_target_and_never_touches_sources():
    audio = _dc(0.5, n=SR)
    audio_before = audio.copy()
    src = ArraySource(audio, SR)
    data_before = src.data.copy()
    eng, be = _engine(volume_ramp_ms=20.0)
    eng.set_source(src)
    eng.play()
    assert np.all(be.pull(256) == 0.5)
    eng.set_volume(0.25)
    got = _pull_many(be, [256] * 10)[:, 0]
    gain = got / 0.5
    assert np.all(np.diff(gain) <= 1e-7)  # monotone toward target
    max_step = 1.0 / (0.020 * SR)
    assert np.max(np.abs(np.diff(np.concatenate([[1.0], gain])))) <= max_step + 1e-6
    ramp_len = math.ceil(0.75 / max_step)
    assert np.all(got[ramp_len + 256 :] == np.float32(0.125))  # reached exactly
    assert eng.volume == 0.25 and eng.state().volume == 0.25
    np.testing.assert_array_equal(src.data, data_before)
    np.testing.assert_array_equal(audio, audio_before)

    eng.stop()
    eng.set_volume(2.0)  # set while stopped: applies immediately on play
    eng.play()
    assert np.all(be.pull(64) == 1.0)

    eng.set_volume(-1.0)
    assert eng.volume == 0.0
    eng.set_volume(100.0)
    assert eng.volume == MAX_VOLUME
    with pytest.raises(ValueError):
        eng.set_volume(float("nan"))


# =========================================================================== errors


def test_backend_open_error_is_reported_not_raised():
    be = FakeBackend(open_error=RuntimeError("no such device"))
    eng = PlaybackEngine(be, stall_timeout=None)
    errors = []
    eng.on_error = errors.append
    eng.set_source(ArraySource(_audio(2, 1000), SR))
    assert eng.play() is False
    st = eng.state()
    assert "no such device" in st.error and not st.playing and len(errors) == 1

    be.open_error = None  # device back: engine is still usable
    assert eng.play() is True
    assert eng.state().error is None
    np.testing.assert_array_equal(be.pull(16), _exp(_audio(2, 1000))[:16])


def test_backend_start_error_is_reported_not_raised():
    be = FakeBackend(start_error=OSError("device busy"))
    eng = PlaybackEngine(be, stall_timeout=None)
    errors = []
    eng.on_error = errors.append
    eng.set_source(ArraySource(_audio(2, 1000), SR))
    assert eng.play() is False
    assert "device busy" in eng.state().error and errors and not eng.is_playing


def test_device_disconnect_sets_error_and_engine_recovers():
    audio = _audio(2, 5000)
    exp = _exp(audio)
    eng, be = _engine()
    errors = []
    eng.on_error = errors.append
    eng.set_source(ArraySource(audio, SR))
    eng.play()
    be.pull(300)
    be.disconnect()
    st = eng.state()
    assert not st.playing and "unexpectedly" in st.error and len(errors) == 1
    assert st.position == 300
    assert eng.play() is True  # start fails on the dead stream -> reopened
    assert be.open_count == 2 and eng.state().error is None
    np.testing.assert_array_equal(be.pull(100), exp[300:400])


def test_underflow_tolerance_and_sustained_underflow_is_fatal():
    be = FakeBackend()
    eng = PlaybackEngine(be, max_consecutive_underflows=2, stall_timeout=None)
    errors = []
    eng.on_error = errors.append
    eng.set_source(ArraySource(_audio(2, 50000), SR))
    eng.play()
    be.pull(64, underflow=True)
    be.pull(64)
    be.pull(64, underflow=True)
    be.pull(64, underflow=True)
    assert eng.is_playing and eng.state().underflows == 3 and not errors
    out = be.pull(64, underflow=True)  # third consecutive
    assert not eng.is_playing and "underflow" in eng.state().error and len(errors) == 1
    assert np.all(out == 0.0)

    strict_be = FakeBackend()
    strict = PlaybackEngine(strict_be, max_consecutive_underflows=0, stall_timeout=None)
    strict.set_source(ArraySource(_audio(2, 5000), SR))
    strict.play()
    strict_be.pull(64, underflow=True)
    assert not strict.is_playing and strict.state().error


def test_exception_inside_callback_is_contained():
    class Broken(PlaybackSource):
        def read_into(self, start, out):
            raise RuntimeError("render bug")

    eng, be = _engine()
    errors = []
    eng.on_error = errors.append
    eng.set_source(Broken(SR, 2, 1000))
    eng.play()
    out = be.pull(64)  # must not raise
    assert np.all(out == 0.0) and not eng.is_playing
    assert "render bug" in eng.state().error and len(errors) == 1


def test_stall_watchdog_detects_dead_device():
    lazy_be = FakeBackend()  # realtime=False: exempt even with a tiny timeout
    lazy = PlaybackEngine(lazy_be, stall_timeout=0.01)
    lazy.set_source(ArraySource(_audio(2, 5000), SR))
    lazy.play()
    time.sleep(0.05)
    assert lazy.state().playing

    be = FakeBackend(realtime=True)
    eng = PlaybackEngine(be, stall_timeout=0.05)
    errors = []
    eng.on_error = errors.append
    eng.set_source(ArraySource(_audio(2, 5000), SR))
    eng.play()
    be.pull(64)
    time.sleep(0.15)  # no callbacks arrive
    st = eng.state()
    assert not st.playing and "stopped responding" in st.error and len(errors) == 1
    assert not be.is_open
    assert eng.play() is True and be.open_count == 2


def test_set_output_device_while_playing_reopens_and_resumes():
    audio = _audio(2, 5000)
    eng, be = _engine()
    eng.set_source(ArraySource(audio, SR))
    eng.play()
    be.pull(100)
    eng.set_output_device(3)
    assert be.device == 3 and be.open_count == 2 and eng.is_playing
    assert eng.output_device == 3
    np.testing.assert_array_equal(be.pull(50), _exp(audio)[100:150])


def test_list_output_devices():
    devices = PlaybackEngine.list_output_devices()
    assert isinstance(devices, list)
    for d in devices:
        assert {"id", "name", "max_output_channels", "default_samplerate", "is_default"} <= set(d)
        assert d["max_output_channels"] > 0


def test_list_output_devices_without_portaudio(monkeypatch):
    monkeypatch.setitem(sys.modules, "sounddevice", None)  # import now raises ImportError
    assert PlaybackEngine.list_output_devices() == []


def test_default_backend_is_sounddevice_and_lazy():
    eng = PlaybackEngine()
    assert isinstance(eng.backend, SoundDeviceBackend)
    assert isinstance(eng.backend, PlaybackBackend)
    assert isinstance(FakeBackend(), PlaybackBackend)
    assert eng.state().device_name == "Default output"
    eng.close()


# =========================================================================== real device (opt-in)


@audio_device
@pytest.mark.skipif(
    os.environ.get("SPLEDIO_AUDIO_TEST") != "1",
    reason="real audio-device test; set SPLEDIO_AUDIO_TEST=1 to run",
)
def test_real_device_smoke():
    devices = PlaybackEngine.list_output_devices()
    assert devices, "no output devices found"
    default = next((d for d in devices if d["is_default"]), devices[0])
    sr = int(default["default_samplerate"])
    n = int(0.3 * sr)
    t = np.arange(n) / sr
    amp = 10 ** (-30 / 20)
    env = np.minimum(1.0, np.minimum(t, t[::-1]) / 0.02)  # 20 ms fades: no clicks
    sig = (amp * env * np.sin(2 * np.pi * 440.0 * t))[np.newaxis, :]

    eng = PlaybackEngine(SoundDeviceBackend())
    finished = threading.Event()
    errors = []
    eng.on_finished = finished.set
    eng.on_error = errors.append
    try:
        eng.set_source(ArraySource(sig, sr, label="smoke"))
        assert eng.play(), eng.state().error
        assert finished.wait(3.0), f"did not finish; state={eng.state()}"
        st = eng.state()
    finally:
        eng.close()
    assert not errors, errors
    assert st.error is None
    assert st.position == n  # advanced through the whole buffer
    assert st.device_name
