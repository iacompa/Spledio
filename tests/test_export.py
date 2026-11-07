"""Tests for spledio.audio.export (clipping policy, dither, SRC, atomic write).

All audio here is synthetic TEST FIXTURES generated in memory or into
pytest's tmp_path by tests/fixtures_audio.py.
"""

from __future__ import annotations

import errno
import math
import os

import numpy as np
import pytest
import soundfile as sf

from spledio.audio.export import (
    CEILING_DBFS,
    EXPORT_FORMATS,
    LIMITER_LOOKAHEAD_S,
    LIMITER_RELEASE_DB_PER_S,
    ClippingError,
    ExportError,
    ExportResult,
    ExportSettings,
    OverwriteError,
    PeakReport,
    analyze_peaks,
    export_audio,
    limiter_gain,
    quantization_step,
    tpdf_dither,
)
from spledio.audio.io import load_audio

try:
    from fixtures_audio import make_fixture, reference_decode, sine, synth_signal, write_fixture
except ImportError:  # tests/ imported as a package
    from tests.fixtures_audio import make_fixture, reference_decode, sine, synth_signal, write_fixture

SR = 48000
CEIL = 10.0 ** (CEILING_DBFS / 20.0)
LSB16 = 2.0 ** -15
LSB24 = 2.0 ** -23


def partials(directory):
    return [n for n in os.listdir(directory) if ".partial" in n]


def boosted_program(tmp_path):
    """SYNTHETIC: 1 s stereo 16-bit sine program (on the 16-bit grid) with a x3 burst
    in frames 20000..26000 and one sample at exactly 1.5."""
    base = sine(440.0, SR, SR, amplitude=0.5, channels=2)
    base[1] *= 0.8
    src = write_fixture(tmp_path / "program.wav", base, SR, subtype="PCM_16")
    data, _ = load_audio(src)
    data = data.copy()
    data[:, 20000:26000] *= 3.0
    data[0, 23000] = 1.5
    return data


# ----------------------------------------------------------------- formats / helpers


def test_export_formats_verified_against_libsndfile():
    assert EXPORT_FORMATS == {"WAV": ("PCM_16", "PCM_24", "FLOAT"), "FLAC": ("PCM_16", "PCM_24")}
    for fmt, subtypes in EXPORT_FORMATS.items():
        for st in subtypes:
            assert sf.check_format(fmt, st)
    assert not sf.check_format("FLAC", "FLOAT")  # why FLAC has no float option


def test_quantization_step():
    assert quantization_step("PCM_16") == 2.0 ** -15
    assert quantization_step("pcm_24") == 2.0 ** -23
    assert quantization_step("PCM_32") == 2.0 ** -31
    assert quantization_step("FLOAT") is None
    assert quantization_step("DOUBLE") is None
    with pytest.raises(ExportError):
        quantization_step("VORBIS")


def test_analyze_peaks():
    x = np.zeros((3, 200_000), dtype=np.float32)
    x[0, 5] = -0.5
    x[1, 150_000] = 1.25  # beyond the first block
    x[1, 150_001] = -1.0  # exactly full scale is not "over"
    x[2, 7] = 1.0000001
    r = analyze_peaks(x)
    assert r.channel_peaks == (0.5, 1.25, float(np.float32(1.0000001)))
    assert r.peak == 1.25
    assert r.peak_dbfs == pytest.approx(20 * math.log10(1.25))
    assert r.samples_over == 2
    assert r.clips_integer
    quiet = analyze_peaks(np.zeros((2, 10)))
    assert quiet.peak == 0.0 and quiet.peak_dbfs == float("-inf") and not quiet.clips_integer
    assert not analyze_peaks(np.full((1, 4), -1.0)).clips_integer


@pytest.mark.parametrize("bad", [np.nan, np.inf, -np.inf])
def test_analyze_peaks_rejects_non_finite(bad):
    x = np.zeros((2, 100))
    x[1, 50] = bad
    with pytest.raises(ExportError, match="non-finite samples"):
        analyze_peaks(x)


def test_tpdf_dither_statistics():
    d = tpdf_dither((1_000_000,), np.random.default_rng(7))
    assert d.min() > -1.0 and d.max() < 1.0  # ±1 LSB
    assert abs(d.mean()) < 2e-3
    assert np.sqrt(np.mean(d ** 2)) == pytest.approx(1 / math.sqrt(6), rel=0.005)
    hist, _ = np.histogram(d, bins=4, range=(-1, 1))  # triangular: 1/8, 3/8, 3/8, 1/8
    np.testing.assert_allclose(hist / d.size, [0.125, 0.375, 0.375, 0.125], atol=0.003)


# ----------------------------------------------------------------- no-edit round trips


@pytest.mark.parametrize("src_subtype", ["FLOAT", "PCM_24", "PCM_16"])
def test_no_edit_float_export_is_bit_identical(tmp_path, src_subtype):
    src = make_fixture(tmp_path, "src.wav", subtype=src_subtype, sample_rate=44100, frames=30_001)
    data, info = load_audio(src)
    out = tmp_path / "out.wav"
    res = export_audio(data, info.sample_rate, out, ExportSettings(format="WAV", subtype="FLOAT"),
                       protected_paths=[src])
    back, binfo = load_audio(out)
    assert (binfo.format, binfo.subtype, binfo.sample_rate, binfo.channels, binfo.frames) == (
        "WAV", "FLOAT", 44100, 2, 30_001)
    assert np.max(np.abs(back.astype(np.float64) - data.astype(np.float64))) == 0.0
    np.testing.assert_array_equal(back, reference_decode(src))
    assert isinstance(res, ExportResult)
    assert (res.frames, res.sample_rate, res.channels, res.subtype, res.format) == (30_001, 44100, 2, "FLOAT", "WAV")
    assert res.path == os.path.abspath(out)
    assert res.dithered is False
    assert res.gain_applied_db == 0.0
    assert res.limited_samples == res.clipped_samples == res.saturated_samples == 0


@pytest.mark.parametrize("fmt,subtype", [("WAV", "PCM_24"), ("WAV", "PCM_16"), ("FLAC", "PCM_24"), ("FLAC", "PCM_16")])
def test_integer_round_trip_without_dither_is_exact(tmp_path, fmt, subtype):
    src = make_fixture(tmp_path, "src.wav", subtype=subtype, frames=20_000, include_extremes=True)
    data, info = load_audio(src)
    out = tmp_path / f"out.{fmt.lower()}"
    res = export_audio(data, info.sample_rate, out, ExportSettings(format=fmt, subtype=subtype, dither="none"))
    assert reference_decode(out, np.int32).tolist() == reference_decode(src, np.int32).tolist()
    back, binfo = load_audio(out)
    assert binfo.format == fmt and binfo.subtype == subtype
    np.testing.assert_array_equal(back, data)
    assert res.dithered is False
    assert res.saturated_samples == 0  # max code (32767/32768) is representable


def test_integer_export_with_tpdf_is_dithered_within_one_and_a_half_lsb(tmp_path):
    src = make_fixture(tmp_path, "src.wav", subtype="PCM_24", frames=20_000)
    data, _ = load_audio(src)
    data = data * 0.7  # an edited level: needs rounding to 24 bits
    res = export_audio(data, SR, tmp_path / "d.wav", ExportSettings(subtype="PCM_24", dither="tpdf", seed=1))
    back, _ = load_audio(tmp_path / "d.wav")
    diff = (back.astype(np.float64) - data) / LSB24
    assert res.dithered is True
    assert np.any(diff != 0)
    assert np.all(np.abs(diff) <= 1.5)  # rounding (0.5) + dither (< 1)


@pytest.mark.parametrize("src_subtype,out_subtype", [("PCM_16", "PCM_16"), ("PCM_16", "PCM_24"), ("PCM_24", "PCM_24")])
def test_unedited_integer_audio_is_not_dithered_when_no_requantization_is_needed(tmp_path, src_subtype, out_subtype):
    """TPDF dither is only needed when samples must be rounded. An unedited 16-bit file exported
    as 16-bit used to come back with about a quarter of its samples changed by one step."""
    src = make_fixture(tmp_path, "src.wav", subtype=src_subtype, frames=20_000)
    data, info = load_audio(src)
    out = tmp_path / "o.wav"
    res = export_audio(data, info.sample_rate, out, ExportSettings(subtype=out_subtype, dither="tpdf", seed=1))
    assert res.dithered is False
    np.testing.assert_array_equal(load_audio(out)[0], data)


def test_requantized_integer_audio_is_still_dithered(tmp_path):
    """A 24-bit file exported as 16-bit, or an edited 16-bit mix, still gets dither."""
    data, _ = load_audio(make_fixture(tmp_path, "s24.wav", subtype="PCM_24", frames=20_000))
    assert export_audio(data, SR, tmp_path / "a.wav", ExportSettings(subtype="PCM_16", seed=1)).dithered
    data16, _ = load_audio(make_fixture(tmp_path, "s16.wav", subtype="PCM_16", frames=20_000))
    assert export_audio(data16 * 0.7, SR, tmp_path / "b.wav", ExportSettings(subtype="PCM_16", seed=1)).dithered


def test_float64_data_is_written_as_float32(tmp_path):
    src = make_fixture(tmp_path, "src32.wav", subtype="PCM_32", frames=5000)
    data, info = load_audio(src)
    assert data.dtype == np.float64
    export_audio(data, SR, tmp_path / "f.wav", ExportSettings(subtype="FLOAT"))
    np.testing.assert_array_equal(load_audio(tmp_path / "f.wav")[0], data.astype(np.float32))


def test_lowercase_settings_are_normalised(tmp_path):
    x = synth_signal(1, 1000, SR, amplitude=0.3)
    res = export_audio(x, SR, tmp_path / "a.flac", ExportSettings(format="flac", subtype="pcm_16", dither="TPDF"))
    assert (res.format, res.subtype, res.dithered) == ("FLAC", "PCM_16", True)


# ----------------------------------------------------------------- clipping policies


def test_clip_policy_error_raises_with_peak_report(tmp_path):
    data = boosted_program(tmp_path)
    out = tmp_path / "clip.wav"
    with pytest.raises(ClippingError) as ei:
        export_audio(data, SR, out, ExportSettings(subtype="PCM_16", clip_policy="error"))
    rep = ei.value.report
    assert isinstance(rep, PeakReport)
    assert rep.peak == 1.5
    assert rep.peak_dbfs == pytest.approx(20 * math.log10(1.5))
    assert rep.samples_over == int(np.count_nonzero(np.abs(data) > 1.0)) > 0
    assert rep.clips_integer
    assert "dBFS" in str(ei.value)
    assert not out.exists() and not partials(tmp_path)


def test_float_export_keeps_overs(tmp_path):
    data = boosted_program(tmp_path)
    for policy in ("error", "reduce_level", "limit", "clip"):
        out = tmp_path / f"float_{policy}.wav"
        res = export_audio(data, SR, out, ExportSettings(subtype="FLOAT", clip_policy=policy))
        back, _ = load_audio(out)
        np.testing.assert_array_equal(back, data)  # never clipped, normalised or dithered
        assert back.max() == np.float32(1.5)
        assert res.peak_before.peak == 1.5
        assert res.gain_applied_db == 0.0 and res.limited_samples == 0 and res.clipped_samples == 0


@pytest.mark.parametrize("dither", ["none", "tpdf"])
def test_reduce_level(tmp_path, dither):
    data = boosted_program(tmp_path)
    out = tmp_path / "reduced.wav"
    res = export_audio(data, SR, out, ExportSettings(subtype="PCM_16", clip_policy="reduce_level", dither=dither, seed=3))
    back, _ = load_audio(out)
    peak = float(np.abs(back).max())
    assert CEIL - 3 * LSB16 <= peak <= CEIL  # ≈ -0.1 dBFS, never above
    assert 20 * math.log10(peak) == pytest.approx(CEILING_DBFS, abs=0.01)
    assert res.gain_applied_db == pytest.approx(20 * math.log10(CEIL / 1.5), abs=0.001)
    gain = 10 ** (res.gain_applied_db / 20)
    # rounding (0.5 LSB) plus, with TPDF, dither (< 1 LSB)
    tol = (1.5 if dither == "tpdf" else 0.5) * LSB16 + 1e-9
    err = np.abs(back.astype(np.float64) - data.astype(np.float64) * gain)
    assert err.max() <= tol  # one static gain, nothing else
    assert res.peak_before.peak == 1.5
    assert res.limited_samples == 0 and res.clipped_samples == 0 and res.saturated_samples == 0


@pytest.mark.parametrize("dither", ["none", "tpdf"])
def test_limit(tmp_path, dither):
    data = boosted_program(tmp_path)
    out = tmp_path / "limited.wav"
    res = export_audio(data, SR, out, ExportSettings(subtype="PCM_16", clip_policy="limit", dither=dither, seed=4))
    back, _ = load_audio(out)
    assert np.abs(back).max() <= CEIL  # never exceeds -0.1 dBFS
    assert np.abs(back).max() >= CEIL - 0.01  # but does not squash far below it
    assert res.limited_samples > 0 and res.gain_applied_db == 0.0 and res.clipped_samples == 0
    # quiet sections (before the look-ahead and after hold + release) are unchanged
    quiet = np.r_[0:19_900, 28_500:SR]
    if dither == "none":
        np.testing.assert_array_equal(back[:, quiet], data[:, quiet])
    else:
        assert np.max(np.abs(back[:, quiet] - data[:, quiet])) <= LSB16
    # inside the burst the gain is between the worst case and unity
    region = slice(20_000, 26_000)
    nz = np.abs(data[:, region]) > 0.05
    ratio = back[:, region][nz] / data[:, region][nz]
    assert ratio.min() > CEIL / 1.5 - 0.01 and ratio.max() < 1.0 + 2 * LSB16 / 0.05


def test_limiter_gain_properties():
    rng = np.random.default_rng(3)
    x = rng.uniform(-0.5, 0.5, (2, SR))  # bounded: overs only where placed below
    x[:, 10_000:10_100] *= 6
    x[1, 30_000] = 2.5
    ceiling = 0.9
    start, g = limiter_gain(x, ceiling, SR)
    full = np.ones(SR)
    full[start:start + g.size] = g
    assert np.all(np.abs(x) * full <= ceiling * (1 + 1e-12))
    assert 0 < full.min() and full.max() == 1.0
    hot = np.flatnonzero((np.abs(x) > ceiling).any(axis=0))
    look = round(LIMITER_LOOKAHEAD_S * SR)
    assert np.all(full[: hot[0] - look] == 1.0)
    assert np.all(full[-1000:] == 1.0)
    rate_ln = LIMITER_RELEASE_DB_PER_S / SR * math.log(10) / 20
    assert np.max(np.diff(np.log(full))) <= rate_ln * (1 + 1e-6)  # bounded release
    assert np.min(np.diff(full)) >= -(1 - full.min()) / (look + 1) - 1e-12  # ramped attack
    assert limiter_gain(np.full((1, 100), 0.5), 0.9, SR) == (0, None)


@pytest.mark.parametrize("dither", ["none", "tpdf"])
def test_clip_policy_clip(tmp_path, dither):
    data = boosted_program(tmp_path)
    out = tmp_path / "clipped.wav"
    res = export_audio(data, SR, out, ExportSettings(subtype="PCM_16", clip_policy="clip", dither=dither, seed=5))
    back, _ = load_audio(out)
    assert back.max() == (2 ** 15 - 1) / 2 ** 15 and back.min() == -1.0
    over = np.abs(data) > 1.0
    assert res.clipped_samples == int(over.sum()) == res.peak_before.samples_over
    assert res.saturated_samples > 0  # +1.0 is held at the largest code, and reported
    if dither == "none":
        np.testing.assert_array_equal(back[~over], data[~over])


@pytest.mark.parametrize("policy", ["error", "reduce_level", "limit", "clip"])
def test_no_policy_action_without_overs(tmp_path, policy):
    # peak 0.995 is above the -0.1 dBFS ceiling but not above full scale: nothing is changed
    src = write_fixture(tmp_path / "hot.wav", sine(440, 9600, SR, amplitude=0.995), SR, subtype="PCM_16")
    data, _ = load_audio(src)
    out = tmp_path / f"{policy}.wav"
    res = export_audio(data, SR, out, ExportSettings(subtype="PCM_16", clip_policy=policy, dither="none"))
    np.testing.assert_array_equal(load_audio(out)[0], data)
    assert res.gain_applied_db == 0.0 and res.limited_samples == 0 and res.clipped_samples == 0


def test_full_scale_plus_one_is_saturated_and_reported(tmp_path):
    x = np.array([[1.0, -1.0, 0.0, 0.5]])
    res = export_audio(x, SR, tmp_path / "fs.wav", ExportSettings(subtype="PCM_16", dither="none"))
    assert reference_decode(tmp_path / "fs.wav", np.int16).tolist() == [[32767, -32768, 0, 16384]]
    assert res.saturated_samples == 1 and not res.peak_before.clips_integer


# ----------------------------------------------------------------- dither


@pytest.mark.parametrize("subtype,lsb", [("PCM_16", LSB16), ("PCM_24", LSB24)])
def test_dither_error_level(tmp_path, subtype, lsb):
    x = sine(997.0, 2 * SR, SR, amplitude=0.25) + 0.001 * np.random.default_rng(0).standard_normal((1, 2 * SR))
    for dither, rms_lsb, max_lsb in [("tpdf", 0.5, 1.5), ("none", 1 / math.sqrt(12), 0.5)]:
        out = tmp_path / f"{subtype}_{dither}.wav"
        res = export_audio(x, SR, out, ExportSettings(subtype=subtype, dither=dither, seed=11))
        err = (load_audio(out)[0].astype(np.float64) - x) / lsb
        # TPDF: dither variance 1/6 + rounding 1/12 = 1/4 LSB^2, independent of the signal
        assert np.sqrt(np.mean(err ** 2)) == pytest.approx(rms_lsb, rel=0.03)
        assert abs(err.mean()) < 0.01
        assert np.abs(err).max() <= max_lsb + 1e-6
        assert res.dithered is (dither == "tpdf")


def test_dither_on_silence(tmp_path):
    z = np.zeros((2, SR))
    export_audio(z, SR, tmp_path / "none.wav", ExportSettings(subtype="PCM_16", dither="none"))
    assert not np.any(load_audio(tmp_path / "none.wav")[0])
    # digital silence is exactly representable: nothing to round, so no dither noise is added
    res = export_audio(z, SR, tmp_path / "tpdf.wav", ExportSettings(subtype="PCM_16", dither="tpdf", seed=2))
    assert res.dithered is False
    assert not np.any(reference_decode(tmp_path / "tpdf.wav", np.int16))
    # a tiny non-zero level needs rounding and is dithered (P(|tri + x| > 0.5) ~ 1/4 for x ~ 0)
    res = export_audio(z + 1e-9, SR, tmp_path / "tpdf2.wav", ExportSettings(subtype="PCM_16", dither="tpdf", seed=2))
    codes = reference_decode(tmp_path / "tpdf2.wav", np.int16)
    assert res.dithered is True
    assert set(np.unique(codes).tolist()) <= {-1, 0, 1}
    assert np.mean(codes != 0) == pytest.approx(0.25, abs=0.01)
    assert np.sqrt(np.mean(codes.astype(float) ** 2)) == pytest.approx(0.5, rel=0.03)


def test_dither_is_reproducible_with_seed(tmp_path):
    x = synth_signal(2, 20_000, SR, amplitude=0.3)
    a = export_audio(x, SR, tmp_path / "a.wav", ExportSettings(subtype="PCM_16", seed=42))
    export_audio(x, SR, tmp_path / "b.wav", ExportSettings(subtype="PCM_16", seed=42))
    export_audio(x, SR, tmp_path / "c.wav", ExportSettings(subtype="PCM_16", seed=43))
    assert a.dithered
    assert (tmp_path / "a.wav").read_bytes() == (tmp_path / "b.wav").read_bytes()
    assert not np.array_equal(load_audio(tmp_path / "a.wav")[0], load_audio(tmp_path / "c.wav")[0])


def test_no_dither_for_float(tmp_path):
    x = synth_signal(2, 5000, SR, amplitude=0.3)
    res = export_audio(x, SR, tmp_path / "f.wav", ExportSettings(subtype="FLOAT", dither="tpdf", seed=1))
    assert res.dithered is False
    np.testing.assert_array_equal(load_audio(tmp_path / "f.wav")[0], x.astype(np.float32))


# ----------------------------------------------------------------- overwrite / atomic write


def test_existing_file_is_not_overwritten_by_default(tmp_path):
    dest = tmp_path / "exists.wav"
    dest.write_bytes(b"precious")
    x = synth_signal(1, 1000, SR)
    with pytest.raises(OverwriteError):
        export_audio(x, SR, dest, ExportSettings())
    assert dest.read_bytes() == b"precious"
    assert not partials(tmp_path)
    export_audio(x, SR, dest, ExportSettings(overwrite=True))
    np.testing.assert_array_equal(load_audio(dest)[0], x.astype(np.float32))
    assert not partials(tmp_path)


def test_protected_source_is_refused_even_with_overwrite(tmp_path):
    src = make_fixture(tmp_path, "source.wav", subtype="PCM_24")
    raw = src.read_bytes()
    data, _ = load_audio(src)
    (tmp_path / "sub").mkdir()
    link = tmp_path / "link.wav"
    link.symlink_to(src)
    hard = tmp_path / "hard.wav"
    os.link(src, hard)
    for target in (src, str(src), tmp_path / "sub" / ".." / "source.wav", link, hard):
        with pytest.raises(ExportError, match="original source file") as ei:
            export_audio(data, SR, target, ExportSettings(subtype="FLOAT", overwrite=True), protected_paths=[src])
        assert not isinstance(ei.value, OverwriteError)
    with pytest.raises(ExportError, match="original source file"):  # a single path is accepted too
        export_audio(data, SR, src, ExportSettings(overwrite=True), protected_paths=src)
    assert src.read_bytes() == raw
    assert not partials(tmp_path)


def _existing_dest(tmp_path):
    dest = tmp_path / "keep.wav"
    dest.write_bytes(b"original destination bytes")
    return dest


def test_failed_write_leaves_destination_intact(tmp_path, monkeypatch):
    dest = _existing_dest(tmp_path)
    calls = {"n": 0}

    def failing_write(self, data):
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError("simulated disk full")
        return original(self, data)

    original = sf.SoundFile.write
    monkeypatch.setattr(sf.SoundFile, "write", failing_write)
    x = synth_signal(2, 300_000, SR, amplitude=0.3)  # several blocks
    with pytest.raises(ExportError, match="simulated disk full"):
        export_audio(x, SR, dest, ExportSettings(subtype="PCM_24", overwrite=True))
    assert calls["n"] == 2
    assert dest.read_bytes() == b"original destination bytes"
    assert not partials(tmp_path)
    new = tmp_path / "new.wav"
    with pytest.raises(ExportError):
        export_audio(x, SR, new, ExportSettings(subtype="PCM_24"))
    assert not new.exists() and not partials(tmp_path)


def test_failed_fsync_or_replace_leaves_destination_intact(tmp_path, monkeypatch):
    dest = _existing_dest(tmp_path)
    x = synth_signal(1, 1000, SR)

    def fail(*args, **kwargs):
        raise OSError(errno.EIO, "simulated I/O error")

    with monkeypatch.context() as m:
        m.setattr(os, "fsync", fail)
        with pytest.raises(ExportError, match="simulated I/O error"):
            export_audio(x, SR, dest, ExportSettings(overwrite=True))
    assert dest.read_bytes() == b"original destination bytes" and not partials(tmp_path)
    with monkeypatch.context() as m:
        m.setattr(os, "replace", fail)
        with pytest.raises(ExportError):
            export_audio(x, SR, dest, ExportSettings(overwrite=True))
    assert dest.read_bytes() == b"original destination bytes" and not partials(tmp_path)


def test_destination_appearing_during_export_is_not_clobbered(tmp_path, monkeypatch):
    dest = tmp_path / "race.wav"
    original = sf.SoundFile.write

    def racing_write(self, data):
        if not dest.exists():
            dest.write_bytes(b"written by someone else meanwhile")
        return original(self, data)

    monkeypatch.setattr(sf.SoundFile, "write", racing_write)
    with pytest.raises(OverwriteError):
        export_audio(synth_signal(1, 1000, SR), SR, dest, ExportSettings())
    assert dest.read_bytes() == b"written by someone else meanwhile"
    assert not partials(tmp_path)


def test_filesystem_without_hard_links(tmp_path, monkeypatch):
    def no_links(*a, **k):
        raise OSError(errno.ENOTSUP, "hard links not supported")

    monkeypatch.setattr(os, "link", no_links)
    x = synth_signal(1, 1000, SR)
    dest = tmp_path / "fat.wav"
    export_audio(x, SR, dest, ExportSettings())
    assert load_audio(dest)[0].shape == (1, 1000)
    with pytest.raises(OverwriteError):
        export_audio(x, SR, dest, ExportSettings())
    assert not partials(tmp_path)


def test_new_file_permissions_follow_umask(tmp_path):
    umask = os.umask(0)
    os.umask(umask)
    export_audio(synth_signal(1, 100, SR), SR, tmp_path / "perm.wav", ExportSettings())
    assert os.stat(tmp_path / "perm.wav").st_mode & 0o777 == 0o666 & ~umask


def test_unicode_destination(tmp_path):
    folder = tmp_path / "Mike's 💾 exports"
    folder.mkdir()
    dest = folder / "mix 'v2' ünïcode 🎛.flac"
    res = export_audio(synth_signal(2, 2000, SR, amplitude=0.3), SR, dest,
                       ExportSettings(format="FLAC", subtype="PCM_24", seed=0))
    assert res.path == os.path.abspath(dest)
    assert load_audio(dest)[1].format == "FLAC"
    assert not partials(folder)


# ----------------------------------------------------------------- validation


@pytest.mark.parametrize(
    "data,match",
    [
        (np.zeros(100), "2-D"),
        (np.zeros((1, 2, 3)), "2-D"),
        (np.zeros((2, 100), dtype=np.int16), "floating point"),
        (np.zeros((2, 0)), "no audio frames"),
        (np.zeros((0, 100)), "channels"),
        (np.zeros((65, 100)), "65 channels"),
        (np.zeros((1000, 2)), "frames, channels"),
        (np.array([[0.0, np.nan]]), "non-finite samples"),
        (np.array([[0.0, np.inf]]), "non-finite samples"),
    ],
)
def test_invalid_data_is_rejected(tmp_path, data, match):
    with pytest.raises(ExportError, match=match):
        export_audio(data, SR, tmp_path / "x.wav", ExportSettings())
    assert os.listdir(tmp_path) == []


@pytest.mark.parametrize(
    "settings,match",
    [
        (ExportSettings(format="MP3"), "format"),
        (ExportSettings(format="FLAC", subtype="FLOAT"), "FLAC export does not support"),
        (ExportSettings(format="WAV", subtype="PCM_32"), "does not support"),
        (ExportSettings(dither="rpdf"), "dither"),
        (ExportSettings(clip_policy="soft"), "clip policy"),
        (ExportSettings(sample_rate=0), "positive"),
        (ExportSettings(sample_rate=-44100), "positive"),
        (ExportSettings(sample_rate=44100.5), "integer"),
        (ExportSettings(sample_rate=True), "integer"),
        (ExportSettings(sample_rate=999), "range"),
        (ExportSettings(overwrite="yes"), "overwrite"),
        (ExportSettings(seed=-1), "seed"),
    ],
)
def test_invalid_settings_are_rejected(tmp_path, settings, match):
    with pytest.raises(ExportError, match=match):
        export_audio(np.zeros((1, 10)), SR, tmp_path / "x.wav", settings)
    assert os.listdir(tmp_path) == []


@pytest.mark.parametrize("rate", [0, -1, 48000.0, True, 100])
def test_invalid_project_rate(tmp_path, rate):
    with pytest.raises(ExportError):
        export_audio(np.zeros((1, 10)), rate, tmp_path / "x.wav", ExportSettings())


def test_bad_destinations(tmp_path):
    x = np.zeros((1, 10))
    with pytest.raises(ExportError, match="does not exist"):
        export_audio(x, SR, tmp_path / "missing" / "x.wav", ExportSettings())
    with pytest.raises(ExportError, match="folder"):
        export_audio(x, SR, tmp_path, ExportSettings(overwrite=True))
    with pytest.raises(ExportError):
        export_audio(x, SR, None, ExportSettings())


def test_container_limits(tmp_path):
    with pytest.raises(ExportError, match="at most 8 channels"):
        export_audio(np.zeros((9, 100)), SR, tmp_path / "nine.flac", ExportSettings(format="FLAC", subtype="PCM_16"))
    with pytest.raises(ExportError, match="655350"):
        export_audio(np.zeros((1, 100)), 768_000, tmp_path / "hi.flac", ExportSettings(format="FLAC", subtype="PCM_16"))
    huge = np.broadcast_to(np.zeros((1, 1), dtype=np.float32), (64, 20_000_000))  # 5 GB of FLOAT, no memory used
    with pytest.raises(ExportError, match="4 GiB"):
        export_audio(huge, SR, tmp_path / "huge.wav", ExportSettings(subtype="FLOAT"))
    assert os.listdir(tmp_path) == []
    # 8-channel FLAC and 64-channel WAV are fine
    export_audio(np.zeros((8, 100)), SR, tmp_path / "eight.flac", ExportSettings(format="FLAC", subtype="PCM_16"))
    export_audio(np.zeros((64, 100)), SR, tmp_path / "sixtyfour.wav", ExportSettings(subtype="PCM_24"))
    assert load_audio(tmp_path / "sixtyfour.wav")[0].shape == (64, 100)


# ----------------------------------------------------------------- sample-rate conversion


def test_resample_48k_to_44k1_keeps_1khz_tone(tmp_path):
    x = sine(1000.0, SR, SR, amplitude=0.5)
    out = tmp_path / "44k1.wav"
    res = export_audio(x, SR, out, ExportSettings(subtype="FLOAT", sample_rate=44100))
    back, info = load_audio(out)
    assert res.frames == info.frames == 44100
    assert res.sample_rate == info.sample_rate == 44100
    y = back[0].astype(np.float64)
    spectrum = np.abs(np.fft.rfft(y * np.hanning(y.size)))
    assert np.argmax(spectrum) * 44100 / y.size == 1000.0
    ideal = 0.5 * np.sin(2 * np.pi * 1000.0 * np.arange(44100) / 44100)
    mid = slice(2000, 42100)  # away from the zero-padded edges
    assert np.max(np.abs(y[mid] - ideal[mid])) < 1e-4  # ~ -80 dB: passband flat, no aliasing
    zero_crossings = np.count_nonzero(np.diff(np.signbit(y[mid])))
    assert zero_crossings / ((mid.stop - mid.start) / 44100) == pytest.approx(2000, rel=0.002)


@pytest.mark.parametrize("src,dst,n_in,n_out", [(48000, 44100, 48001, 44101), (44100, 48000, 44100, 48000),
                                                (96000, 44100, 9600, 4410), (22050, 48000, 2205, 4800)])
def test_resample_frame_counts(tmp_path, src, dst, n_in, n_out):
    x = synth_signal(2, n_in, src, amplitude=0.3)
    res = export_audio(x, src, tmp_path / "r.wav", ExportSettings(subtype="PCM_24", sample_rate=dst, seed=0))
    back, info = load_audio(tmp_path / "r.wav")
    assert res.frames == info.frames == back.shape[1] == n_out
    assert info.sample_rate == dst and info.channels == 2


def test_same_rate_means_no_conversion(tmp_path):
    x = synth_signal(2, 5000, SR, amplitude=0.3).astype(np.float32)
    export_audio(x, SR, tmp_path / "same.wav", ExportSettings(subtype="FLOAT", sample_rate=SR))
    np.testing.assert_array_equal(load_audio(tmp_path / "same.wav")[0], x)


def test_peaks_are_measured_after_conversion(tmp_path):
    # a naive square wave at exactly ±1.0 overshoots (Gibbs) once band-limited by the SRC filter
    t = np.arange(SR) / SR
    x = np.where(np.sin(2 * np.pi * 1000 * t) >= 0, 1.0, -1.0)[None, :]
    assert not analyze_peaks(x).clips_integer
    with pytest.raises(ClippingError) as ei:
        export_audio(x, SR, tmp_path / "sq.wav", ExportSettings(subtype="PCM_16", sample_rate=44100))
    assert ei.value.report.peak > 1.0
    res = export_audio(x, SR, tmp_path / "sq.wav", ExportSettings(subtype="PCM_16", sample_rate=44100,
                                                                 clip_policy="limit", seed=0))
    assert res.peak_before.peak > 1.0 and res.limited_samples > 0
    assert np.abs(load_audio(tmp_path / "sq.wav")[0]).max() <= CEIL


def test_unreasonable_rate_ratio_is_refused(tmp_path):
    with pytest.raises(ExportError, match="too complex"):
        export_audio(np.zeros((1, 100)), 44100, tmp_path / "x.wav", ExportSettings(sample_rate=44101))
