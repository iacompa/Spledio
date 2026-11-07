"""Tests for spledio.rendering: display analysis, scales, colour maps, image output."""

from __future__ import annotations

import threading
import time
import tracemalloc

import numpy as np
import pytest
import scipy.fft

from spledio.dsp.stft import STFTConfig, magnitude_reference, stft
from spledio.rendering import analysis as analysis_mod
from spledio.rendering.analysis import (
    DB_FLOOR,
    TILE_FRAMES,
    DisplayAnalysisSettings,
    SpectrogramAnalyzer,
    TileStatus,
)
from spledio.rendering.colormaps import COLORBLIND_FRIENDLY, COLORMAP_NAMES, get_lut, lightness
from spledio.rendering.image import colorize, hatch_colour, map_grid_to_view, mask_overlay, nearest_cells
from spledio.rendering.scales import (
    FrequencyScale,
    nice_freq_ticks,
    nice_time_ticks,
    note_name,
    note_ticks,
    row_centres_hz,
    row_edges_hz,
)

CFG_2048 = STFTConfig(2048, 512, "hann")


# --------------------------------------------------------------------------- helpers


class ArrayProvider:
    """Serves ``x`` (channels, n) with zero padding; returns read-only data and counts calls."""

    def __init__(self, x):
        self.x = np.atleast_2d(np.asarray(x, dtype=np.float64))
        self.calls = 0
        self.max_len = 0
        self._lock = threading.Lock()

    @property
    def n(self):
        return self.x.shape[1]

    def __call__(self, a, b):
        with self._lock:
            self.calls += 1
            self.max_len = max(self.max_len, b - a)
        n = self.n
        if 0 <= a and b <= n:
            v = self.x[:, a:b].view()
            v.flags.writeable = False
            return v
        out = np.zeros((self.x.shape[0], b - a))
        lo, hi = max(a, 0), min(b, n)
        if hi > lo:
            out[:, lo - a : hi - a] = self.x[:, lo:hi]
        out.flags.writeable = False
        return out


class LazyProvider:
    """Generates samples on demand from ``fn(sample_index_array) -> (channels, len)``."""

    def __init__(self, fn, n, channels=1):
        self.fn, self.n, self.channels = fn, n, channels
        self.calls = 0
        self.max_len = 0
        self._lock = threading.Lock()

    def __call__(self, a, b):
        with self._lock:
            self.calls += 1
            self.max_len = max(self.max_len, b - a)
        out = np.zeros((self.channels, b - a))
        lo, hi = max(a, 0), min(b, self.n)
        if hi > lo:
            out[:, lo - a : hi - a] = self.fn(np.arange(lo, hi, dtype=np.float64))
        return out


def _hash_noise(idx):
    """Deterministic pseudo-random noise in [-1, 1) from a sample index (lazy, position-stable)."""
    v = np.sin(idx * 12.9898 + 78.233) * 43758.5453
    return 2.0 * (v - np.floor(v)) - 1.0


@pytest.fixture
def analyzers():
    made = []

    def make(**kw):
        an = SpectrogramAnalyzer(**kw)
        made.append(an)
        return an

    yield make
    for an in made:
        an.shutdown()


def sine(freq, amp, sr, seconds, phase=0.0):
    t = np.arange(int(round(sr * seconds))) / sr
    return amp * np.sin(2 * np.pi * freq * t + phase)


def row_spans_top_first(scale, height, f_lo=None, f_hi=None):
    e = row_edges_hz(scale, height, f_lo, f_hi)
    return e[:-1][::-1], e[1:][::-1]  # (lower, upper) per image row, row 0 = top


# --------------------------------------------------------------------------- level / frequency


@pytest.mark.parametrize("kind", [FrequencyScale.LINEAR, FrequencyScale.LOG])
def test_sine_peak_frequency_and_level(analyzers, kind):
    # 44.1 kHz: 1000 Hz sits 0.12 bin from bin 93 (n_fft 4096) -> Hann scalloping only 0.08 dB.
    # (At 48 kHz, 1000 Hz is always 1/3 bin off-centre for power-of-two FFTs: -0.63 dB.)
    sr = 44100
    x = sine(1000.0, 0.5, sr, 4.0)
    prov = ArrayProvider(x)
    an = analyzers()
    an.set_audio(prov, prov.n, 1, sr, version=1)
    df = sr / 4096
    W, H = 400, 600
    scale = FrequencyScale.for_analysis(kind, 0.0, sr / 2, sr, 4096)
    vd = an.compute_view_sync(0.0, 4.0, None, None, W, H, scale)
    assert vd.db.shape == (H, W) and vd.db.dtype == np.float32
    assert vd.status.shape == (H, W) and vd.status.dtype == np.uint8
    assert vd.frame_index.shape == (W,) and vd.frame_index.dtype == np.int64
    assert (vd.status == TileStatus.READY).all()
    lower, upper = row_spans_top_first(scale, H)
    for c in (50, 200, 350):  # away from the zero-padded edges
        r = int(np.argmax(vd.db[:, c]))
        assert lower[r] - df <= 1000.0 <= upper[r] + df
        assert vd.db[r, c] == pytest.approx(-6.02, abs=0.5)

    # Zoomed in: rows much narrower than a bin (interpolation regime), peak level unchanged.
    zoom = FrequencyScale.for_analysis(kind, 900.0, 1100.0, sr, 4096)
    vz = an.compute_view_sync(1.0, 3.0, None, None, 100, 500, zoom)
    centres = row_centres_hz(zoom, 500)[::-1]
    r = int(np.argmax(vz.db[:, 50]))
    assert abs(centres[r] - 1000.0) <= df
    assert vz.db[r, 50] == pytest.approx(-6.02, abs=0.5)


def test_bin_centred_sine_reads_exact_level_and_stereo_mix_is_power_mean(analyzers):
    sr = 48000
    f = 88 * sr / 4096  # 1031.25 Hz, exactly bin 88
    tone = sine(f, 0.5, sr, 3.0)
    scale = FrequencyScale.for_analysis("linear", 0, sr / 2, sr, 4096)
    an = analyzers()
    prov = ArrayProvider(np.stack([np.zeros_like(tone), tone]))  # tone on channel 1 only
    an.set_audio(prov, prov.n, 2, sr, version=1)
    levels = {}
    for mode in ("ch1", "mix", "ch0"):
        an.set_settings(DisplayAnalysisSettings(channel_mode=mode))
        vd = an.compute_view_sync(0.5, 2.5, None, None, 200, 400, scale)
        levels[mode] = float(vd.db[:, 100].max())
    assert levels["ch1"] == pytest.approx(-6.0206, abs=0.05)
    assert levels["mix"] == pytest.approx(-6.0206 - 3.0103, abs=0.05)  # mean of per-channel power
    assert levels["ch0"] == pytest.approx(DB_FLOOR, abs=1.0)  # digital silence
    with pytest.raises(ValueError):
        an.set_settings(DisplayAnalysisSettings(channel_mode="ch2"))


def test_zoomed_out_view_keeps_narrow_resonance_via_max_pooling(analyzers):
    sr, seconds = 48000, 120
    n = sr * seconds
    f_res = 3000.0  # = bin 256 at n_fft 4096 -> no scalloping
    amp = 0.05  # -26.02 dB

    def fn(idx):
        return (amp * np.sin(2 * np.pi * f_res * idx / sr) + 1e-4 * _hash_noise(idx))[None, :]

    prov = LazyProvider(fn, n)
    an = analyzers()
    an.set_audio(prov, n, 1, sr, version=1)
    H = 48  # 500 Hz (~43 bins) per row
    scale = FrequencyScale("linear", 0, sr / 2, sr / 2)
    vd = an.compute_view_sync(0.0, float(seconds), None, None, 300, H, scale)
    assert vd.stride > 1  # level of detail actually engaged
    lower, upper = row_spans_top_first(scale, H)
    r_res = int(np.flatnonzero((lower <= f_res) & (upper > f_res))[0])
    expected = 20 * np.log10(amp)
    for c in (20, 150, 280):
        col = vd.db[:, c]
        assert int(np.argmax(col)) == r_res
        assert col[r_res] == pytest.approx(expected, abs=0.5)
        # mean-pooling the same row would hide it: compare against the true bins of that frame
        k = int(vd.frame_index[c])
        X = stft(fn(np.arange(k * 1024 - 2048, k * 1024 + 2048, dtype=np.float64))[0], STFTConfig(), 2, 3)[0]
        db_bins = 20 * np.log10(np.abs(X) / magnitude_reference(STFTConfig()) + 1e-12)
        b = np.arange(len(db_bins)) * sr / 4096
        in_row = (b >= lower[r_res]) & (b < upper[r_res])
        mean_db = 10 * np.log10(np.mean(10 ** (db_bins[in_row] / 10)))
        assert col[r_res] > mean_db + 10
        assert col[r_res] == pytest.approx(db_bins[in_row].max(), abs=1e-3)  # the real bin, not invented


def test_rows_partition_bins_and_interpolation_stays_between_neighbours(analyzers):
    sr = 48000
    rng = np.random.default_rng(1)
    x = rng.standard_normal(sr * 2) * 0.1
    prov = ArrayProvider(x)
    an = analyzers()
    an.set_audio(prov, prov.n, 1, sr, version=1)
    cfg = STFTConfig()
    # max regime: every bin belongs to exactly one row -> column max == max over all bins
    scale = FrequencyScale("linear", 0, sr / 2, sr / 2)
    vd = an.compute_view_sync(0.0, 2.0, None, None, 64, 100, scale)
    c = 30
    k = int(vd.frame_index[c])
    X = stft(x, cfg, k, k + 1)[0]
    db_bins = (20 * np.log10(np.abs(X) / magnitude_reference(cfg) + 1e-12)).astype(np.float32)
    assert vd.db[:, c].max() == pytest.approx(db_bins.max(), abs=1e-4)
    # interpolation regime: 0.1 bin per row
    zoom = FrequencyScale("linear", 1000.0, 1000.0 + 50 * sr / 4096, sr / 2)
    vz = an.compute_view_sync(0.0, 2.0, None, None, 64, 500, zoom)
    centres = row_centres_hz(zoom, 500)[::-1]
    p = centres / (sr / 4096)
    b0 = np.floor(p).astype(int)
    lo = np.minimum(db_bins[b0], db_bins[b0 + 1]) - 1e-3
    hi = np.maximum(db_bins[b0], db_bins[b0 + 1]) + 1e-3
    col = vz.db[:, c]
    assert np.all((col >= lo) & (col <= hi))


# --------------------------------------------------------------------------- display-only re-colouring


def test_display_changes_never_rerun_fft_or_touch_audio(analyzers, monkeypatch):
    sr = 48000
    x = sine(440.0, 0.3, sr, 5.0) + 0.01 * np.random.default_rng(0).standard_normal(sr * 5)
    x.setflags(write=False)
    before = x.copy()
    prov = ArrayProvider(x)
    an = analyzers()
    an.set_audio(prov, prov.n, 1, sr, version=1)
    scale = FrequencyScale.for_analysis("log", 0, sr / 2, sr, 4096)
    vd = an.compute_view_sync(0.0, 5.0, None, None, 300, 200, scale)

    counts = {"stft": 0, "rfft": 0}
    real_stft, real_rfft = analysis_mod.stft, scipy.fft.rfft

    def counting_stft(*a, **k):
        counts["stft"] += 1
        return real_stft(*a, **k)

    def counting_rfft(*a, **k):
        counts["rfft"] += 1
        return real_rfft(*a, **k)

    monkeypatch.setattr(analysis_mod, "stft", counting_stft)
    monkeypatch.setattr(scipy.fft, "rfft", counting_rfft)
    calls0 = prov.calls

    images = []
    for name in COLORMAP_NAMES:
        for lo_db, hi_db, gamma in ((-120, 0, 1.0), (-90, -10, 0.5), (-60, 6, 2.2)):
            images.append(colorize(vd.db, vd.status, lo_db, hi_db, gamma, get_lut(name)))
    again = an.view_data(0.0, 5.0, None, None, 300, 200, scale)  # UI repaint after a colour change
    assert again.complete and np.array_equal(again.db, vd.db)
    assert counts == {"stft": 0, "rfft": 0}
    assert prov.calls == calls0
    assert np.array_equal(x, before)
    assert len({im.tobytes() for im in images}) == len(images)  # the settings really changed pixels

    # positive control: the counters do see real analysis
    an.invalidate(0, 100, new_version=2)
    an.compute_view_sync(0.0, 5.0, None, None, 300, 200, scale)
    assert counts["stft"] > 0 and counts["rfft"] > 0 and prov.calls > calls0


# --------------------------------------------------------------------------- async status


def test_pending_tiles_reported_immediately_then_ready(analyzers):
    sr = 48000
    x = sine(1000.0, 0.5, sr, 6.0)
    base = ArrayProvider(x)
    gate = threading.Event()
    threads = []

    def slow(a, b):
        gate.wait(10)
        return base(a, b)

    def on_ready():
        threads.append(threading.current_thread())

    an = analyzers(on_tile_ready=on_ready)
    an.set_audio(slow, base.n, 1, sr, version=1)
    scale = FrequencyScale("linear", 0, sr / 2, sr / 2)
    t = time.perf_counter()
    vd = an.view_data(0.0, 6.0, None, None, 500, 100, scale)
    elapsed = time.perf_counter() - t
    assert elapsed < 0.25  # non-blocking even though every provider call is stuck
    assert (vd.status == TileStatus.PENDING).all()
    assert np.isnan(vd.db).all()  # no data, never fabricated
    assert not vd.complete and vd.pending_tiles > 0
    vd2 = an.view_data(0.0, 6.0, None, None, 500, 100, scale)  # repeated request: no duplicate jobs
    assert an.stats()["inflight"] == vd.pending_tiles == vd2.pending_tiles

    gate.set()
    assert an.wait_idle(10)
    vd3 = an.view_data(0.0, 6.0, None, None, 500, 100, scale)
    assert (vd3.status == TileStatus.READY).all() and vd3.complete
    assert len(threads) == vd.pending_tiles
    assert all(th is not threading.main_thread() for th in threads)


def test_failing_provider_marks_tiles_failed_without_raising(analyzers):
    sr = 48000
    good = ArrayProvider(sine(1000.0, 0.5, sr, 4.0))
    state = {"broken": True}

    def flaky(a, b):
        if state["broken"]:
            raise OSError("device unplugged")
        return good(a, b)

    an = analyzers()
    an.set_audio(flaky, good.n, 1, sr, version=1)
    scale = FrequencyScale("linear", 0, sr / 2, sr / 2)
    vd = an.view_data(0.0, 5.0, None, None, 250, 80, scale)  # 4 s of audio in a 5 s view
    inside = vd.frame_index >= 0
    assert (vd.status[:, inside] == TileStatus.PENDING).all()
    assert (vd.status[:, ~inside] == TileStatus.OUTSIDE).all()
    assert an.wait_idle(10)
    vf = an.view_data(0.0, 5.0, None, None, 250, 80, scale)
    assert (vf.status[:, inside] == TileStatus.FAILED).all() and vf.failed_tiles > 0
    assert np.isnan(vf.db[:, inside]).all()
    assert (vf.db[:, ~inside] == DB_FLOOR).all()
    vs = an.compute_view_sync(0.0, 5.0, None, None, 250, 80, scale, timeout=10)  # no exception, no hang
    assert (vs.status[:, inside] == TileStatus.FAILED).all()
    assert isinstance(an.last_error, OSError)
    calls = good.calls
    an.view_data(0.0, 5.0, None, None, 250, 80, scale)
    assert an.wait_idle(5) and good.calls == calls  # failed tiles are not hammered

    # colouring shows a red hatch, independent of whatever db holds there
    img = colorize(vf.db, vf.status, -120, 0, 1.0, get_lut("ember"))
    allowed = {tuple(np.array((120, 20, 20, 255))), hatch_colour((120, 20, 20, 255))}
    seen = {tuple(p) for p in img[:, inside].reshape(-1, 4).tolist()}
    assert seen == {tuple(map(int, c)) for c in allowed}
    assert {tuple(p) for p in img[:, ~inside].reshape(-1, 4).tolist()} == {(16, 16, 20, 255)}

    # retry after invalidate once the provider works again
    state["broken"] = False
    an.invalidate(0, 0, new_version=2)
    vr = an.compute_view_sync(0.0, 5.0, None, None, 250, 80, scale, timeout=10)
    assert (vr.status[:, inside] == TileStatus.READY).all()
    assert vr.db[:, 100].max() == pytest.approx(-6.02, abs=0.7)


def test_colorize_never_uses_db_of_pending_or_failed_pixels():
    H, W = 40, 60
    status = np.zeros((H, W), np.uint8)
    status[:, 10:30] = TileStatus.PENDING
    status[:, 30:45] = TileStatus.FAILED
    status[:, 45:] = TileStatus.OUTSIDE
    rng = np.random.default_rng(3)
    imgs = []
    for fill in (np.nan, -500.0, 0.0, 50.0):
        db = rng.uniform(-100, 0, (H, W)).astype(np.float32)
        db[:, 10:] = fill if not np.isnan(fill) else np.nan
        db[:, 10:] += rng.uniform(-50, 50, (H, 50)).astype(np.float32)
        imgs.append(colorize(db, status, -100, 0, 1.0, get_lut("ice")))
    for im in imgs[1:]:
        assert np.array_equal(im[:, 10:], imgs[0][:, 10:])
    pend = imgs[0][:, 10:30].reshape(-1, 4)
    assert {tuple(p) for p in pend.tolist()} == {(40, 40, 48, 255), hatch_colour((40, 40, 48, 255))}
    # the hatch is diagonal (stripes along x + y = const): one row down = one column left
    p = imgs[0][:, 10:30, 0]
    assert np.array_equal(p[1:, :-1], p[:-1, 1:])
    assert not np.array_equal(p[1:, :], p[:-1, :])  # not horizontal bands
    assert imgs[0].dtype == np.uint8 and imgs[0].shape == (H, W, 4) and imgs[0].flags.c_contiguous


# --------------------------------------------------------------------------- region invalidation


def _affected_tiles(cfg, stride, n_tiles, start, end):
    """Brute force: tiles with an analysed frame whose support overlaps [start, end)."""
    out = set()
    for t in range(n_tiles):
        for j in range(t * TILE_FRAMES, (t + 1) * TILE_FRAMES):
            k = j * stride
            if k * cfg.hop - cfg.half < end and k * cfg.hop + cfg.half > start:
                out.add(t)
                break
    return out


def test_region_invalidation_recomputes_only_overlapping_tiles(analyzers):
    sr = 48000
    cfg = CFG_2048
    x = 0.01 * np.random.default_rng(5).standard_normal(sr * 30)
    prov = ArrayProvider(x)
    an = analyzers()
    an.set_settings(DisplayAnalysisSettings(cfg))
    an.set_audio(prov, prov.n, 1, sr, version=1)
    scale = FrequencyScale("linear", 0, sr / 2, sr / 2)
    full = dict(t0=0.0, t1=30.0, f_lo=None, f_hi=None, width=2000, height=128, scale=scale)
    coarse = dict(full, width=500)  # 5.6 frames per pixel -> stride 4
    v1 = an.compute_view_sync(**full)
    c1 = an.compute_view_sync(**coarse)
    assert v1.stride == 1 and c1.stride == 4
    n_tiles_1 = len(np.unique(v1.frame_index[v1.frame_index >= 0] // TILE_FRAMES))
    n_tiles_4 = len(np.unique(c1.frame_index[c1.frame_index >= 0] // (4 * TILE_FRAMES)))
    s0 = an.stats()
    assert s0["tiles_computed"] == n_tiles_1 + n_tiles_4

    for version, (start, end) in enumerate([(480000, 484800), (524288 - 100, 524288 + 100)], start=2):
        prov.x[0, start:end] += 0.5 * np.sin(np.arange(end - start) * 0.3)  # engine edits the audio
        before = an.stats()["tiles_computed"]
        calls = prov.calls
        an.invalidate(start, end, new_version=version)
        v2 = an.compute_view_sync(**full)
        c2 = an.compute_view_sync(**coarse)
        exp1 = _affected_tiles(cfg, 1, n_tiles_1, start, end)
        exp4 = _affected_tiles(cfg, 4, n_tiles_4, start, end)
        recomputed = an.stats()["tiles_computed"] - before
        assert recomputed == len(exp1) + len(exp4)
        assert prov.calls - calls == recomputed  # contiguous tiles: one provider read each
        assert v2.version == version
        # untouched columns are bit-identical; touched ones show the edit
        ka, kb = cfg.frames_touching(start, end)
        touched = (v2.frame_index >= ka) & (v2.frame_index < kb)
        assert touched.any()
        assert np.array_equal(v2.db[:, ~touched], v1.db[:, ~touched])
        # frames overlapping the edit only at their window tails change little; the centre a lot
        assert v2.db[:, touched].max() > v1.db[:, touched].max() + 10
        v1 = v2
    assert len(exp1) == 2  # the second edit straddles the tile 7 / tile 8 boundary (frame 1024)


def test_invalidate_during_computation_discards_stale_tile(analyzers):
    sr = 48000
    base = ArrayProvider(np.zeros(sr * 6))
    gate = threading.Event()
    entered = []

    def slow(a, b):
        entered.append(a)
        gate.wait(10)
        return base(a, b)

    an = analyzers()
    an.set_audio(slow, base.n, 1, sr, version=1)
    scale = FrequencyScale("linear", 0, sr / 2, sr / 2)
    view = (0.0, 6.0, None, None, 600, 64, scale)
    first = an.view_data(*view)
    n_tiles = first.pending_tiles
    assert n_tiles == 3
    deadline = time.monotonic() + 10
    while len(entered) < n_tiles and time.monotonic() < deadline:  # all jobs are running
        time.sleep(0.001)
    assert len(entered) == n_tiles
    # the engine edits samples inside tile 0 while its job is running
    base.x[0, 24000:28800] = 0.5 * np.sin(2 * np.pi * 1500 * np.arange(4800) / sr)
    an.invalidate(24000, 28800, new_version=2)
    gate.set()
    assert an.wait_idle(10)
    st = an.stats()
    assert st["tiles_discarded"] == 1 and st["tiles_cancelled"] == 0
    assert st["tiles_computed"] == n_tiles - 1
    mid = an.view_data(*view)  # tile 0 is requested again, the others were kept (re-keyed)
    assert mid.version == 2 and mid.pending_tiles == 1
    assert an.wait_idle(10)
    final = an.view_data(*view)
    assert final.complete and an.stats()["tiles_computed"] == n_tiles
    tone_cols = (final.frame_index * 1024 >= 24000) & (final.frame_index * 1024 < 28800)
    assert final.db[:, tone_cols].max() > -10
    assert final.db[:, final.frame_index > 60].max() < -200  # untouched audio is still silence
    # re-binding the same audio/version keeps the cache
    an.set_audio(slow, base.n, 1, sr, version=2)
    again = an.view_data(*view)
    assert again.complete and an.stats()["tiles_computed"] == n_tiles


def test_edit_keeps_previous_image_until_new_tiles_are_ready(analyzers):
    """stale_ok: an edited tile shows its previous analysis (STALE), never the pending hatch,
    so changing an area's level does not make the spectrogram blink."""
    sr = 48000
    base = ArrayProvider(0.1 * np.sin(2 * np.pi * 1000 * np.arange(sr * 6) / sr))
    gate = threading.Event()
    gate.set()

    def gated(a, b):
        gate.wait(10)
        return base(a, b)

    an = analyzers()
    an.set_audio(gated, base.n, 1, sr, version=1)
    scale = FrequencyScale("linear", 0, sr / 2, sr / 2)
    view = (0.0, 6.0, None, None, 600, 64, scale)
    an.view_data(*view)
    assert an.wait_idle(10)
    before = an.view_data(*view)
    assert before.complete
    gate.clear()  # hold the recomputation so the in-between state can be inspected
    base.x[0, 24000:28800] *= 0.25
    an.invalidate(24000, 28800, new_version=2)
    mid = an.view_data(*view, stale_ok=True)
    cs = mid.column_status
    assert not np.any(cs == TileStatus.PENDING)  # nothing hatched
    assert np.any(cs == TileStatus.STALE) and not mid.complete and mid.pending_tiles == 0
    stale_cols = cs == TileStatus.STALE
    assert np.array_equal(mid.db[:, stale_cols], before.db[:, stale_cols])  # previous picture
    plain = an.view_data(*view)  # default behaviour unchanged: PENDING
    assert np.any(plain.column_status == TileStatus.PENDING)
    gate.set()
    assert an.wait_idle(10)
    after = an.view_data(*view, stale_ok=True)
    assert after.complete and after.version == 2 and not np.any(after.column_status == TileStatus.STALE)
    edited = (after.frame_index * 1024 >= 26000) & (after.frame_index * 1024 < 27000)
    assert after.db[:, edited].max() < before.db[:, edited].max() - 6  # the new data arrived


# --------------------------------------------------------------------------- bounded cache


def test_lru_cache_bounded_while_panning_30_minute_signal(analyzers):
    sr = 48000
    n = sr * 60 * 30  # 30 minutes, never materialised
    cfg = CFG_2048

    def fn(idx):  # tone stepping 500, 600, ... Hz each minute, plus faint noise
        f = 500.0 + 100.0 * np.floor(idx / (60 * sr))
        return (0.25 * np.sin(2 * np.pi * f * idx / sr) + 1e-4 * _hash_noise(idx))[None, :]

    prov = LazyProvider(fn, n)
    limit = 4 * 2**20
    an = analyzers(max_cache_bytes=limit)
    an.set_settings(DisplayAnalysisSettings(cfg))
    an.set_audio(prov, n, 1, sr, version=1)
    scale = FrequencyScale("linear", 0, 4000, sr / 2)
    tile_bytes = TILE_FRAMES * cfg.n_bins * 4
    peak_cache = 0
    for t0 in np.linspace(0, 1800 - 6, 60):
        vd = an.compute_view_sync(float(t0), float(t0) + 6.0, None, None, 1000, 200, scale)
        assert vd.stride == 1 and vd.complete
        peak_cache = max(peak_cache, an.cache_bytes())
        assert an.cache_bytes() <= limit
    st = an.stats()
    assert st["tiles_computed"] * tile_bytes > 4 * limit  # far more was analysed than kept
    assert st["tiles_evicted"] > 0
    assert peak_cache <= limit
    assert prov.max_len <= (TILE_FRAMES - 1) * cfg.hop + cfg.n_fft  # one tile at a time

    # whole-file overview: coarse stride, per-frame reads, small memory footprint
    prov.max_len = 0
    tracemalloc.start()
    try:
        ov = an.compute_view_sync(0.0, 1800.0, None, None, 1200, 200, scale)
        _cur, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert ov.stride == 128 and ov.complete
    assert prov.max_len == cfg.n_fft
    assert peak < 64 * 2**20
    assert an.cache_bytes() <= limit
    # honest LOD: each minute's column still shows that minute's real tone
    centres = row_centres_hz(scale, 200)[::-1]
    for minute in (0, 7, 29):
        c = int((minute * 60 + 30) / 1800 * 1200)
        f_peak = centres[int(np.argmax(ov.db[:, c]))]
        assert abs(f_peak - (500 + 100 * minute)) < 4000 / 200 + sr / cfg.n_fft


def test_settings_namespaces_are_cached_separately(analyzers):
    sr = 48000
    prov = ArrayProvider(sine(2000.0, 0.5, sr, 3.0))
    an = analyzers()
    an.set_audio(prov, prov.n, 1, sr, version=1)
    scale = FrequencyScale("linear", 0, sr / 2, sr / 2)
    a = DisplayAnalysisSettings(STFTConfig(4096, 1024, "hann"))
    b = DisplayAnalysisSettings(STFTConfig(1024, 256, "blackman"))
    an.set_settings(a)
    va = an.compute_view_sync(0, 3, None, None, 300, 100, scale)
    n_a = an.stats()["tiles_computed"]
    an.set_settings(b)
    vb = an.compute_view_sync(0, 3, None, None, 300, 100, scale)
    n_b = an.stats()["tiles_computed"]
    assert n_b > n_a and vb.settings == b
    an.set_settings(a)
    va2 = an.compute_view_sync(0, 3, None, None, 300, 100, scale)
    assert an.stats()["tiles_computed"] == n_b  # switching back is a cache hit
    assert np.array_equal(va.db, va2.db)
    assert an.choose_stride(0.3) == 1 and an.choose_stride(1.99) == 1
    assert an.choose_stride(2) == 2 and an.choose_stride(1000) == 512
    assert an.choose_stride(float("nan")) == 1


def test_outside_columns_and_no_audio(analyzers):
    an = analyzers()
    scale = FrequencyScale("linear", 0, 24000, 24000)
    vd = an.view_data(0, 1, None, None, 10, 5, scale)  # no audio bound yet
    assert (vd.status == TileStatus.OUTSIDE).all() and (vd.frame_index == -1).all()
    sr = 48000
    prov = ArrayProvider(sine(1000.0, 0.5, sr, 1.0))
    an.set_audio(prov, prov.n, 1, sr, version=1)
    vd = an.compute_view_sync(-1.0, 2.0, None, None, 300, 20, scale)
    tc = (np.arange(300) + 0.5) * 3.0 / 300 - 1.0
    inside = (tc >= 0) & (tc < 1.0)
    assert np.array_equal(vd.frame_index >= 0, inside)
    assert (vd.status[:, ~inside] == TileStatus.OUTSIDE).all()
    assert (vd.status[:, inside] == TileStatus.READY).all()
    assert (vd.db[:, ~inside] == DB_FLOOR).all()


# --------------------------------------------------------------------------- scales


def test_linear_scale_inverse_and_monotonic():
    s = FrequencyScale("linear", 100.0, 8000.0, 24000.0)
    f = np.linspace(100, 8000, 1001)
    u = s.to_unit(f)
    assert u[0] == pytest.approx(0) and u[-1] == pytest.approx(1)
    assert np.all(np.diff(u) > 0)
    assert np.allclose(s.from_unit(u), f, rtol=0, atol=1e-9)
    assert s.from_unit(0.0) == pytest.approx(100.0)
    assert not s.is_at_floor(0.0)
    assert FrequencyScale("linear", 0, 50000, 24000).f_hi == 24000  # clamped to Nyquist


def test_log_scale_inverse_floor_and_dc():
    s = FrequencyScale("log", 0.0, 24000.0, 24000.0)
    assert s.floor_hz == 20.0 and s.axis_lo == 20.0
    u = np.linspace(0, 1, 2001)
    f = s.from_unit(u)
    assert np.all(np.diff(f) > 0)
    assert np.allclose(s.to_unit(f), u, atol=1e-12)
    assert s.from_unit(0.0) == pytest.approx(20.0)
    assert s.from_unit(1.0) == pytest.approx(24000.0)
    # DC and sub-floor frequencies land on the bottom row
    assert s.to_unit(0.0) == 0.0 and s.to_unit(5.0) == 0.0 and s.to_unit(20.0) == pytest.approx(0.0)
    ff = np.concatenate([[0.0, 1.0, 10.0], np.geomspace(20, 24000, 500)])
    assert np.all(np.diff(s.to_unit(ff)) >= 0)  # monotonic (non-decreasing through the floor)
    assert s.to_unit(1000.0) == pytest.approx(np.log10(1000 / 20) / np.log10(24000 / 20))
    assert s.is_at_floor(0.0) and s.is_at_floor(5e-4) and not s.is_at_floor(0.01)
    assert list(s.is_at_floor(np.array([0.0, 0.5]))) == [True, False]
    # bottom row lumps everything from 0 Hz to the floor
    edges = row_edges_hz(s, 100)
    assert edges[0] == 0.0 and edges[1] > 20.0
    # floor never below one bin: n_fft 512 at 48 kHz -> 93.75 Hz
    s512 = FrequencyScale.for_analysis("log", 0, 24000, 48000, 512)
    assert s512.floor_hz == pytest.approx(93.75) and s512.from_unit(0) == pytest.approx(93.75)
    # a view starting above the floor is a plain log axis, no lumping
    s2 = FrequencyScale("log", 200.0, 2000.0, 24000.0)
    assert s2.to_unit(200.0) == pytest.approx(0) and s2.to_unit(100.0) < 0
    assert not s2.is_at_floor(0.0) and row_edges_hz(s2, 10)[0] == pytest.approx(200.0)
    with pytest.raises(ValueError):
        FrequencyScale("log", 0, 15, 24000)  # entirely below the floor
    with pytest.raises(ValueError):
        FrequencyScale("linear", 500, 400, 24000)
    with pytest.raises(ValueError):
        FrequencyScale("mel", 0, 1000, 24000)


def test_note_names_and_ticks():
    assert note_name(440.0) == "A4"
    assert note_name(554.37) == "C#5"
    assert note_name(261.63) == "C4"
    assert note_name(27.5) == "A0"
    assert note_name(446.0, cents=True) == "A4+23c"  # 1200*log2(446/440) = 23.4
    assert note_name(0.0) == "" and note_name(-5) == ""
    nt = note_ticks(250, 530)
    assert nt.labels[0] == "C4" and nt.labels[-1] == "C5" and len(nt.labels) == 13
    assert note_ticks(20, 20000, which="C").labels[:2] == ["C1", "C2"]
    assert "C#4" not in note_ticks(250, 530, which="naturals").labels


def test_ruler_ticks():
    t = nice_time_ticks(1.0, 1.5, 3)
    assert "1.250 s" not in t.labels and len(t.labels) <= 3
    t = nice_time_ticks(1.0, 1.5, 6)
    assert t.labels == ["1.000 s", "1.100 s", "1.200 s", "1.300 s", "1.400 s", "1.500 s"]
    t = nice_time_ticks(1.2, 1.3, 3)
    assert t.labels == ["1.200 s", "1.250 s", "1.300 s"]
    assert np.allclose(t.positions, [0, 0.5, 1])
    assert nice_time_ticks(0, 1800, 8).labels[1] == "5:00"
    lin = nice_freq_ticks(FrequencyScale("linear", 0, 24000, 24000), 6)
    assert lin.labels == ["0 Hz", "5 kHz", "10 kHz", "15 kHz", "20 kHz"]
    z = nice_freq_ticks(FrequencyScale("linear", 11000, 14000, 24000), 8)
    assert "12.5 kHz" in z.labels and "12 kHz" in z.labels
    lg = nice_freq_ticks(FrequencyScale("log", 0, 24000, 24000), 8)
    assert "1 kHz" in lg.labels and "100 Hz" in lg.labels and len(lg.labels) <= 8
    assert np.all(np.diff(lg.positions) > 0) and lg.positions.min() >= 0 and lg.positions.max() <= 1


# --------------------------------------------------------------------------- colour maps


def test_colormaps():
    assert {"ember", "ice", "grayscale", "cividis-like", "viridis-like"} <= set(COLORMAP_NAMES)
    assert {"cividis-like", "viridis-like"} <= COLORBLIND_FRIENDLY <= set(COLORMAP_NAMES)
    for name in COLORMAP_NAMES:
        lut = get_lut(name)
        assert lut.shape == (256, 3) and lut.dtype == np.uint8
        L = lightness(lut)
        assert np.all(np.diff(L) > -0.1), name  # perceptually ordered (within uint8 rounding)
        assert L[-1] - L[0] > 60
    ember = get_lut("ember")
    assert tuple(ember[0]) == (0, 0, 0) and ember[-1, 0] > 240 and ember[-1, 2] > 150  # pale yellow
    mid = ember[64]
    assert mid[2] > mid[1] and mid[0] > mid[1]  # purple segment
    ice = get_lut("ice")
    assert tuple(ice[0]) == (0, 0, 0) and tuple(ice[-1]) == (255, 255, 255) and ice[128, 2] > ice[128, 0]
    with pytest.raises(KeyError):
        get_lut("jet")


# --------------------------------------------------------------------------- image helpers


def test_colorize_range_gamma_and_lut():
    lut = get_lut("grayscale")
    db = np.array([[-100.0, -50.0, 0.0, 20.0, -200.0]], dtype=np.float32)
    st = np.zeros(db.shape, np.uint8)
    img = colorize(db, st, -100, 0, 1.0, lut)
    assert list(img[0, :, 0]) == [0, 128, 255, 255, 0]
    assert (img[..., 3] == 255).all()
    bright = colorize(db, st, -100, 0, 0.5, lut)
    assert bright[0, 1, 0] == round((0.5**0.5) * 255)
    assert colorize(db, st, -100, 0, 1.0, "grayscale").tobytes() == img.tobytes()
    rnd = np.random.default_rng(2).uniform(-130, 10, (50, 70)).astype(np.float32)
    for g in (0.3, 0.7, 1.0, 1.8, 3.0):
        exact = np.floor(np.clip((rnd.astype(np.float64) + 100) / 100, 0, 1) ** g * 255 + 0.5)
        got = colorize(rnd, np.zeros(rnd.shape, np.uint8), -100, 0, g, lut)[..., 0].astype(int)
        assert np.abs(got - exact).max() <= 1
    with pytest.raises(ValueError):
        colorize(db, st, 0, -10, 1.0, lut)
    with pytest.raises(ValueError):
        colorize(db, st, -100, 0, 0.0, lut)


def test_mask_overlay_straight_alpha():
    m = np.array([[0.0, 0.5, 1.0, 2.0]], dtype=np.float32)
    o = mask_overlay(m, (80, 200, 255), 0.4)
    assert o.shape == (1, 4, 4) and o.dtype == np.uint8
    assert (o[..., :3] == (80, 200, 255)).all()  # straight alpha: colour untouched
    assert list(o[0, :, 3]) == [0, 51, 102, 102]
    p = mask_overlay(m, (80, 200, 255), 0.4, premultiplied=True)
    assert tuple(p[0, 2, :3]) == (32, 80, 102)


@pytest.mark.parametrize("kind", ["linear", "log"])
def test_map_grid_to_view_lights_exactly_nearest_cells(kind):
    sr = 48000
    cfg = CFG_2048
    k0, b0 = 100, 10
    mask = np.zeros((60, 90), dtype=np.float32)  # frames 100..159, bins 10..99
    mask[17, 23] = 1.0  # frame 117, bin 33
    mask[40:43, 60:65] = 0.5
    scale = FrequencyScale.for_analysis(kind, 100.0, 2500.0, sr, cfg.n_fft)
    t0, t1, W, H = 1.0123, 1.8771, 517, 389
    out = map_grid_to_view(mask, k0, b0, t0, t1, W, scale, H, sr, cfg)
    assert out.shape == (H, W) and out.dtype == np.float32
    # independent check from pixel-centre geometry: a pixel is lit iff its centre lies in the
    # cell's time/frequency footprint (centre +- hop/2, centre +- bin/2)
    tc = t0 + (np.arange(W) + 0.5) * (t1 - t0) / W
    fc = row_centres_hz(scale, H)[::-1]
    in_t = np.abs(tc - 117 * cfg.hop / sr) < cfg.hop / sr / 2
    in_f = np.abs(fc - 33 * sr / cfg.n_fft) < sr / cfg.n_fft / 2
    expected = np.outer(in_f, in_t)
    assert expected.any()
    assert np.array_equal(out == 1.0, expected)
    in_t2 = (tc >= (140 - 0.5) * cfg.hop / sr) & (tc < (142 + 0.5) * cfg.hop / sr)
    in_f2 = (fc >= (70 - 0.5) * sr / cfg.n_fft) & (fc < (74 + 0.5) * sr / cfg.n_fft)
    assert np.array_equal(out == 0.5, np.outer(in_f2, in_t2))
    assert set(np.unique(out)) <= {0.0, 0.5, 1.0}


def test_overlay_columns_match_spectrogram_columns(analyzers):
    sr = 48000
    cfg = CFG_2048
    prov = ArrayProvider(0.01 * np.random.default_rng(7).standard_normal(sr * 4))
    an = analyzers()
    an.set_settings(DisplayAnalysisSettings(cfg))
    an.set_audio(prov, prov.n, 1, sr, version=1)
    scale = FrequencyScale("log", 0, sr / 2, sr / 2)
    vd = an.compute_view_sync(0.3, 1.9, None, None, 640, 300, scale)
    assert vd.stride == 1
    k, b = nearest_cells(0.3, 1.9, 640, scale, 300, sr, cfg)
    assert np.array_equal(vd.frame_index, k)
    assert b[0] > b[-1]  # rows top-first
