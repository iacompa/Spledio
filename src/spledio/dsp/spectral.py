"""Phase-preserving spectral masking: the core of every manual edit.

For a selection mask ``M`` (soft, 0..1) on the edit STFT grid:

``component  c = ISTFT(M * X)``      complex multiply: magnitude AND phase kept
``gain edit  y = x + (G - 1) * c``   with ``G = 10**(gain_db/20)`` (0 for mute)

which is identical to ``ISTFT(A * X)`` with the amplitude-blend rule
``A = 1 + M * (G - 1)``. Only frames that carry a non-zero mask are
transformed; their inverse is added to the untouched signal, so samples
outside ``[k0*hop - n_fft/2, (k1-1)*hop + n_fft/2)`` are bit-identical.

A single full-band time span (no frequency limits) uses a direct
sample-domain envelope instead of the STFT: exact time boundaries and no
spectral smearing (``method == "time"``).

Spectral leakage/time smearing: a mask edge inside the STFT grid spreads its
effect over about one window length in time (n_fft samples) and a few bins
in frequency. Feathering reduces the audible edge; nothing here claims a
mathematically zero influence outside an infinitely sharp selection.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

import numpy as np

from spledio.dsp.stft import STFTConfig, istft_segment, stft
from spledio.selections.geometry import raised_cosine
from spledio.selections.selection import Selection, raster_extent, rasterize


_BLOCK = 1 << 16  # samples per block for sample-domain loops (keeps temporaries small)


class Cancelled(Exception):
    """Raised when a long render is cancelled; no state has been modified."""


class EmptySelectionError(ValueError):
    pass


def gain_factor(gain_db: float) -> float:
    """Linear amplitude factor; ``-inf`` dB (mute/delete) maps to exactly 0."""
    if gain_db is None or (isinstance(gain_db, float) and math.isinf(gain_db) and gain_db < 0):
        return 0.0
    return float(10.0 ** (float(gain_db) / 20.0))


@dataclass
class Component:
    """Selected component of a multichannel signal on samples ``[start, start+n)``."""

    start: int
    data: np.ndarray  # float64 (channels, n); rows outside the channel scope are zero
    method: str  # "stft" or "time"
    config: STFTConfig
    channels: tuple[int, ...]

    @property
    def end(self) -> int:
        return self.start + self.data.shape[1]


def resolve_channels(selection: Selection, n_channels: int) -> tuple[int, ...]:
    if selection.channels is None:
        return tuple(range(n_channels))
    chans = tuple(c for c in selection.channels if 0 <= c < n_channels)
    if not chans:
        raise EmptySelectionError("The selection's channel scope does not match this audio")
    return chans


def time_span_samples(t0: float, t1: float, sample_rate: float) -> tuple[int, int]:
    """Half-open hard sample range ``[round(t0*sr), round(t1*sr))`` for a time span."""
    return int(round(t0 * sample_rate)), int(round(t1 * sample_rate))


def time_envelope(t0: float, t1: float, feather_t: float, sample_rate: float, start: int, end: int) -> np.ndarray:
    """Sample-domain membership: 1 on ``[a, b)``, raised-cosine fall-off over
    ``feather_t`` seconds outside, 0 beyond."""
    a, b = time_span_samples(t0, t1, sample_rate)
    n = np.arange(start, end, dtype=np.float64)
    dist = np.maximum(np.maximum(a - n, n - (b - 1)), 0.0)
    if feather_t <= 0:
        return (dist == 0).astype(np.float64)
    return raised_cosine(dist / (feather_t * sample_rate))


def selected_component(
    audio: np.ndarray,
    selection: Selection,
    cfg: STFTConfig,
    sample_rate: float,
    *,
    chunk_frames: int = 384,
    cancel: Callable[[], bool] | None = None,
    allow_time_path: bool = True,
    dtype=np.float64,
) -> Component | None:
    """Compute ``ISTFT(M * X)`` (or the exact time-domain equivalent) for the
    selection over ``audio`` (channels, frames). Returns ``None`` if the
    selection covers no processing cells.

    The maths is float64 whatever ``dtype`` is. ``dtype=np.float32`` returns
    exactly ``float64 result.astype(float32)`` – the overlap-add runs in a
    float64 window that slides along with the chunks and each sample is
    rounded once, when it is final – without ever holding the float64
    component of the whole span (half the memory; used for adjusted areas,
    whose component is stored in the layer's float32 anyway).
    """
    audio = np.asarray(audio)
    if audio.ndim != 2:
        raise ValueError("audio must be (channels, frames)")
    n_ch, n = audio.shape
    chans = resolve_channels(selection, n_ch)
    if n == 0:
        return None
    dtype = np.dtype(dtype)

    span = selection.time_span_of_rect() if allow_time_path else None
    if span is not None:
        ft = selection.effective_feather[0]
        ha, hb = time_span_samples(span[0], span[1], sample_rate)
        fs = int(math.ceil(ft * sample_rate))
        a, b = max(0, ha - fs), min(n, hb + fs)
        if b <= a:
            return None

        def env(i: int, j: int) -> np.ndarray:  # envelope on [i, j); elementwise, so blocks match the whole
            return time_envelope(span[0], span[1], ft, sample_rate, i, j)

        first = last = None
        for i in range(a, b, _BLOCK):  # first sample with a non-zero envelope
            nz = np.flatnonzero(env(i, min(b, i + _BLOCK)) > 0)
            if nz.size:
                first = i + int(nz[0])
                break
        if first is None:
            return None
        for j in range(b, first, -_BLOCK):  # last one
            i = max(first, j - _BLOCK)
            nz = np.flatnonzero(env(i, j) > 0)
            if nz.size:
                last = i + int(nz[-1])
                break
        a, b = first, last + 1
        data = np.zeros((n_ch, b - a), dtype=dtype)
        for i in range(a, b, _BLOCK):
            j = min(b, i + _BLOCK)
            e = env(i, j)
            for c in chans:
                data[c, i - a : j - a] = audio[c, i:j].astype(np.float64) * e
        return Component(a, data, "time", cfg, chans)

    ext = raster_extent(selection, cfg, sample_rate, n)
    if ext is None:
        return None
    k0, k1 = ext[0], ext[1]
    sa, sb = cfg.support_of_frames(k0, k1)
    out_a, out_b = max(sa, 0), min(sb, n)
    data = np.zeros((n_ch, out_b - out_a), dtype=dtype)
    # Overlap-add window: float64 samples [acc_a, acc_a + width). A chunk starting at frame c0
    # only writes samples >= c0*hop - n_fft/2, so everything before that is final.
    width = chunk_frames * cfg.hop + cfg.n_fft
    acc = np.zeros((n_ch, width))
    acc_a = out_a

    def flush(upto: int) -> None:
        nonlocal acc_a
        m = min(upto, out_b) - acc_a
        if m <= 0:
            return
        assert m <= width
        data[:, acc_a - out_a : acc_a - out_a + m] = acc[:, :m]
        acc[:, : width - m] = acc[:, m:]
        acc[:, width - m :] = 0.0
        acc_a += m

    any_cell = False
    for c0 in range(k0, k1, chunk_frames):
        if cancel is not None and cancel():
            raise Cancelled()
        flush(c0 * cfg.hop - cfg.half)
        r = rasterize(selection, cfg, sample_rate, n, k_range=(c0, min(k1, c0 + chunk_frames)))
        if r is None:
            continue
        rows = np.nonzero(np.any(r.data > 0, axis=1))[0]
        if len(rows) == 0:
            continue
        any_cell = True
        r0, r1 = int(rows[0]), int(rows[-1]) + 1
        sub = r.data[r0:r1]
        kk0 = r.k0 + r0
        for c in chans:
            X = stft(audio[c], cfg, kk0, kk0 + sub.shape[0])
            D = np.zeros_like(X)
            D[:, r.b0 : r.b1] = X[:, r.b0 : r.b1] * sub
            s, y = istft_segment(D, cfg, kk0, n)
            acc[c, s - acc_a : s - acc_a + len(y)] += y
    if not any_cell:
        return None
    flush(out_b)
    return Component(out_a, data, "stft", cfg, chans)


def apply_gain_to_region(audio: np.ndarray, comp: Component, gain_db: float) -> np.ndarray:
    """New samples for ``[comp.start, comp.end)``: ``x + (G-1)*c`` (float64)."""
    g = gain_factor(gain_db)
    seg = audio[:, comp.start : comp.end].astype(np.float64)
    return seg + (g - 1.0) * comp.data


def add_scaled_in_place(audio: np.ndarray, start: int, channels, c: np.ndarray, d: float) -> None:
    """``audio[ch, start:start+n] = x + d·c`` for each ``ch`` in ``channels``, in place.

    ``c`` is ``(channels, n)``. Per sample this is exactly ``float64(x) + d·float64(c)``
    rounded once to ``audio``'s dtype – the same values as building the whole region in
    float64 (:func:`apply_gain_to_region` with ``d = G − 1``) and assigning it – but done in
    blocks, so no full-size temporary exists. ``d == 0`` changes nothing. The block buffers
    are allocated before the first sample is written."""
    if d == 0.0:
        return
    n = c.shape[1]
    k = min(n, _BLOCK)
    bx, bt = np.empty(k), np.empty(k)
    d = float(d)
    for ch in channels:
        row, crow = audio[ch, start : start + n], c[ch]
        for i in range(0, n, _BLOCK):
            j = min(n, i + _BLOCK)
            x, t = bx[: j - i], bt[: j - i]
            np.copyto(x, row[i:j])
            np.copyto(t, crow[i:j])
            np.multiply(t, d, out=t)
            np.add(x, t, out=x)
            np.copyto(row[i:j], x, casting="same_kind")


def coverage_gain_region(audio: np.ndarray, comp: Component, gain_db: float) -> np.ndarray:
    """Alias kept for brush strokes: the coverage mask *is* the selection."""
    return apply_gain_to_region(audio, comp, gain_db)
