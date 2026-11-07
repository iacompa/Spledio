"""Selections: ordered shape operations + feathering + channel scope, and
rasterisation onto an STFT processing grid.

Combination rules for soft masks (documented and tested):

==========  ==============================
REPLACE     ``M = m``
ADD         ``M = max(M, m)``
SUBTRACT    ``M = min(M, 1 - m)``
INTERSECT   ``M = min(M, m)``
INVERT      ``M = 1 - M`` (whole project extent)
==========  ==============================

The mask used for DSP is the continuous membership sampled at the grid nodes
``(t_k, f_b)`` = (frame centre time, bin centre frequency) of the edit STFT
configuration. The UI overlay draws the very same raster, so what you see is
exactly what is processed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

import numpy as np

from spledio.dsp.stft import STFTConfig
from spledio.selections.geometry import (
    INF_F,
    BrushShape,
    RectShape,
    Shape,
    frequency_range,
    shape_from_dict,
    time_range,
)

REPLACE, ADD, SUBTRACT, INTERSECT, INVERT = "replace", "add", "subtract", "intersect", "invert"
MODES = (REPLACE, ADD, SUBTRACT, INTERSECT)


@dataclass(frozen=True)
class SelectionOp:
    mode: str
    shape: Shape | None  # None only for INVERT

    def to_dict(self) -> dict:
        return {"mode": self.mode, "shape": None if self.shape is None else self.shape.to_dict()}

    @staticmethod
    def from_dict(d: dict) -> "SelectionOp":
        mode = d["mode"]
        if mode not in MODES + (INVERT,):
            raise ValueError(f"bad selection mode {mode!r}")
        shp = None if d.get("shape") is None else shape_from_dict(d["shape"])
        if mode != INVERT and shp is None:
            raise ValueError("selection op without shape")
        return SelectionOp(mode, shp)


@dataclass(frozen=True)
class Selection:
    """Immutable selection. ``channels=None`` means all channels (linked)."""

    ops: tuple[SelectionOp, ...] = ()
    feather_t: float = 0.0  # seconds
    feather_f: float = 0.0  # Hz
    feather_enabled: bool = True
    channels: tuple[int, ...] | None = None
    name: str = ""

    # ----- construction -------------------------------------------------
    @staticmethod
    def of(shape: Shape, **kw) -> "Selection":
        return Selection(ops=(SelectionOp(REPLACE, shape),), **kw)

    def combine(self, mode: str, shape: Shape) -> "Selection":
        if mode == REPLACE or not self.ops:
            return replace(self, ops=(SelectionOp(REPLACE, shape),))
        if mode not in MODES:
            raise ValueError(mode)
        return replace(self, ops=self.ops + (SelectionOp(mode, shape),))

    def inverted(self) -> "Selection":
        if self.ops and self.ops[-1].mode == INVERT:
            return replace(self, ops=self.ops[:-1])
        return replace(self, ops=self.ops + (SelectionOp(INVERT, None),))

    def with_feather(self, feather_t: float | None = None, feather_f: float | None = None, enabled: bool | None = None):
        return replace(
            self,
            feather_t=self.feather_t if feather_t is None else max(0.0, float(feather_t)),
            feather_f=self.feather_f if feather_f is None else max(0.0, float(feather_f)),
            feather_enabled=self.feather_enabled if enabled is None else bool(enabled),
        )

    def with_channels(self, channels: tuple[int, ...] | None) -> "Selection":
        return replace(self, channels=None if channels is None else tuple(sorted(set(int(c) for c in channels))))

    def transformed(self, dt: float = 0.0, df: float = 0.0) -> "Selection":
        """Move the selection mask only (never the audio)."""
        return replace(
            self, ops=tuple(SelectionOp(o.mode, None if o.shape is None else o.shape.translated(dt, df)) for o in self.ops)
        )

    def expanded(self, dt: float = 0.0, df: float = 0.0) -> "Selection":
        """Grow (positive) or shrink (negative) rect/ellipse/brush geometry on each axis."""
        from spledio.selections.geometry import EllipseShape

        def grow(s: Shape | None):
            if isinstance(s, RectShape):
                t0, t1 = s.t0 - dt, s.t1 + dt
                if t1 < t0:
                    t0 = t1 = 0.5 * (s.t0 + s.t1)
                f0, f1 = max(0.0, s.f0 - df) if s.f0 > 0 else 0.0, (s.f1 + df if s.f1 < INF_F else INF_F)
                if f1 < f0:
                    f0 = f1 = 0.5 * (s.f0 + s.f1)
                return RectShape(t0, t1, f0, f1)
            if isinstance(s, EllipseShape):
                return EllipseShape(s.tc, s.fc, max(1e-6, s.rt + dt), max(1e-3, s.rf + df))
            if isinstance(s, BrushShape):
                return BrushShape(s.path, max(1e-6, s.rt + dt), max(1e-3, s.rf + df), s.hardness, s.square)
            return s

        return replace(self, ops=tuple(SelectionOp(o.mode, grow(o.shape)) for o in self.ops))

    # ----- queries --------------------------------------------------------
    @property
    def is_empty(self) -> bool:
        return not self.ops

    @property
    def effective_feather(self) -> tuple[float, float]:
        if not self.feather_enabled:
            return 0.0, 0.0
        return self.feather_t, self.feather_f

    def is_full_band_time_range(self) -> bool:
        """True when the selection is a single time span across all frequencies."""
        return (
            len(self.ops) == 1
            and isinstance(self.ops[0].shape, RectShape)
            and self.ops[0].shape.full_band
            and not self.ops[0].shape.full_time
            and self.effective_feather[1] == 0.0
        )

    def time_span_of_rect(self) -> tuple[float, float] | None:
        if self.is_full_band_time_range():
            s = self.ops[0].shape
            return s.t0, s.t1
        return None

    def bounds(self, duration: float, nyquist: float) -> tuple[float, float, float, float] | None:
        """Conservative (t0, t1, f0, f1) support of M > 0, clipped to the project."""
        ft, ff = self.effective_feather
        box = None
        for op in self.ops:
            if op.mode == INVERT:
                box = (0.0, duration, 0.0, nyquist)
                continue
            t0, t1, f0, f1 = op.shape.bbox()
            b = (t0 - ft, t1 + ft, max(0.0, f0 - ff), f1 + ff)
            if op.mode == REPLACE or box is None and op.mode == ADD:
                box = b
            elif op.mode == ADD:
                box = (min(box[0], b[0]), max(box[1], b[1]), min(box[2], b[2]), max(box[3], b[3]))
            elif op.mode == INTERSECT and box is not None:
                box = (max(box[0], b[0]), min(box[1], b[1]), max(box[2], b[2]), min(box[3], b[3]))
            # SUBTRACT never grows the support
        if box is None:
            return None
        t0, t1 = max(box[0], 0.0), min(box[1], duration)
        f0, f1 = max(box[2], 0.0), min(box[3], nyquist)
        if t1 < t0 or f1 < f0:
            return None
        return (t0, t1, f0, f1)

    def evaluate(self, t: np.ndarray, f: np.ndarray) -> np.ndarray:
        """Continuous combined membership on a (T,1) x (1,F) grid."""
        ft, ff = self.effective_feather
        shape = np.broadcast(t, f).shape
        m = np.zeros(shape)
        for op in self.ops:
            if op.mode == INVERT:
                m = 1.0 - m
                continue
            v = op.shape.evaluate(t, f, ft, ff)
            if op.mode == REPLACE:
                m = np.array(np.broadcast_to(v, shape), dtype=np.float64)
            elif op.mode == ADD:
                m = np.maximum(m, v)
            elif op.mode == SUBTRACT:
                m = np.minimum(m, 1.0 - v)
            elif op.mode == INTERSECT:
                m = np.minimum(m, v)
        return np.clip(m, 0.0, 1.0)

    def describe(self, sample_rate: float | None = None) -> str:
        if not self.ops:
            return "no selection"
        parts = []
        for op in self.ops:
            if op.mode == INVERT:
                parts.append("inverted")
            else:
                prefix = "" if op.mode in (REPLACE,) else f"{op.mode} "
                parts.append(prefix + op.shape.describe())
        return "; ".join(parts)

    def label_range(self, duration: float, nyquist: float) -> str:
        b = self.bounds(duration, nyquist)
        if b is None:
            return "empty selection"
        t0, t1, f0, f1 = b
        if f0 <= 0 and f1 >= nyquist:
            return f"{t0:.2f}-{t1:.2f} s, all frequencies"
        return f"{t0:.2f}-{t1:.2f} s, {f0:.0f}-{f1:.0f} Hz"

    # ----- persistence ----------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "ops": [o.to_dict() for o in self.ops],
            "feather_t": self.feather_t,
            "feather_f": self.feather_f,
            "feather_enabled": self.feather_enabled,
            "channels": None if self.channels is None else list(self.channels),
            "name": self.name,
        }

    @staticmethod
    def from_dict(d: dict) -> "Selection":
        ops = tuple(SelectionOp.from_dict(o) for o in d.get("ops", []))
        if len(ops) > 10_000:
            raise ValueError("too many selection operations")
        ch = d.get("channels")
        return Selection(
            ops=ops,
            feather_t=max(0.0, float(d.get("feather_t", 0.0))),
            feather_f=max(0.0, float(d.get("feather_f", 0.0))),
            feather_enabled=bool(d.get("feather_enabled", True)),
            channels=None if ch is None else tuple(int(c) for c in ch),
            name=str(d.get("name", "")),
        )


def select_all(duration: float) -> Selection:
    return Selection.of(RectShape(0.0, duration, 0.0, INF_F))


@dataclass
class MaskRaster:
    """Soft mask on the processing grid: frames ``[k0, k1)`` x bins ``[b0, b1)``."""

    k0: int
    k1: int
    b0: int
    b1: int
    data: np.ndarray  # float64 (k1-k0, b1-b0) in [0,1]
    config: STFTConfig = field(default_factory=STFTConfig)

    @property
    def empty(self) -> bool:
        return self.data.size == 0 or not np.any(self.data > 0)

    def full_bins(self) -> np.ndarray:
        out = np.zeros((self.k1 - self.k0, self.config.n_bins))
        out[:, self.b0 : self.b1] = self.data
        return out


def raster_extent(
    selection: Selection, cfg: STFTConfig, sample_rate: float, n_samples: int, margin_frames: int = 1
) -> tuple[int, int, int, int] | None:
    """Grid extent ``(k0, k1, b0, b1)`` (half-open) that contains all of M > 0."""
    duration = n_samples / sample_rate
    b = selection.bounds(duration, sample_rate / 2.0)
    if b is None:
        return None
    t0, t1, f0, f1 = b
    hop, sr = cfg.hop, sample_rate
    fk0, fk1 = cfg.full_frame_range(n_samples)
    k0 = max(fk0, int(math.floor(t0 * sr / hop)) - margin_frames)
    k1 = min(fk1, int(math.ceil(t1 * sr / hop)) + 1 + margin_frames)
    df = sr / cfg.n_fft
    b0 = max(0, int(math.floor(f0 / df)) - 1)
    b1 = min(cfg.n_bins, int(math.ceil(f1 / df)) + 2)
    if k1 <= k0 or b1 <= b0:
        return None
    return k0, k1, b0, b1


def rasterize(
    selection: Selection,
    cfg: STFTConfig,
    sample_rate: float,
    n_samples: int,
    k_range: tuple[int, int] | None = None,
    margin_frames: int = 1,
) -> MaskRaster | None:
    """Sample the selection at the STFT grid nodes. Returns ``None`` if empty.

    Only the bounding region of the support is evaluated (plus ``margin_frames``);
    ``k_range`` restricts the frames further (used for chunked processing).
    """
    ext = raster_extent(selection, cfg, sample_rate, n_samples, margin_frames)
    if ext is None:
        return None
    k0, k1, b0, b1 = ext
    if k_range is not None:
        k0, k1 = max(k0, k_range[0]), min(k1, k_range[1])
    if k1 <= k0:
        return None
    hop, sr = cfg.hop, sample_rate
    t = (np.arange(k0, k1) * (hop / sr))[:, None]
    f = (np.arange(b0, b1) * (sr / cfg.n_fft))[None, :]
    data = selection.evaluate(t, f)
    if not np.any(data > 0):
        return None
    return MaskRaster(k0, k1, b0, b1, data, cfg)


def rasterize_on(selection: Selection, frame_times: np.ndarray, freqs: np.ndarray) -> np.ndarray:
    """Evaluate on arbitrary grid nodes (used by the overlay at coarse zoom)."""
    return selection.evaluate(np.asarray(frame_times)[:, None], np.asarray(freqs)[None, :])


__all__ = [
    "Selection",
    "SelectionOp",
    "MaskRaster",
    "rasterize",
    "raster_extent",
    "rasterize_on",
    "select_all",
    "time_range",
    "frequency_range",
    "REPLACE",
    "ADD",
    "SUBTRACT",
    "INTERSECT",
    "INVERT",
]
