"""Local resonance suggestions (optional helper, never applied automatically).

Method (documented, deliberately simple):

1. Analyse only the chosen time/frequency region with the edit STFT.
2. For every bin, take the *median* power over the region's frames
   (persistent energy; short transients do not dominate). For regions longer
   than two ``SEGMENT_SECONDS`` the median is taken per half-overlapping
   segment and the **highest segment median** is used ("sustained level"), so
   a resonance that rings for only part of a long region is still found.
3. Estimate a local spectral baseline with a moving median over a window of
   ``baseline_octaves`` octaves around each bin (in log-frequency).
4. Prominence = 10*log10(median power / baseline). Peaks above
   ``min_prominence_db`` that are narrower than ``max_width_hz`` are candidates.
5. Active span = from the first to the last segment (refined to frames) in
   which the bin exceeds the baseline by at least half the prominence
   threshold. Persistence = fraction of frames *within that span* that do.

These are *candidates*: a prominent musical harmonic looks exactly like a
resonance to this test. The user accepts or rejects each one.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.signal import find_peaks

from spledio.dsp.stft import STFTConfig, stft

SEGMENT_SECONDS = 1.0


@dataclass(frozen=True)
class ResonanceCandidate:
    frequency_hz: float
    prominence_db: float
    width_hz: float
    persistence: float  # 0..1, within the active span
    level_db: float  # sustained level re full-scale sine
    t_start: float | None = None  # active span, seconds from the start of the analysed audio
    t_end: float | None = None

    def describe(self) -> str:
        span = (f" between {self.t_start:.2f} and {self.t_end:.2f} s"
                if self.t_start is not None and self.t_end is not None else "")
        return (f"{self.frequency_hz:.0f} Hz  +{self.prominence_db:.1f} dB above local baseline, "
                f"~{self.width_hz:.0f} Hz wide, present in {self.persistence * 100:.0f}% of frames{span}")


def suggest_resonances(
    audio: np.ndarray,
    sample_rate: float,
    t0: float,
    t1: float,
    f_lo: float = 40.0,
    f_hi: float | None = None,
    cfg: STFTConfig = STFTConfig(8192, 2048, "hann"),
    min_prominence_db: float = 6.0,
    max_width_hz: float = 250.0,
    baseline_octaves: float = 1.0 / 3.0,
    max_candidates: int = 8,
) -> list[ResonanceCandidate]:
    audio = np.atleast_2d(np.asarray(audio))
    n = audio.shape[1]
    f_hi = sample_rate / 2.0 if f_hi is None else min(f_hi, sample_rate / 2.0)
    a, b = max(0, int(t0 * sample_rate)), min(n, int(t1 * sample_rate))
    if b - a < cfg.n_fft // 2:
        raise ValueError("Choose a region of at least half an analysis window "
                         f"({cfg.n_fft / 2 / sample_rate * 1000:.0f} ms)")
    k0, k1 = cfg.frames_touching(a, b)
    k0, k1 = k0 + cfg.n_fft // cfg.hop // 2, max(k0 + cfg.n_fft // cfg.hop // 2 + 1, k1 - cfg.n_fft // cfg.hop // 2)
    power = None
    for c in range(audio.shape[0]):
        X = stft(audio[c].astype(np.float64), cfg, k0, k1)
        p = np.abs(X) ** 2
        power = p if power is None else power + p
    power /= audio.shape[0]
    n_frames = power.shape[0]
    seg = max(4, int(round(SEGMENT_SECONDS * sample_rate / cfg.hop)))
    if n_frames >= 2 * seg:
        step = max(1, seg // 2)
        starts = list(range(0, n_frames - seg + 1, step))
        if starts[-1] != n_frames - seg:
            starts.append(n_frames - seg)
        seg_med = np.stack([np.median(power[i : i + seg], axis=0) for i in starts])
        med = seg_med.max(axis=0) + 1e-20
    else:
        starts, seg_med = [0], None
        med = np.median(power, axis=0) + 1e-20
    freqs = cfg.bin_frequencies(sample_rate)
    # log-frequency moving median baseline
    baseline = np.empty_like(med)
    ratio = 2.0 ** (baseline_octaves / 2.0)
    for i, f in enumerate(freqs):
        if f <= 0:
            baseline[i] = med[i]
            continue
        lo, hi = np.searchsorted(freqs, [f / ratio, f * ratio])
        lo, hi = max(1, lo), max(lo + 3, hi)
        baseline[i] = np.median(med[lo:hi])
    prom = 10.0 * np.log10(med / (baseline + 1e-20))
    band = (freqs >= f_lo) & (freqs <= f_hi)
    prom_b = np.where(band, prom, -np.inf)
    df = sample_rate / cfg.n_fft
    peaks, props = find_peaks(prom_b, height=min_prominence_db, width=(None, max(1.0, max_width_hz / df)))
    from spledio.dsp.stft import magnitude_reference

    ref = magnitude_reference(cfg) ** 2
    out = []
    hop_s = cfg.hop / sample_rate
    for i, pk in enumerate(peaks):
        thresh = baseline[pk] * 10 ** (min_prominence_db / 20.0)
        active = power[:, pk] > thresh
        lo_f, hi_f = 0, n_frames  # frame range of the active span
        if seg_med is not None:
            on = np.flatnonzero(seg_med[:, pk] > thresh)
            if on.size:
                lo_f, hi_f = starts[on[0]], starts[on[-1]] + seg
        hits = np.flatnonzero(active[lo_f:hi_f])
        if hits.size:
            lo_f, hi_f = lo_f + hits[0], lo_f + hits[-1] + 1
        persistence = float(np.mean(active[lo_f:hi_f])) if hi_f > lo_f else 0.0
        t_start = max(t0, ((k0 + lo_f) * cfg.hop) / sample_rate - hop_s / 2)
        t_end = min(t1, ((k0 + hi_f - 1) * cfg.hop) / sample_rate + hop_s / 2)
        out.append(ResonanceCandidate(
            frequency_hz=float(freqs[pk]),
            prominence_db=float(prom[pk]),
            width_hz=float(props["widths"][i] * df),
            persistence=persistence,
            level_db=float(10 * np.log10(med[pk] / ref)),
            t_start=float(t_start),
            t_end=float(max(t_end, t_start)),
        ))
    out.sort(key=lambda c: (c.prominence_db * (0.5 + c.persistence)), reverse=True)
    return out[:max_candidates]
