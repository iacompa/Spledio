"""Selection shapes in audio coordinates (seconds, Hz).

Every shape evaluates a *continuous* soft membership ``m(t, f)`` in [0, 1]:

* ``1`` inside the base geometry,
* an outward raised-cosine fall-off over ``feather_t`` seconds (time) and
  ``feather_f`` Hz (frequency) outside it,
* ``0`` beyond the feather.

Time and frequency feathering are independent and are *not* part of the
shape: they are passed at evaluation time so the user can change them after
drawing. Feathering is measured in an anisotropic normalised space
``(dt / feather_t, df / feather_f)``; a zero feather on an axis means a hard
edge on that axis.

Shapes are immutable and serialisable with ``to_dict``/``shape_from_dict``.
Screen pixels are never stored.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

INF_F = 1.0e12  # "all frequencies" upper bound (clipped to Nyquist when rasterised)


def raised_cosine(d: np.ndarray) -> np.ndarray:
    """1 at d<=0, 0 at d>=1, smooth raised cosine in between."""
    d = np.clip(d, 0.0, 1.0)
    return 0.5 * (1.0 + np.cos(np.pi * d))


def _axis_falloff(dist: np.ndarray, feather: float) -> np.ndarray:
    """Outward distance (>=0 outside, 0 inside) -> membership along one axis."""
    if feather <= 0.0:
        return (dist <= 0.0).astype(np.float64)
    return raised_cosine(dist / feather)


@dataclass(frozen=True)
class Shape:
    kind: str = field(init=False, default="shape")

    def bbox(self) -> tuple[float, float, float, float]:
        """(t0, t1, f0, f1) of the base geometry."""
        raise NotImplementedError

    def evaluate(self, t: np.ndarray, f: np.ndarray, feather_t: float, feather_f: float) -> np.ndarray:
        """Membership on a grid; ``t`` shape (T,1) or broadcastable, ``f`` shape (1,F)."""
        raise NotImplementedError

    def translated(self, dt: float, df: float) -> "Shape":
        raise NotImplementedError

    def to_dict(self) -> dict:
        raise NotImplementedError

    def describe(self) -> str:
        t0, t1, f0, f1 = self.bbox()
        return f"{self.kind} {t0:.3f}-{t1:.3f} s, {f0:.0f}-{min(f1, 1e9):.0f} Hz"


@dataclass(frozen=True)
class RectShape(Shape):
    """Axis-aligned time/frequency box. TimeRange and FrequencyRange are rects
    with one axis spanning everything (``f0=0, f1=INF_F`` / ``t0=-inf, t1=inf``)."""

    t0: float = 0.0
    t1: float = 0.0
    f0: float = 0.0
    f1: float = INF_F
    kind: str = field(init=False, default="rect")

    def __post_init__(self):
        if self.t1 < self.t0:
            a, b = self.t1, self.t0
            object.__setattr__(self, "t0", a)
            object.__setattr__(self, "t1", b)
        if self.f1 < self.f0:
            a, b = self.f1, self.f0
            object.__setattr__(self, "f0", a)
            object.__setattr__(self, "f1", b)
        object.__setattr__(self, "f0", max(0.0, self.f0))

    @property
    def full_band(self) -> bool:
        return self.f0 <= 0.0 and self.f1 >= INF_F

    @property
    def full_time(self) -> bool:
        return math.isinf(self.t0) and math.isinf(self.t1)

    def bbox(self):
        return (self.t0, self.t1, self.f0, self.f1)

    def evaluate(self, t, f, feather_t, feather_f):
        dt = np.maximum(np.maximum(self.t0 - t, t - self.t1), 0.0)
        df = np.maximum(np.maximum(self.f0 - f, f - self.f1), 0.0)
        if self.f0 <= 0.0:  # never feather below DC
            df = np.where(f < self.f0, 0.0, df)
        return _axis_falloff(dt, feather_t) * _axis_falloff(df, feather_f)

    def translated(self, dt, df):
        return RectShape(self.t0 + dt, self.t1 + dt, max(0.0, self.f0 + df), self.f1 + df if self.f1 < INF_F else INF_F)

    def to_dict(self):
        return {"kind": "rect", "t0": _fin(self.t0), "t1": _fin(self.t1), "f0": self.f0, "f1": _fin(self.f1)}

    def describe(self):
        if self.full_band and self.full_time:
            return "everything"
        if self.full_band:
            return f"time {self.t0:.3f}-{self.t1:.3f} s (all frequencies)"
        if self.full_time:
            return f"frequency {self.f0:.0f}-{self.f1:.0f} Hz (whole duration)"
        return f"{self.t0:.3f}-{self.t1:.3f} s, {self.f0:.0f}-{self.f1:.0f} Hz"


def time_range(t0: float, t1: float) -> RectShape:
    return RectShape(t0, t1, 0.0, INF_F)


def frequency_range(f0: float, f1: float) -> RectShape:
    return RectShape(-math.inf, math.inf, f0, f1)


@dataclass(frozen=True)
class EllipseShape(Shape):
    tc: float = 0.0
    fc: float = 0.0
    rt: float = 0.1  # semi-axis, seconds
    rf: float = 100.0  # semi-axis, Hz
    kind: str = field(init=False, default="ellipse")

    def bbox(self):
        return (self.tc - self.rt, self.tc + self.rt, max(0.0, self.fc - self.rf), self.fc + self.rf)

    def evaluate(self, t, f, feather_t, feather_f):
        dt = np.abs(t - self.tc)
        df = np.abs(f - self.fc)
        rt, rf = max(self.rt, 1e-12), max(self.rf, 1e-9)
        inside = (dt / rt) ** 2 + (df / rf) ** 2 <= 1.0
        if feather_t <= 0 and feather_f <= 0:
            return inside.astype(np.float64)
        dt_b, df_b = np.broadcast_arrays(dt, df)
        # find s in [0,1] with (dt/(rt+s*ft))^2 + (df/(rf+s*ff))^2 = 1 (monotonic in s)
        lo = np.zeros(dt_b.shape)
        hi = np.ones(dt_b.shape)
        for _ in range(18):
            mid = 0.5 * (lo + hi)
            val = (dt_b / (rt + mid * feather_t)) ** 2 + (df_b / (rf + mid * feather_f)) ** 2
            outside = val > 1.0
            lo = np.where(outside, mid, lo)
            hi = np.where(outside, hi, mid)
        s = 0.5 * (lo + hi)
        outer = (dt_b / (rt + feather_t)) ** 2 + (df_b / (rf + feather_f)) ** 2 > 1.0
        m = raised_cosine(s)
        m = np.where(inside, 1.0, np.where(outer, 0.0, m))
        return m

    def translated(self, dt, df):
        return EllipseShape(self.tc + dt, max(0.0, self.fc + df), self.rt, self.rf)

    def to_dict(self):
        return {"kind": "ellipse", "tc": self.tc, "fc": self.fc, "rt": self.rt, "rf": self.rf}


def _points_in_polygon(px: np.ndarray, py: np.ndarray, vx: np.ndarray, vy: np.ndarray) -> np.ndarray:
    """Even-odd rule, vectorised over points."""
    inside = np.zeros(np.broadcast(px, py).shape, dtype=bool)
    n = len(vx)
    j = n - 1
    for i in range(n):
        xi, yi, xj, yj = vx[i], vy[i], vx[j], vy[j]
        cond = (yi > py) != (yj > py)
        with np.errstate(divide="ignore", invalid="ignore"):
            xint = (xj - xi) * (py - yi) / (yj - yi + 1e-300) + xi
        inside ^= cond & (px < xint)
        j = i
    return inside


def _segment_distance(px, py, ax, ay, bx, by):
    abx, aby = bx - ax, by - ay
    denom = abx * abx + aby * aby
    if denom <= 0:
        return np.hypot(px - ax, py - ay)
    u = np.clip(((px - ax) * abx + (py - ay) * aby) / denom, 0.0, 1.0)
    return np.hypot(px - (ax + u * abx), py - (ay + u * aby))


@dataclass(frozen=True)
class PolygonShape(Shape):
    """Closed polygon (freehand lasso or polygon lasso). Vertices in (s, Hz)."""

    points: tuple[tuple[float, float], ...] = ()
    kind: str = field(init=False, default="polygon")

    def bbox(self):
        p = np.asarray(self.points, dtype=np.float64)
        return (float(p[:, 0].min()), float(p[:, 0].max()), max(0.0, float(p[:, 1].min())), float(p[:, 1].max()))

    def evaluate(self, t, f, feather_t, feather_f):
        p = np.asarray(self.points, dtype=np.float64)
        if len(p) < 3:
            return np.zeros(np.broadcast(t, f).shape)
        tb, fb = np.broadcast_arrays(t, f)
        inside = _points_in_polygon(tb, fb, p[:, 0], p[:, 1])
        if feather_t <= 0 and feather_f <= 0:
            return inside.astype(np.float64)
        # anisotropic normalised distance to the boundary (exact, vector)
        st = feather_t if feather_t > 0 else 1e-9
        sf = feather_f if feather_f > 0 else 1e-6
        qx, qy = tb / st, fb / sf
        vx, vy = p[:, 0] / st, p[:, 1] / sf
        d = np.full(tb.shape, np.inf)
        n = len(p)
        for i in range(n):
            j = (i + 1) % n
            d = np.minimum(d, _segment_distance(qx, qy, vx[i], vy[i], vx[j], vy[j]))
        m = raised_cosine(d)
        return np.where(inside, 1.0, m)

    def translated(self, dt, df):
        return PolygonShape(tuple((a + dt, max(0.0, b + df)) for a, b in self.points))

    def to_dict(self):
        return {"kind": "polygon", "points": [[float(a), float(b)] for a, b in self.points]}


@dataclass(frozen=True)
class BrushShape(Shape):
    """Union (max) of elliptical stamps along a stroke path.

    ``rt``/``rf`` are the stamp semi-axes in seconds/Hz (the UI converts a
    screen-size brush to spectral units at stroke time and records the
    result). ``hardness`` in [0,1]: the fraction of the radius that is fully
    opaque; the rest falls off with a raised cosine. Accumulation is ``max``,
    so the coverage of one stroke never grows with the pointer event rate.
    The path is resampled so stamps are at most ``spacing`` radii apart.
    """

    path: tuple[tuple[float, float], ...] = ()
    rt: float = 0.02
    rf: float = 50.0
    hardness: float = 0.5
    square: bool = False
    kind: str = field(init=False, default="brush")

    def stamps(self, spacing: float = 0.25) -> np.ndarray:
        p = np.asarray(self.path, dtype=np.float64).reshape(-1, 2)
        if len(p) <= 1:
            return p
        out = [p[0]]
        for a, b in zip(p[:-1], p[1:]):
            dn = math.hypot((b[0] - a[0]) / self.rt, (b[1] - a[1]) / self.rf)
            steps = max(1, int(math.ceil(dn / spacing)))
            for s in range(1, steps + 1):
                out.append(a + (b - a) * (s / steps))
        return np.asarray(out)

    def bbox(self):
        p = np.asarray(self.path, dtype=np.float64).reshape(-1, 2)
        return (
            float(p[:, 0].min() - self.rt),
            float(p[:, 0].max() + self.rt),
            max(0.0, float(p[:, 1].min() - self.rf)),
            float(p[:, 1].max() + self.rf),
        )

    def evaluate(self, t, f, feather_t, feather_f):
        tb, fb = np.broadcast_arrays(t, f)
        m = np.zeros(tb.shape)
        st = self.stamps()
        if len(st) == 0:
            return m
        # Brush softness is defined by `hardness`; the selection-wide feather
        # parameters intentionally do not enlarge brush stamps.
        hard = float(np.clip(self.hardness, 0.0, 1.0))
        rt, rf = self.rt, self.rf
        # restrict work per stamp to its local bbox for speed
        t_axis = tb[:, 0] if tb.ndim == 2 else tb
        f_axis = fb[0, :] if fb.ndim == 2 else fb
        for ct, cf in st:
            i0, i1 = np.searchsorted(t_axis, [ct - rt, ct + rt])
            j0, j1 = np.searchsorted(f_axis, [cf - rf, cf + rf])
            if i1 <= i0 or j1 <= j0:
                continue
            dt = (t_axis[i0:i1, None] - ct) / rt
            df = (f_axis[None, j0:j1] - cf) / rf
            r = np.maximum(np.abs(dt), np.abs(df)) if self.square else np.sqrt(dt * dt + df * df)
            if hard >= 1.0:
                v = (r <= 1.0).astype(np.float64)
            else:
                v = raised_cosine((r - hard) / (1.0 - hard))
            sub = m[i0:i1, j0:j1]
            np.maximum(sub, v, out=sub)
        return m

    def translated(self, dt, df):
        return BrushShape(tuple((a + dt, max(0.0, b + df)) for a, b in self.path), self.rt, self.rf, self.hardness, self.square)

    def to_dict(self):
        return {
            "kind": "brush",
            "path": [[float(a), float(b)] for a, b in self.path],
            "rt": self.rt,
            "rf": self.rf,
            "hardness": self.hardness,
            "square": self.square,
        }


def _fin(v: float):
    if math.isinf(v):
        return "inf" if v > 0 else "-inf"
    return float(v)


def _num(v) -> float:
    if isinstance(v, str):
        if v in ("inf", "-inf"):
            return float(v)
        raise ValueError(f"bad number {v!r}")
    x = float(v)
    if math.isnan(x):
        raise ValueError("NaN in selection")
    return x


def _finite(v) -> float:
    x = _num(v)
    if not math.isfinite(x):
        raise ValueError(f"bad number {v!r}")
    return x


def _radius(v) -> float:
    """A brush radius: finite and > 0 (stamp spacing divides by it)."""
    x = _finite(v)
    if x <= 0.0:
        raise ValueError(f"brush radius must be positive, got {x!r}")
    return x


def shape_from_dict(d: dict) -> Shape:
    """Shape from :meth:`Shape.to_dict` output. Raises ``ValueError`` for anything the editor
    cannot draw (non-finite coordinates, a polygon with fewer than 3 points, an empty brush or one
    of radius 0, a negative ellipse), so a damaged project fails to open instead of failing every
    redraw."""
    kind = d.get("kind")
    if kind == "rect":
        return RectShape(_num(d["t0"]), _num(d["t1"]), _num(d["f0"]), _num(d["f1"]))
    if kind == "ellipse":
        rt, rf = _finite(d["rt"]), _finite(d["rf"])
        if rt < 0.0 or rf < 0.0:
            raise ValueError("ellipse radius must not be negative")
        return EllipseShape(_finite(d["tc"]), _finite(d["fc"]), rt, rf)
    if kind == "polygon":
        pts = tuple((_finite(a), _finite(b)) for a, b in d["points"])
        if len(pts) > 100_000:
            raise ValueError("polygon too large")
        if len(pts) < 3:
            raise ValueError("a polygon needs at least 3 points")
        return PolygonShape(pts)
    if kind == "brush":
        pts = tuple((_finite(a), _finite(b)) for a, b in d["path"])
        if len(pts) > 100_000:
            raise ValueError("brush path too large")
        if not pts:
            raise ValueError("empty brush stroke")
        return BrushShape(pts, _radius(d["rt"]), _radius(d["rf"]), _finite(d["hardness"]), bool(d.get("square", False)))
    raise ValueError(f"unknown shape kind {kind!r}")
