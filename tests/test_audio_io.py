"""Tests for spledio.audio.io (probe / import / validation).

All audio files are synthetic TEST FIXTURES generated into pytest's tmp_path
by tests/fixtures_audio.py.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys

import numpy as np
import pytest
import soundfile as sf

from spledio.audio.io import (
    SUPPORTED_IMPORT_CONTAINERS,
    SUPPORTED_IMPORT_SUBTYPES,
    AudioFileInfo,
    AudioImportError,
    file_sha256,
    load_audio,
    probe_audio,
    read_block,
    storage_dtype_for,
)

try:
    from fixtures_audio import (
        PCM_BITS,
        canonical_wav_bytes,
        make_aiff,
        make_corrupt_files,
        make_fixture,
        make_flac,
        make_mono_wav,
        make_stereo_wav,
        reference_decode,
        synth_signal,
        write_fixture,
    )
except ImportError:  # tests/ imported as a package
    from tests.fixtures_audio import (
        PCM_BITS,
        canonical_wav_bytes,
        make_aiff,
        make_corrupt_files,
        make_fixture,
        make_flac,
        make_mono_wav,
        make_stereo_wav,
        reference_decode,
        synth_signal,
        write_fixture,
    )


def _snapshot(path):
    st = os.stat(path)
    return file_sha256(path), st.st_size, st.st_mtime_ns


# ----------------------------------------------------------------- capabilities


def test_supported_containers_are_the_requested_set_and_available():
    wanted = ("WAV", "WAVEX", "RF64", "W64", "FLAC", "AIFF", "CAF")
    available = sf.available_formats()
    assert SUPPORTED_IMPORT_CONTAINERS == tuple(c for c in wanted if c in available)
    # this build of libsndfile (1.2.2) provides all of them
    assert set(SUPPORTED_IMPORT_CONTAINERS) == set(wanted)
    assert "MP3" not in SUPPORTED_IMPORT_CONTAINERS and "OGG" not in SUPPORTED_IMPORT_CONTAINERS
    for s in ("PCM_16", "PCM_24", "PCM_32", "FLOAT", "DOUBLE"):
        assert s in SUPPORTED_IMPORT_SUBTYPES
    for s in ("IMA_ADPCM", "MS_ADPCM", "GSM610", "VORBIS", "OPUS", "MPEG_LAYER_III"):
        assert s not in SUPPORTED_IMPORT_SUBTYPES


# ----------------------------------------------------------------- properties retained


@pytest.mark.parametrize("subtype", ["PCM_16", "PCM_24", "PCM_32", "FLOAT"])
@pytest.mark.parametrize("channels", [1, 2])
@pytest.mark.parametrize("sample_rate", [22050, 44100, 48000, 96000])
def test_wav_import_keeps_rate_frames_channels(tmp_path, sample_rate, channels, subtype):
    frames = sample_rate // 5 + 7  # deliberately not a round number
    path = make_fixture(tmp_path, f"in_{subtype}.wav", subtype=subtype, channels=channels,
                        sample_rate=sample_rate, frames=frames)
    info = probe_audio(path)
    assert (info.format, info.subtype) == ("WAV", subtype)
    assert info.sample_rate == sample_rate
    assert info.channels == channels
    assert info.frames == frames
    assert info.duration_seconds == pytest.approx(frames / sample_rate)
    assert info.path == os.path.abspath(path)

    data, linfo = load_audio(path)
    assert linfo == info
    assert data.shape == (channels, frames)  # (channels, frames): no downmix, no resample
    assert data.flags.c_contiguous
    assert data.dtype == storage_dtype_for(info)
    np.testing.assert_array_equal(data, reference_decode(path, data.dtype))


@pytest.mark.parametrize(
    "fmt,subtype,channels,sample_rate",
    [
        ("FLAC", "PCM_16", 2, 44100),
        ("FLAC", "PCM_24", 1, 96000),
        ("FLAC", "PCM_24", 6, 48000),
        ("AIFF", "PCM_16", 2, 44100),
        ("AIFF", "PCM_24", 1, 22050),
        ("AIFF", "FLOAT", 2, 48000),
        ("AIFF", "PCM_S8", 1, 8000),
        ("WAVEX", "PCM_24", 6, 48000),
        ("RF64", "PCM_24", 2, 48000),
        ("W64", "FLOAT", 2, 44100),
        ("CAF", "PCM_24", 2, 48000),
        ("CAF", "ALAC_16", 2, 44100),
        ("CAF", "ALAC_24", 2, 48000),
        ("CAF", "ALAC_32", 1, 48000),
        ("WAV", "DOUBLE", 2, 48000),
        ("WAV", "PCM_U8", 1, 11025),
        ("WAV", "ULAW", 1, 8000),
        ("WAV", "ALAW", 1, 8000),
    ],
)
def test_other_containers_and_encodings(tmp_path, fmt, subtype, channels, sample_rate):
    if subtype not in SUPPORTED_IMPORT_SUBTYPES:
        pytest.skip(f"{subtype} not in this libsndfile build")
    # Even count: libsndfile's AIFF *writer* stores numSampleFrames + 1 for odd-length
    # 8-bit mono files (it counts the chunk pad byte), which would make the fixture lie.
    frames = 3000
    path = make_fixture(tmp_path, f"x.{fmt.lower()}", fmt=fmt, subtype=subtype, channels=channels,
                        sample_rate=sample_rate, frames=frames)
    data, info = load_audio(path)
    assert (info.format, info.subtype, info.channels, info.sample_rate, info.frames) == (
        fmt, subtype, channels, sample_rate, frames)
    assert data.shape == (channels, frames)
    assert data.dtype == storage_dtype_for(info)
    np.testing.assert_array_equal(data, reference_decode(path, data.dtype))


def test_mono_and_stereo_helpers(tmp_path):
    mono = load_audio(make_mono_wav(tmp_path))[0]
    stereo = load_audio(make_stereo_wav(tmp_path))[0]
    assert mono.shape == (1, 4410)
    assert stereo.shape == (2, 4800)
    assert not np.array_equal(stereo[0], stereo[1])  # channels kept separate
    assert load_audio(make_flac(tmp_path))[1].format == "FLAC"
    assert load_audio(make_aiff(tmp_path))[1].format == "AIFF"


# ----------------------------------------------------------------- dtype / exactness


@pytest.mark.parametrize(
    "subtype,expected",
    [("PCM_16", np.float32), ("PCM_24", np.float32), ("PCM_U8", np.float32), ("FLOAT", np.float32),
     ("PCM_32", np.float64), ("DOUBLE", np.float64), ("ALAC_32", np.float64), ("ALAC_24", np.float32)],
)
def test_storage_dtype_for(subtype, expected):
    info = AudioFileInfo("x.wav", "WAV", subtype, 48000, 2, 10)
    assert storage_dtype_for(info) == np.dtype(expected)
    assert storage_dtype_for(subtype) == np.dtype(expected)


@pytest.mark.parametrize("subtype", ["PCM_16", "PCM_24", "PCM_32"])
def test_integer_pcm_decodes_to_exact_codes(tmp_path, subtype):
    bits = PCM_BITS[subtype]
    path = make_fixture(tmp_path, "codes.wav", subtype=subtype, frames=2000, include_extremes=True)
    data, info = load_audio(path)
    codes = reference_decode(path, np.int32).astype(np.int64) >> (32 - bits)
    scaled = data.astype(np.float64) * 2.0 ** (bits - 1)
    np.testing.assert_array_equal(scaled, codes.astype(np.float64))  # exact, no rounding anywhere
    assert data.min() == -1.0  # most negative code
    assert data.max() == (2 ** (bits - 1) - 1) / 2 ** (bits - 1)  # most positive code


def test_float_file_values_beyond_full_scale_are_kept(tmp_path):
    x = synth_signal(2, 1000, 48000, amplitude=0.5)
    x[0, 10] = 1.75
    x[1, 20] = -3.0
    path = write_fixture(tmp_path / "hot.wav", x, 48000, subtype="FLOAT")
    data, _ = load_audio(path)
    np.testing.assert_array_equal(data, x.astype(np.float32))
    assert data[0, 10] == np.float32(1.75) and data[1, 20] == np.float32(-3.0)


def test_dtype_override_and_validation(tmp_path):
    path = make_stereo_wav(tmp_path, subtype="PCM_16")
    d32, _ = load_audio(path)
    d64, _ = load_audio(path, dtype=np.float64)
    assert d32.dtype == np.float32 and d64.dtype == np.float64
    np.testing.assert_array_equal(d64, d32.astype(np.float64))
    with pytest.raises(ValueError):
        load_audio(path, dtype=np.int16)
    with pytest.raises(ValueError):
        read_block(path, 0, 10, dtype=np.complex64)


# ----------------------------------------------------------------- header-based detection


def test_flac_bytes_with_wav_extension_is_reported_as_flac(tmp_path):
    flac = make_flac(tmp_path, "real.flac", subtype="PCM_24")
    fake = tmp_path / "actually_flac.wav"
    shutil.copyfile(flac, fake)
    info = probe_audio(fake)
    assert info.format == "FLAC" and info.subtype == "PCM_24"
    np.testing.assert_array_equal(load_audio(fake)[0], load_audio(flac)[0])


@pytest.mark.parametrize("fmt,ext", [("WAV", ".flac"), ("AIFF", ".wav"), ("CAF", ".aiff")])
def test_other_misnamed_files_are_detected_by_header(tmp_path, fmt, ext):
    src = make_fixture(tmp_path, "orig.bin", fmt=fmt, subtype="PCM_16")
    fake = tmp_path / f"misnamed{ext}"
    shutil.copyfile(src, fake)
    assert probe_audio(fake).format == fmt


def test_extension_fallbacks_of_libsndfile_are_not_used(tmp_path):
    # libsndfile opens unrecognised *.au / *.raw files by *name* as headerless audio;
    # Spledio hands it a file descriptor, so only the header counts.
    rng = np.random.default_rng(5)
    for name in ("garbage.au", "garbage.raw", "garbage.snd", "garbage.gsm"):
        p = tmp_path / name
        p.write_bytes(rng.integers(0, 256, 4096, dtype=np.uint8).tobytes())
        with pytest.raises(AudioImportError):
            probe_audio(p)


# ----------------------------------------------------------------- corrupt / invalid files


@pytest.mark.parametrize("kind", ["random_bytes", "truncated_header", "empty", "text", "truncated_flac"])
def test_corrupt_files_raise_import_error(tmp_path, kind):
    path = make_corrupt_files(tmp_path)[kind]
    with pytest.raises(AudioImportError) as ei:
        load_audio(path)
    assert ei.value.path == os.path.abspath(path)
    assert str(ei.value)  # user-facing message
    if kind != "truncated_flac":  # the FLAC header itself is intact
        with pytest.raises(AudioImportError):
            probe_audio(path)
    if kind == "empty":
        assert "empty" in str(ei.value)


def test_missing_file_and_directory(tmp_path):
    with pytest.raises(AudioImportError, match="not found") as ei:
        probe_audio(tmp_path / "nope.wav")
    assert ei.value.path == os.path.abspath(tmp_path / "nope.wav")
    with pytest.raises(AudioImportError, match="folder"):
        load_audio(tmp_path)
    with pytest.raises(AudioImportError):
        file_sha256(tmp_path / "nope.wav")


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX only")
def test_fifo_is_rejected_without_blocking(tmp_path):
    fifo = tmp_path / "pipe.wav"
    os.mkfifo(fifo)
    with pytest.raises(AudioImportError, match="regular file"):
        probe_audio(fifo)


def test_zero_frame_file(tmp_path):
    path = tmp_path / "zero.wav"
    sf.write(str(path), np.zeros((0, 2), dtype=np.int16), 48000, subtype="PCM_16")
    with pytest.raises(AudioImportError, match="no audio frames"):
        probe_audio(path)
    with pytest.raises(AudioImportError, match="no audio frames"):
        load_audio(path)


def test_truncated_data_returns_actual_frames(tmp_path):
    path = make_stereo_wav(tmp_path, "full.wav", frames=4000)
    full, _ = load_audio(path)
    cut = tmp_path / "cut.wav"
    cut.write_bytes(path.read_bytes()[: 44 + 1500 * 4 + 2])  # header claims 4000 frames
    data, info = load_audio(cut)
    assert info.frames == data.shape[1] == 1500
    np.testing.assert_array_equal(data, full[:, :1500])
    assert data.flags.c_contiguous


@pytest.mark.parametrize(
    "channels,rate,match",
    [(0, 44100, None), (2, 0, None), (2, 999, "sample rate"), (2, 1_000_000, "sample rate"),
     (65, 44100, "65 channels"), (2, 768_001, "sample rate")],
)
def test_absurd_header_values_are_rejected(tmp_path, channels, rate, match):
    path = tmp_path / "crafted.wav"
    path.write_bytes(canonical_wav_bytes(channels, rate))
    with pytest.raises(AudioImportError, match=match):
        probe_audio(path)


def test_crafted_header_sanity_check(tmp_path):
    path = tmp_path / "ok.wav"
    path.write_bytes(canonical_wav_bytes(2, 44100, frames=16))
    assert probe_audio(path).frames == 16


def test_many_channels_written_by_libsndfile(tmp_path):
    ok = write_fixture(tmp_path / "64.wav", np.zeros((64, 10)), 48000, fmt="WAVEX", subtype="PCM_16")
    assert load_audio(ok)[0].shape == (64, 10)
    bad = write_fixture(tmp_path / "65.wav", np.zeros((65, 10)), 48000, fmt="WAVEX", subtype="PCM_16")
    with pytest.raises(AudioImportError, match="65 channels"):
        load_audio(bad)


def test_unusual_rates_written_by_libsndfile(tmp_path):
    x = np.zeros((1, 100))
    with pytest.raises(AudioImportError, match="sample rate"):
        probe_audio(write_fixture(tmp_path / "500.wav", x, 500))
    with pytest.raises(AudioImportError, match="sample rate"):
        probe_audio(write_fixture(tmp_path / "2M.wav", x, 2_000_000))
    assert probe_audio(write_fixture(tmp_path / "1k.wav", x, 1000)).sample_rate == 1000
    assert probe_audio(write_fixture(tmp_path / "768k.wav", x, 768_000)).sample_rate == 768_000


@pytest.mark.parametrize("fmt,subtype,match", [
    ("OGG", "VORBIS", "compressed format OGG is not supported by the core importer yet"),
    ("OGG", "OPUS", "compressed format OGG is not supported by the core importer yet"),
    ("MP3", "MPEG_LAYER_III", "compressed format MP3 is not supported by the core importer yet"),
    ("WAV", "IMA_ADPCM", "compressed encoding IMA_ADPCM"),
    ("WAV", "GSM610", "compressed encoding GSM610"),
    ("AU", "PCM_16", "container is not supported"),
])
def test_unsupported_formats_have_clear_messages(tmp_path, fmt, subtype, match):
    path = tmp_path / f"enc.{fmt.lower()}"
    rate = 8000 if subtype == "GSM610" else 48000
    try:
        sf.write(str(path), synth_signal(1, 4800, rate).T * 0.5, rate, format=fmt, subtype=subtype)
    except (sf.LibsndfileError, RuntimeError, TypeError, ValueError) as e:
        pytest.skip(f"cannot write {fmt}/{subtype} fixture with this libsndfile: {e}")
    with pytest.raises(AudioImportError, match=match):
        probe_audio(path)
    with pytest.raises(AudioImportError, match=match):
        load_audio(path)


def test_non_finite_float_samples_are_rejected(tmp_path):
    x = synth_signal(2, 1000, 48000)
    x[1, 500] = np.nan
    x[0, 7] = np.inf
    path = write_fixture(tmp_path / "nan.wav", x, 48000, subtype="FLOAT")
    with pytest.raises(AudioImportError, match="non-finite"):
        load_audio(path)


# ----------------------------------------------------------------- read-only guarantee


def test_source_file_is_not_modified(tmp_path):
    paths = [
        make_stereo_wav(tmp_path, "a.wav", subtype="PCM_24"),
        make_flac(tmp_path, "b.flac"),
        make_aiff(tmp_path, "c.aiff"),
        make_fixture(tmp_path, "d.wav", subtype="FLOAT"),
    ]
    for p in paths:
        before = _snapshot(p)
        raw = p.read_bytes()
        probe_audio(p)
        load_audio(p)
        load_audio(p, dtype=np.float64)
        read_block(p, 100, 500)
        assert _snapshot(p) == before
        assert p.read_bytes() == raw


def test_read_only_file_can_be_imported(tmp_path):
    p = make_stereo_wav(tmp_path)
    os.chmod(p, 0o444)
    try:
        data, info = load_audio(p)
        assert data.shape == (2, info.frames)
    finally:
        os.chmod(p, 0o644)


# ----------------------------------------------------------------- block reader


def test_read_block_matches_full_decode(tmp_path):
    p = make_flac(tmp_path, "long.flac", subtype="PCM_24", frames=50_000)
    full, info = load_audio(p)
    for start, n in [(0, 1), (0, 1000), (12_345, 20_000), (49_000, 1000)]:
        blk = read_block(p, start, n)
        assert blk.shape == (2, n) and blk.dtype == np.float32 and blk.flags.c_contiguous
        np.testing.assert_array_equal(blk, full[:, start:start + n])
    tail = read_block(p, 49_900, 1000)
    assert tail.shape == (2, 100)
    np.testing.assert_array_equal(tail, full[:, 49_900:])
    assert read_block(p, 50_000, 10).shape == (2, 0)
    assert read_block(p, 10, 0).shape == (2, 0)
    assert read_block(p, 5, 10, dtype=np.float64).dtype == np.float64
    with pytest.raises(ValueError):
        read_block(p, -1, 10)
    with pytest.raises(ValueError):
        read_block(p, 0, -5)


def test_read_block_rejects_corrupt_file(tmp_path):
    with pytest.raises(AudioImportError):
        read_block(make_corrupt_files(tmp_path)["text"], 0, 10)


# ----------------------------------------------------------------- hashing / info serialisation


def test_file_sha256(tmp_path):
    p = make_stereo_wav(tmp_path)
    expected = hashlib.sha256(p.read_bytes()).hexdigest()
    assert file_sha256(p) == expected
    assert file_sha256(str(p), chunk=7) == expected
    with pytest.raises(ValueError):
        file_sha256(p, chunk=0)


def test_info_dict_round_trip(tmp_path):
    info = probe_audio(make_flac(tmp_path))
    d = info.to_dict()
    assert json.loads(json.dumps(d)) == d
    assert AudioFileInfo.from_dict(d) == info
    assert AudioFileInfo.from_dict({**d, "future_field": 1}) == info  # unknown keys ignored
    bad = dict(d)
    del bad["frames"]
    with pytest.raises(ValueError, match="frames"):
        AudioFileInfo.from_dict(bad)
    with pytest.raises(ValueError):
        AudioFileInfo.from_dict({**d, "sample_rate": "48000"})
    with pytest.raises(ValueError):
        AudioFileInfo.from_dict({**d, "channels": True})
    assert AudioFileInfo("p", "WAV", "PCM_16", 48000, 1, 24000).duration_seconds == 0.5


def test_unicode_path_with_apostrophe_and_spaces(tmp_path):
    folder = tmp_path / "Mike's 💾 Disk" / "täke one"
    folder.mkdir(parents=True)
    p = make_stereo_wav(folder, "söng 'final' 🎵.wav", subtype="PCM_24")
    data, info = load_audio(p)
    assert info.path == os.path.abspath(p)
    assert data.shape == (2, 4800)
    assert read_block(p, 0, 10).shape == (2, 10)
    assert file_sha256(p) == hashlib.sha256(p.read_bytes()).hexdigest()


def test_pathlike_bytes_and_str_paths(tmp_path):
    p = make_mono_wav(tmp_path)
    a = probe_audio(p)
    assert probe_audio(str(p)) == a
    assert probe_audio(os.fsencode(str(p))) == a
    with pytest.raises(AudioImportError):
        probe_audio(12.5)
