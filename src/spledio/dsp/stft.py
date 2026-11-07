"""Invertible short-time Fourier transform on a fixed global frame grid.

Conventions (shared by display analysis, selection rasterization and editing):

* Audio arrays are 1-D float arrays (one channel) of length ``L``.
* Frame ``k`` (any integer, may be negative) is centred on sample ``k * hop``
  and covers samples ``[k*hop - n_fft/2, k*hop + n_fft/2)``. Samples outside
  ``[0, L)`` are treated as zeros (zero padding). Frame centre time is
  ``k * hop / sample_rate`` seconds.
* Bin ``b`` (0..n_fft/2) has centre frequency ``b * sample_rate / n_fft`` Hz.
* The *full* frame range for a signal of length ``L`` is ``[k_min, k_max]``
  with every frame that overlaps ``[0, L)``. With the full range every sample
  is covered by the same set of window positions, so weighted overlap-add
  (WOLA) with analysis window ``w`` and synthesis window ``w``, divided by the
  periodic window-square sum ``P[n mod hop]``, reconstructs the input exactly
  (up to floating-point rounding).
* Because frames that are not computed contribute nothing to a *difference*
  signal, region-limited processing can transform only the frames that carry
  a non-zero change and add the inverse transform of that change to the
  untouched signal. Samples outside the support of the changed frames are
  never modified.

Only window/hop combinations listed in :data:`SUPPORTED_HOP_DIVISORS` are
accepted; each is validated numerically for the NOLA condition.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import numpy as np
import scipy.fft as sfft

SUPPORTED_FFT_SIZES = (512, 1024, 2048, 4096, 8192, 16384)
SUPPORTED_WINDOWS = ("hann", "hamming", "blackman")
# hop = n_fft // divisor
SUPPORTED_HOP_DIVISORS = (2, 4, 8)

_FFT_WORKERS = -1  # use all cores inside scipy.fft (releases the GIL)


class STFTConfigError(ValueError):
    pass


@dataclass(frozen=True)
class STFTConfig:
    """Analysis/synthesis configuration. Stored with every committed edit."""

    n_fft: int = 4096
    hop: int = 1024
    window: str = "hann"

    def validate(self) -> "STFTConfig":
        if self.n_fft not in SUPPORTED_FFT_SIZES:
            raise STFTConfigError(f"Unsupported FFT size {self.n_fft}; use one of {SUPPORTED_FFT_SIZES}")
        if self.window not in SUPPORTED_WINDOWS:
            raise STFTConfigError(f"Unsupported window {self.window!r}; use one of {SUPPORTED_WINDOWS}")
        if self.hop <= 0 or self.n_fft % self.hop or (self.n_fft // self.hop) not in SUPPORTED_HOP_DIVISORS:
            raise STFTConfigError(
                f"Unsupported hop {self.hop} for FFT size {self.n_fft}; hop must be n_fft/2, n_fft/4 or n_fft/8"
            )
        pattern = window_square_pattern(self.n_fft, self.hop, self.window)
        if pattern.min() < 1e-3 * pattern.max():
            raise STFTConfigError(f"{self.window} window with hop {self.hop} fails the NOLA condition")
        return self

    @property
    def n_bins(self) -> int:
        return self.n_fft // 2 + 1

    @property
    def half(self) -> int:
        return self.n_fft // 2

    def bin_frequencies(self, sample_rate: float) -> np.ndarray:
        return np.arange(self.n_bins) * (sample_rate / self.n_fft)

    def bin_spacing(self, sample_rate: float) -> float:
        return sample_rate / self.n_fft

    def frame_spacing(self, sample_rate: float) -> float:
        return self.hop / sample_rate

    def frame_time(self, k, sample_rate: float):
        return np.asarray(k) * (self.hop / sample_rate)

    def full_frame_range(self, length: int) -> tuple[int, int]:
        """Half-open range ``[k_start, k_stop)`` of all frames overlapping ``[0, length)``."""
        return full_frame_range(length, self.n_fft, self.hop)

    def frames_touching(self, start_sample: int, end_sample: int) -> tuple[int, int]:
        """Half-open frame range whose support overlaps samples ``[start, end)``."""
        h, half = self.hop, self.half
        k0 = (start_sample - half) // h + 1  # first k with k*h + half > start
        k1 = -((-(end_sample + half)) // h)  # first k with k*h - half >= end
        return int(k0), int(k1)

    def support_of_frames(self, k0: int, k1: int) -> tuple[int, int]:
        """Sample range ``[a, b)`` covered by frames ``[k0, k1)`` (unclipped)."""
        return k0 * self.hop - self.half, (k1 - 1) * self.hop + self.half

    def to_dict(self) -> dict:
        return {"n_fft": int(self.n_fft), "hop": int(self.hop), "window": str(self.window)}

    @classmethod
    def from_dict(cls, d: dict) -> "STFTConfig":
        return cls(n_fft=int(d["n_fft"]), hop=int(d["hop"]), window=str(d["window"])).validate()

    def describe(self) -> str:
        return f"{self.window} {self.n_fft}/{self.hop} ({100 - 100 * self.hop // self.n_fft}% overlap)"


DEFAULT_EDIT_CONFIG = STFTConfig(4096, 1024, "hann")


def full_frame_range(length: int, n_fft: int, hop: int) -> tuple[int, int]:
    half = n_fft // 2
    k_start = (-half) // hop + 1
    k_stop = -((-(length + half)) // hop)
    return int(k_start), int(max(k_stop, k_start + 1))


@lru_cache(maxsize=64)
def _window_cached(name: str, n: int) -> np.ndarray:
    m = np.arange(n, dtype=np.float64)
    phase = 2.0 * np.pi * m / n  # periodic (DFT-even) windows
    if name == "hann":
        w = 0.5 - 0.5 * np.cos(phase)
    elif name == "hamming":
        w = 0.54 - 0.46 * np.cos(phase)
    elif name == "blackman":
        w = 0.42 - 0.5 * np.cos(phase) + 0.08 * np.cos(2.0 * phase)
        w[w < 0] = 0.0
    else:
        raise STFTConfigError(f"Unknown window {name!r}")
    w.setflags(write=False)
    return w


def get_window(name: str, n: int) -> np.ndarray:
    """Periodic window of length ``n`` (read-only float64 array)."""
    return _window_cached(name, int(n))


@lru_cache(maxsize=64)
def _pattern_cached(n_fft: int, hop: int, window: str) -> np.ndarray:
    w2 = get_window(window, n_fft) ** 2
    p = w2.reshape(n_fft // hop, hop).sum(axis=0)
    p.setflags(write=False)
    return p


def window_square_pattern(n_fft: int, hop: int, window: str) -> np.ndarray:
    """``P[r] = sum_j w[r + j*hop]^2``; the WOLA normaliser for sample ``n`` is ``P[n mod hop]``."""
    return _pattern_cached(int(n_fft), int(hop), str(window))


def magnitude_reference(cfg: STFTConfig) -> float:
    """Magnitude of a full-scale (amplitude 1.0) stationary sinusoid at a bin centre."""
    return float(get_window(cfg.window, cfg.n_fft).sum() / 2.0)


def _segment(x: np.ndarray, a: int, b: int) -> np.ndarray:
    """``x[a:b]`` with zeros outside ``[0, len(x))`` (float64 copy)."""
    n = len(x)
    out = np.zeros(b - a, dtype=np.float64)
    lo, hi = max(a, 0), min(b, n)
    if hi > lo:
        out[lo - a : hi - a] = x[lo:hi]
    return out


def stft(x: np.ndarray, cfg: STFTConfig, k0: int | None = None, k1: int | None = None) -> np.ndarray:
    """Complex STFT of 1-D signal ``x`` for frames ``[k0, k1)``; shape ``(frames, n_bins)``.

    Defaults to the full frame range. Computation is float64/complex128.
    """
    x = np.asarray(x)
    if x.ndim != 1:
        raise ValueError("stft expects a 1-D signal")
    if k0 is None or k1 is None:
        fk0, fk1 = cfg.full_frame_range(len(x))
        k0 = fk0 if k0 is None else k0
        k1 = fk1 if k1 is None else k1
    if k1 <= k0:
        return np.zeros((0, cfg.n_bins), dtype=np.complex128)
    a, b = cfg.support_of_frames(k0, k1)
    seg = _segment(x, a, b)
    frames = np.lib.stride_tricks.sliding_window_view(seg, cfg.n_fft)[:: cfg.hop]
    assert frames.shape[0] == k1 - k0
    w = get_window(cfg.window, cfg.n_fft)
    return sfft.rfft(frames * w, axis=1, workers=_FFT_WORKERS)


def istft_segment(X: np.ndarray, cfg: STFTConfig, k0: int, length: int) -> tuple[int, np.ndarray]:
    """Weighted overlap-add of frames ``[k0, k0 + len(X))``.

    Returns ``(start, y)`` where ``y`` holds output samples ``[start, start+len(y))``
    clipped to ``[0, length)``. Normalisation uses the global periodic window-square
    pattern, which is exact when every frame not included in ``X`` is zero (e.g. a
    difference signal) or when ``X`` spans the full frame range.
    """
    n_frames = X.shape[0]
    if n_frames == 0:
        return 0, np.zeros(0, dtype=np.float64)
    n_fft, hop = cfg.n_fft, cfg.hop
    r = n_fft // hop
    w = get_window(cfg.window, n_fft)
    frames = sfft.irfft(X, n=n_fft, axis=1, workers=_FFT_WORKERS) * w
    blocks = frames.reshape(n_frames, r, hop)
    out = np.zeros((n_frames + r - 1, hop), dtype=np.float64)
    for j in range(r):
        out[j : j + n_frames] += blocks[:, j, :]
    y = out.reshape(-1)
    a, _b = cfg.support_of_frames(k0, k0 + n_frames)
    # normalise by P[n mod hop]; a is a multiple of hop because n_fft/2 is
    pattern = window_square_pattern(n_fft, hop, cfg.window)
    y = y / np.tile(pattern, n_frames + r - 1)
    lo, hi = max(a, 0), min(a + len(y), length)
    if hi <= lo:
        return max(a, 0), np.zeros(0, dtype=np.float64)
    return lo, y[lo - a : hi - a]


def istft(X: np.ndarray, cfg: STFTConfig, length: int, k0: int | None = None) -> np.ndarray:
    """Inverse of :func:`stft` over the full frame range, returning exactly ``length`` samples."""
    if k0 is None:
        k0 = cfg.full_frame_range(length)[0]
    start, seg = istft_segment(X, cfg, k0, length)
    out = np.zeros(length, dtype=np.float64)
    out[start : start + len(seg)] = seg
    return out
