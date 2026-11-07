"""Numerical tests of the STFT, selection masks and spectral edits.

All signals here are synthetic TEST FIXTURES generated in code (sines and
seeded noise). They are not real-world recordings or benchmarks.
"""

import math

import numpy as np
import pytest

from spledio.dsp.spectral import apply_gain_to_region, gain_factor, selected_component
from spledio.dsp.stft import (
    DEFAULT_EDIT_CONFIG,
    SUPPORTED_FFT_SIZES,
    SUPPORTED_HOP_DIVISORS,
    SUPPORTED_WINDOWS,
    STFTConfig,
    STFTConfigError,
    istft,
    stft,
)
from spledio.selections.geometry import (
    BrushShape,
    EllipseShape,
    PolygonShape,
    RectShape,
    frequency_range,
    shape_from_dict,
    time_range,
)
from spledio.selections.selection import Selection, rasterize

SR = 48000


def tone(f, dur=4.0, amp=0.5, sr=SR, phase=0.0):
    t = np.arange(int(dur * sr)) / sr
    return amp * np.sin(2 * np.pi * f * t + phase)


def level(sig, f, a, b, sr=SR):
    """Amplitude of frequency f in sig[a:b] by complex demodulation (exact for bin-free sines over many cycles)."""
    tt = np.arange(a, b) / sr
    return 2 * abs(np.mean(sig[a:b] * np.exp(-2j * np.pi * f * tt)))


# ---------------------------------------------------------------- 3. STFT round trip
@pytest.mark.parametrize("n_fft", SUPPORTED_FFT_SIZES)
@pytest.mark.parametrize("div", SUPPORTED_HOP_DIVISORS)
@pytest.mark.parametrize("window", SUPPORTED_WINDOWS)
def test_stft_roundtrip_all_supported_configs(n_fft, div, window):
    rng = np.random.default_rng(n_fft + div)
    cfg = STFTConfig(n_fft, n_fft // div, window).validate()
    for L in (1, 777, 3 * n_fft + 5):
        x = rng.uniform(-1, 1, L)
        y = istft(stft(x, cfg), cfg, L)
        assert y.shape == x.shape  # exact intended output length
        assert np.max(np.abs(y - x)) <= 1e-6  # spec target; actual is ~1e-15 in float64
        assert np.max(np.abs(y - x)) < 1e-12


def test_invalid_configs_rejected():
    with pytest.raises(STFTConfigError):
        STFTConfig(4096, 3000, "hann").validate()
    with pytest.raises(STFTConfigError):
        STFTConfig(1000, 250, "hann").validate()
    with pytest.raises(STFTConfigError):
        STFTConfig(4096, 1024, "kaiser").validate()


def test_gain_factor_mute_is_exact_zero():
    assert gain_factor(-math.inf) == 0.0
    assert gain_factor(0.0) == 1.0
    assert abs(gain_factor(-6.0) - 0.501187) < 1e-6


# ---------------------------------------------------------------- 4. frequency-selective gain
def test_frequency_selective_gain_minus_6_db():
    x = (tone(440) + tone(3000, amp=0.3))[None, :]
    sel = Selection.of(RectShape(1.0, 3.0, 2800, 3200))
    comp = selected_component(x, sel, DEFAULT_EDIT_CONFIG, SR)
    y = x.copy()
    y[:, comp.start : comp.end] = apply_gain_to_region(x, comp, -6.0)
    # evaluate away from the transition regions (one window = 85 ms)
    a, b = int(1.2 * SR), int(2.8 * SR)
    ratio = level(y[0], 3000, a, b) / level(x[0], 3000, a, b)
    assert abs(20 * math.log10(ratio) - (-6.0)) < 0.05
    other = level(y[0], 440, a, b) / level(x[0], 440, a, b)
    assert abs(20 * math.log10(other)) < 0.01
    # outside the selection in time the 3 kHz tone is untouched
    a2, b2 = int(0.2 * SR), int(0.8 * SR)
    assert abs(level(y[0], 3000, a2, b2) - level(x[0], 3000, a2, b2)) < 1e-6
    # samples outside the processed support are bit-identical
    assert np.array_equal(y[:, : comp.start], x[:, : comp.start])
    assert np.array_equal(y[:, comp.end :], x[:, comp.end :])
    assert y.shape == x.shape


# ---------------------------------------------------------------- 5. spectral mute
def test_spectral_mute_suppresses_component_and_keeps_duration():
    x = (tone(500) + tone(5000, amp=0.4))[None, :]
    sel = Selection.of(RectShape(0.5, 3.5, 4500, 5500))
    comp = selected_component(x, sel, DEFAULT_EDIT_CONFIG, SR)
    y = x.copy()
    y[:, comp.start : comp.end] = apply_gain_to_region(x, comp, -math.inf)
    a, b = int(1.0 * SR), int(3.0 * SR)
    supp = level(y[0], 5000, a, b) / level(x[0], 5000, a, b)
    assert 20 * math.log10(supp + 1e-12) < -60  # deep suppression (window leakage limits "perfect")
    assert abs(level(y[0], 500, a, b) / level(x[0], 500, a, b) - 1) < 1e-3
    assert y.shape == x.shape


# ---------------------------------------------------------------- 6. full-band time gain
def test_full_band_time_gain_uses_exact_sample_path():
    rng = np.random.default_rng(3)
    x = rng.uniform(-0.5, 0.5, (2, SR * 2))
    sel = Selection.of(time_range(0.5, 1.0))
    comp = selected_component(x, sel, DEFAULT_EDIT_CONFIG, SR)
    assert comp.method == "time"
    assert (comp.start, comp.end) == (24000, 48000)
    y = x.copy()
    y[:, comp.start : comp.end] = apply_gain_to_region(x, comp, -6.0)
    g = 10 ** (-6 / 20)
    np.testing.assert_allclose(y[:, 24000:48000], x[:, 24000:48000] * g, rtol=0, atol=1e-15)
    assert np.array_equal(y[:, :24000], x[:, :24000])
    assert np.array_equal(y[:, 48000:], x[:, 48000:])


# ---------------------------------------------------------------- 7. extraction conservation
@pytest.mark.parametrize("feather", [(0.0, 0.0), (0.05, 80.0)])
@pytest.mark.parametrize(
    "shape",
    [
        RectShape(0.7, 1.9, 300, 2500),
        EllipseShape(1.2, 1200, 0.4, 600),
        PolygonShape(((0.5, 200.0), (1.5, 3000.0), (2.5, 400.0))),
        frequency_range(900, 1100),
    ],
)
def test_extraction_conservation(shape, feather):
    rng = np.random.default_rng(7)
    x = rng.normal(0, 0.2, (2, int(SR * 3)))
    sel = Selection.of(shape, feather_t=feather[0], feather_f=feather[1])
    comp = selected_component(x, sel, DEFAULT_EDIT_CONFIG, SR)
    extracted = np.zeros_like(x)
    extracted[:, comp.start : comp.end] = comp.data
    remainder = x - extracted
    assert np.max(np.abs(remainder + extracted - x)) < 1e-12
    assert np.max(np.abs(extracted)) > 0.01


# ---------------------------------------------------------------- selection model
def test_combination_rules_soft():
    t = np.linspace(0, 2, 201)[:, None]
    f = np.linspace(0, 4000, 101)[None, :]
    a = RectShape(0.0, 1.0, 0, 2000)
    b = RectShape(0.5, 1.5, 1000, 3000)
    ma = Selection.of(a, feather_t=0.1, feather_f=100).evaluate(t, f)
    mb = Selection.of(b, feather_t=0.1, feather_f=100).evaluate(t, f)
    base = Selection.of(a, feather_t=0.1, feather_f=100)
    np.testing.assert_allclose(base.combine("add", b).evaluate(t, f), np.maximum(ma, mb))
    np.testing.assert_allclose(base.combine("subtract", b).evaluate(t, f), np.minimum(ma, 1 - mb))
    np.testing.assert_allclose(base.combine("intersect", b).evaluate(t, f), np.minimum(ma, mb))
    np.testing.assert_allclose(base.inverted().evaluate(t, f), 1 - ma)
    for m in (ma, mb):
        assert m.min() >= 0 and m.max() <= 1


def test_feather_is_separate_from_geometry_and_can_be_disabled():
    s = Selection.of(RectShape(1.0, 2.0, 1000, 2000), feather_t=0.2, feather_f=200)
    t = np.array([[0.9]])
    f = np.array([[1500.0]])
    assert 0 < s.evaluate(t, f)[0, 0] < 1
    assert s.with_feather(enabled=False).evaluate(t, f)[0, 0] == 0
    assert s.with_feather(feather_t=0.0).evaluate(t, f)[0, 0] == 0
    assert s.ops == s.with_feather(feather_t=0.5).ops  # geometry unchanged


def test_selection_serialisation_roundtrip():
    s = Selection.of(EllipseShape(1, 1000, 0.2, 100), feather_t=0.01).combine(
        "add", PolygonShape(((0.0, 10.0), (1.0, 20.0), (0.5, 900.0)))
    ).combine("subtract", BrushShape(((0.2, 300.0), (0.4, 320.0)), 0.02, 40, 0.3)).inverted().with_channels((1,))
    s2 = Selection.from_dict(s.to_dict())
    assert s2 == s
    t = np.linspace(0, 2, 50)[:, None]
    f = np.linspace(0, 2000, 40)[None, :]
    np.testing.assert_array_equal(s.evaluate(t, f), s2.evaluate(t, f))
    with pytest.raises(ValueError):
        shape_from_dict({"kind": "evil"})


def test_raster_samples_frame_centres_and_bins():
    cfg = DEFAULT_EDIT_CONFIG
    s = Selection.of(RectShape(1.0, 1.5, 1000, 1200))
    r = rasterize(s, cfg, SR, SR * 3)
    times = np.arange(r.k0, r.k1) * cfg.hop / SR
    freqs = np.arange(r.b0, r.b1) * SR / cfg.n_fft
    inside = (times[:, None] >= 1.0) & (times[:, None] <= 1.5) & (freqs[None, :] >= 1000) & (freqs[None, :] <= 1200)
    np.testing.assert_array_equal(r.data > 0.5, inside)


# ---------------------------------------------------------------- 13. brush determinism
def test_brush_coverage_independent_of_event_rate_and_gap_free():
    t = np.linspace(0.0, 2.0, 400)[:, None]
    f = np.linspace(0, 4000, 200)[None, :]
    sparse = BrushShape(((0.2, 1000.0), (1.8, 3000.0)), rt=0.05, rf=150, hardness=0.6)
    dense_path = tuple((0.2 + 1.6 * u, 1000 + 2000 * u) for u in np.linspace(0, 1, 500))
    dense = BrushShape(dense_path, rt=0.05, rf=150, hardness=0.6)
    a = sparse.evaluate(t, f, 0, 0)
    b = dense.evaluate(t, f, 0, 0)
    assert np.max(np.abs(a - b)) < 0.05  # comparable results
    assert a.max() <= 1.0  # max-accumulation: never exceeds full coverage
    # no gaps: every point on the stroke centre line is fully covered
    for u in np.linspace(0, 1, 60):
        tt, ff = 0.2 + 1.6 * u, 1000 + 2000 * u
        v = sparse.evaluate(np.array([[tt]]), np.array([[ff]]), 0, 0)[0, 0]
        assert v > 0.99


# ---------------------------------------------------------------- 9. stereo
def test_linked_and_independent_channel_edits():
    x = np.vstack([tone(3000, amp=0.3) + tone(600, amp=0.3)] * 2)
    linked = Selection.of(RectShape(1.0, 3.0, 2800, 3200))
    c = selected_component(x, linked, DEFAULT_EDIT_CONFIG, SR)
    y = x.copy()
    y[:, c.start : c.end] = apply_gain_to_region(x, c, -12)
    assert np.array_equal(y[0], y[1])  # identical channels stay identical
    right_only = linked.with_channels((1,))
    c = selected_component(x, right_only, DEFAULT_EDIT_CONFIG, SR)
    y = x.copy()
    y[:, c.start : c.end] = apply_gain_to_region(x, c, -12)
    assert np.array_equal(y[0], x[0])  # left untouched
    assert not np.array_equal(y[1], x[1])


def test_resonance_ringing_for_part_of_a_long_range_is_found_with_its_span():
    """A ring present for 3 s of 8 s must rank above steady musical tones and report where it rings."""
    from spledio.dsp.resonance import suggest_resonances

    sr = 48000
    t = np.arange(8 * sr) / sr
    rng = np.random.default_rng(3)
    ring = ((t >= 1.5) & (t < 4.5)) * 0.12 * np.sin(2 * np.pi * 2370 * t)
    chord = sum(0.08 * np.sin(2 * np.pi * f * t) for f in (220, 330, 440))
    x = 0.02 * rng.normal(size=t.size) + chord + ring
    cands = suggest_resonances(x[None, :], sr, 0.0, 8.0)
    top = cands[0]
    assert abs(top.frequency_hz - 2370) < 10
    assert top.t_start == pytest.approx(1.5, abs=0.1) and top.t_end == pytest.approx(4.5, abs=0.1)
    assert top.persistence > 0.9
    steady = [c for c in cands if abs(c.frequency_hz - 440) < 10][0]
    assert steady.t_start < 0.1 and steady.t_end > 7.9
