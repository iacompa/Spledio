"""SYNTHETIC TEST FIXTURES for the audio import/export tests.

Everything here *generates* audio (sines plus seeded noise) and writes it into
a pytest ``tmp_path``. None of it is real recorded material, and none of it is
used by the application – it exists only so the tests can exercise real
libsndfile files (WAV, FLAC, AIFF, CAF, ...) with known content.

Integer PCM fixtures are written from exact integer codes (soundfile receives
int32 samples whose top ``bits`` bits are the code), optionally including the
most negative and most positive code, so the tests can check bit-exact
decoding. Float fixtures are written as float32/float64 values and may
deliberately contain values beyond ±1.0.
"""

from __future__ import annotations

import os
import struct
from pathlib import Path

import numpy as np
import soundfile as sf

PCM_BITS = {"PCM_S8": 8, "PCM_U8": 8, "PCM_16": 16, "PCM_24": 24, "PCM_32": 32}


def synth_signal(
    channels: int,
    frames: int,
    sample_rate: int,
    *,
    amplitude: float = 0.5,
    noise: float = 0.02,
    seed: int = 0,
) -> np.ndarray:
    """SYNTHETIC: (channels, frames) float64 – a different sine per channel plus seeded noise."""
    rng = np.random.default_rng(seed)
    t = np.arange(frames) / sample_rate
    out = np.empty((channels, frames), dtype=np.float64)
    for c in range(channels):
        f = 220.0 * (1.0 + 0.37 * c)  # distinct per channel, so a downmix would be detectable
        out[c] = amplitude * np.sin(2 * np.pi * f * t + 0.3 * c) + noise * rng.standard_normal(frames)
    return out


def sine(frequency: float, frames: int, sample_rate: int, amplitude: float = 0.5, channels: int = 1) -> np.ndarray:
    """SYNTHETIC: pure sine, (channels, frames) float64, identical in every channel."""
    t = np.arange(frames) / sample_rate
    return np.tile(amplitude * np.sin(2 * np.pi * frequency * t), (channels, 1))


def pcm_codes(x: np.ndarray, bits: int) -> np.ndarray:
    """Round float data to integer PCM codes of ``bits`` (clipped to the code range), int64."""
    scale = 2 ** (bits - 1)
    return np.clip(np.rint(np.asarray(x, dtype=np.float64) * scale), -scale, scale - 1).astype(np.int64)


def write_fixture(
    path: os.PathLike | str,
    data: np.ndarray,
    sample_rate: int,
    *,
    fmt: str = "WAV",
    subtype: str = "PCM_16",
    include_extremes: bool = False,
) -> Path:
    """SYNTHETIC: write (channels, frames) ``data`` with libsndfile and return the path.

    For integer PCM subtypes the data is first rounded to exact codes; with
    ``include_extremes`` the first two frames of every channel are set to the
    most negative and most positive code.
    """
    path = Path(path)
    data = np.asarray(data, dtype=np.float64)
    bits = PCM_BITS.get(subtype)
    if bits is not None:
        codes = pcm_codes(data, bits)
        if include_extremes and codes.shape[1] >= 2:
            codes[:, 0] = -(2 ** (bits - 1))
            codes[:, 1] = 2 ** (bits - 1) - 1
        frames_first = np.ascontiguousarray((codes << (32 - bits)).astype(np.int32).T)
    elif subtype == "FLOAT":
        frames_first = np.ascontiguousarray(data.astype(np.float32).T)
    else:  # DOUBLE, ULAW, ALAW, ALAC_*: let libsndfile encode float64 input
        frames_first = np.ascontiguousarray(data.T)
    sf.write(str(path), frames_first, sample_rate, format=fmt, subtype=subtype)
    return path


def make_fixture(
    directory: os.PathLike | str,
    name: str,
    *,
    fmt: str = "WAV",
    subtype: str = "PCM_16",
    channels: int = 2,
    sample_rate: int = 48000,
    frames: int = 4800,
    amplitude: float = 0.5,
    seed: int = 0,
    include_extremes: bool = True,
) -> Path:
    """SYNTHETIC: generate :func:`synth_signal` and write it as ``directory/name``."""
    data = synth_signal(channels, frames, sample_rate, amplitude=amplitude, seed=seed)
    return write_fixture(
        Path(directory) / name, data, sample_rate, fmt=fmt, subtype=subtype, include_extremes=include_extremes
    )


def make_mono_wav(directory, name="mono.wav", subtype="PCM_16", sample_rate=44100, frames=4410, seed=0) -> Path:
    return make_fixture(directory, name, subtype=subtype, channels=1, sample_rate=sample_rate, frames=frames, seed=seed)


def make_stereo_wav(directory, name="stereo.wav", subtype="PCM_16", sample_rate=48000, frames=4800, seed=0) -> Path:
    return make_fixture(directory, name, subtype=subtype, channels=2, sample_rate=sample_rate, frames=frames, seed=seed)


def make_flac(directory, name="fixture.flac", subtype="PCM_16", channels=2, sample_rate=48000, frames=4800) -> Path:
    return make_fixture(
        directory, name, fmt="FLAC", subtype=subtype, channels=channels, sample_rate=sample_rate, frames=frames
    )


def make_aiff(directory, name="fixture.aiff", subtype="PCM_16", channels=2, sample_rate=44100, frames=4410) -> Path:
    return make_fixture(
        directory, name, fmt="AIFF", subtype=subtype, channels=channels, sample_rate=sample_rate, frames=frames
    )


def reference_decode(path: os.PathLike | str, dtype=np.float32) -> np.ndarray:
    """Decode with soundfile directly (independent of Spledio), as (channels, frames)."""
    data, _ = sf.read(str(path), dtype=dtype, always_2d=True)
    return np.ascontiguousarray(data.T)


def canonical_wav_bytes(channels: int, sample_rate: int, bits: int = 16, frames: int = 16, fmt_tag: int = 1) -> bytes:
    """SYNTHETIC: hand-built 44-byte-header PCM WAV (lets tests craft absurd header values)."""
    block = channels * bits // 8
    payload = b"\0" * (frames * max(block, 1))
    fmt_chunk = struct.pack("<HHIIHH", fmt_tag, channels, sample_rate, sample_rate * block, block, bits)
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt_chunk)) + fmt_chunk
    body += b"data" + struct.pack("<I", len(payload)) + payload
    return b"RIFF" + struct.pack("<I", len(body)) + body


def make_corrupt_files(directory: os.PathLike | str) -> dict[str, Path]:
    """SYNTHETIC: files that must be rejected – random bytes, truncated header, empty, text."""
    d = Path(directory)
    rng = np.random.default_rng(1234)
    files = {}
    files["random_bytes"] = d / "random.wav"
    files["random_bytes"].write_bytes(rng.integers(0, 256, 8192, dtype=np.uint8).tobytes())
    valid = make_stereo_wav(d, "valid_for_truncation.wav")
    files["truncated_header"] = d / "truncated_header.wav"
    files["truncated_header"].write_bytes(valid.read_bytes()[:20])
    files["empty"] = d / "empty.wav"
    files["empty"].write_bytes(b"")
    files["text"] = d / "notes.wav"
    files["text"].write_text("This is a text file renamed to .wav, not audio.\n" * 40, encoding="utf-8")
    flac = make_flac(d, "valid_for_truncation.flac", frames=48000)
    files["truncated_flac"] = d / "truncated.flac"
    files["truncated_flac"].write_bytes(flac.read_bytes()[: len(flac.read_bytes()) // 2])
    return files
