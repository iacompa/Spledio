"""Frequency scales, musical-note helpers and ruler ticks for the spectrogram view.

Pure functions only (no Qt, no audio). Normalised vertical positions ``u`` run
from ``0`` (bottom edge of the view) to ``1`` (top edge). Image rows are the
other way round (row 0 = top); :func:`row_edges_hz` / :func:`row_centres_hz`
return *ascending* arrays (index 0 = bottom row), so image row ``r`` is
ascending index ``height - 1 - r``.

DC / 0 Hz in log mode
---------------------
A logarithmic axis cannot show 0 Hz. The log axis therefore starts at
``axis_lo = max(view f_lo, floor_hz)`` with
``floor_hz = max(log_floor_hz, bin_spacing_hz)``. ``log_floor_hz`` defaults to
20 Hz; pass the display analysis bin spacing (``sample_rate / n_fft``, or use
:meth:`FrequencyScale.for_analysis`) so the floor is never below one FFT bin.

* Frequencies below the floor (including DC) are clamped to the floor before
  the log mapping, so they land on the bottom row (``u`` clamped to ``0`` when
  the view starts at or below the floor).
* ``from_unit(0)`` returns the floor.
* The spectrogram renderer lumps the bins below the floor into the bottom row
  (max-pooled, see :func:`row_edges_hz`), so DC offset / sub-sonic energy stays
  visible there instead of silently disappearing.
* Because the bottom edge stands for "0 Hz up to the floor", the UI treats a
  selection dragged to the bottom edge in log mode as extending to 0 Hz; use
  :meth:`FrequencyScale.is_at_floor` to detect that case.

Linear mode has no floor: ``to_unit``/``from_unit`` are exact affine inverses
(not clamped, so positions outside ``[0, 1]`` mean "outside the view").
"""

from __future__ import annotations

import math
from typing import NamedTuple

import numpy as np

__all__ = [
    "FrequencyScale",
    "Ticks",
    "NOTE_NAMES",
    "midi_number",
    "note_name",
    "note_ticks",
    "nice_time_ticks",
    "nice_freq_ticks",
    "format_time",
    "format_frequency",
    "column_centre_times",
    "row_edges_hz",
    "row_centres_hz",
    "nearest_index",
]


def _ret(x: np.ndarray):
    """Return a Python float for 0-d results, the array otherwise."""
    return float(x) if np.ndim(x) == 0 else x


def nearest_index(x) -> np.ndarray:
    """Round half up (``floor(x + 0.5)``) to int64 – the one rounding rule used for
    column→frame and row→bin mapping everywhere in ``rendering`` (NumPy's ``rint``
    rounds half to even, which would make the overlay and the image disagree on ties)."""
    return np.floor(np.asarray(x, dtype=np.float64) + 0.5).astype(np.int64)


class FrequencyScale:
    """Maps Hz <-> normalised position ``u`` (0 = bottom, 1 = top) for a view range.

    ``kind`` is :attr:`LINEAR` or :attr:`LOG`. ``f_lo`` is clamped to ``>= 0`` and
    ``f_hi`` to ``<= nyquist``. In log mode the axis starts at
    ``max(f_lo, floor_hz)`` (see module docstring). Instances are immutable and
    hashable.
    """

    LINEAR = "linear"
    LOG = "log"
    KINDS = (LINEAR, LOG)

    __slots__ = ("kind", "f_lo", "f_hi", "nyquist", "log_floor_hz", "floor_hz", "axis_lo", "_la", "_lspan", "_span")

    def __init__(self, kind, f_lo, f_hi, nyquist, log_floor_hz=20.0, *, bin_spacing_hz: float = 0.0):
        if kind not in self.KINDS:
            raise ValueError(f"Unknown frequency scale {kind!r}; use one of {self.KINDS}")
        nyquist = float(nyquist)
        if not nyquist > 0 or not math.isfinite(nyquist):
            raise ValueError("nyquist must be a positive finite frequency")
        lo = max(0.0, float(f_lo))
        hi = min(float(f_hi), nyquist)
        if not hi > lo:
            raise ValueError(f"Empty frequency view [{f_lo}, {f_hi}] (nyquist {nyquist})")
        floor = max(float(log_floor_hz), float(bin_spacing_hz))
        if not floor > 0 or not math.isfinite(floor):
            raise ValueError("log floor must be a positive frequency")
        s = object.__setattr__
        s(self, "kind", kind)
        s(self, "f_lo", lo)
        s(self, "f_hi", hi)
        s(self, "nyquist", nyquist)
        s(self, "log_floor_hz", float(log_floor_hz))
        s(self, "floor_hz", floor)
        if kind == self.LOG:
            axis_lo = max(lo, floor)
            if not hi > axis_lo:
                raise ValueError(f"Log view [{lo}, {hi}] Hz must extend above the log floor ({floor} Hz)")
            s(self, "axis_lo", axis_lo)
            s(self, "_la", math.log10(axis_lo))
            s(self, "_lspan", math.log10(hi) - math.log10(axis_lo))
            s(self, "_span", hi - axis_lo)
        else:
            s(self, "axis_lo", lo)
            s(self, "_la", 0.0)
            s(self, "_lspan", 0.0)
            s(self, "_span", hi - lo)

    def __setattr__(self, name, value):
        raise AttributeError("FrequencyScale is immutable")

    # -- construction helpers ------------------------------------------------
    @classmethod
    def for_analysis(cls, kind, f_lo, f_hi, sample_rate, n_fft, log_floor_hz=20.0) -> "FrequencyScale":
        """Scale whose log floor is never below one bin of an ``n_fft`` analysis."""
        sr = float(sample_rate)
        return cls(kind, f_lo, f_hi, sr / 2.0, log_floor_hz, bin_spacing_hz=sr / int(n_fft))

    def with_range(self, f_lo, f_hi) -> "FrequencyScale":
        return FrequencyScale(self.kind, f_lo, f_hi, self.nyquist, self.log_floor_hz, bin_spacing_hz=self.floor_hz)

    def with_kind(self, kind) -> "FrequencyScale":
        return FrequencyScale(kind, self.f_lo, self.f_hi, self.nyquist, self.log_floor_hz, bin_spacing_hz=self.floor_hz)

    # -- mapping -------------------------------------------------------------
    @property
    def floor_at_bottom(self) -> bool:
        """True when the bottom edge of the view stands for "0 Hz up to the floor"."""
        return self.kind == self.LOG and self.f_lo <= self.floor_hz

    def to_unit(self, f):
        """Hz -> ``u`` (vectorised). Log mode clamps ``f`` below the floor to the floor."""
        f = np.asarray(f, dtype=np.float64)
        if self.kind == self.LINEAR:
            u = (f - self.f_lo) / self._span
        else:
            u = (np.log10(np.maximum(f, self.floor_hz)) - self._la) / self._lspan
        return _ret(u)

    def from_unit(self, u):
        """``u`` -> Hz (vectorised). Exact inverse of :meth:`to_unit` above the floor."""
        u = np.asarray(u, dtype=np.float64)
        if self.kind == self.LINEAR:
            f = self.f_lo + u * self._span
        else:
            f = np.power(10.0, self._la + u * self._lspan)
        return _ret(f)

    def is_at_floor(self, u, tolerance: float = 1e-3):
        """True where ``u`` is at the bottom edge *and* that edge is the log floor.

        The UI extends a selection dragged there down to 0 Hz. Always False in
        linear mode (its bottom edge is a real frequency, ``from_unit(0) == f_lo``).
        """
        u = np.asarray(u, dtype=np.float64)
        res = (u <= float(tolerance)) & self.floor_at_bottom
        return bool(res) if res.ndim == 0 else res

    # -- dunder --------------------------------------------------------------
    def _key(self):
        return (self.kind, self.f_lo, self.f_hi, self.nyquist, self.floor_hz)

    def __eq__(self, other):
        return isinstance(other, FrequencyScale) and self._key() == other._key()

    def __hash__(self):
        return hash(self._key())

    def __repr__(self):
        return (
            f"FrequencyScale({self.kind!r}, f_lo={self.f_lo:g}, f_hi={self.f_hi:g}, "
            f"nyquist={self.nyquist:g}, floor_hz={self.floor_hz:g})"
        )


# ---------------------------------------------------------------------------
# view geometry shared by analysis.py and image.py
# ---------------------------------------------------------------------------


def column_centre_times(t0: float, t1: float, width: int) -> np.ndarray:
    """Centre time (s) of each of ``width`` columns spanning ``[t0, t1)``."""
    width = int(width)
    return float(t0) + (np.arange(width, dtype=np.float64) + 0.5) * ((float(t1) - float(t0)) / width)


def _view_unit_range(scale: FrequencyScale, f_lo, f_hi) -> tuple[float, float, float, float]:
    lo = scale.f_lo if f_lo is None else max(0.0, float(f_lo))
    hi = scale.f_hi if f_hi is None else min(float(f_hi), scale.nyquist)
    if not hi > lo:
        raise ValueError(f"Empty frequency view [{f_lo}, {f_hi}]")
    return lo, hi, float(scale.to_unit(lo)), float(scale.to_unit(hi))


def row_edges_hz(scale: FrequencyScale, height: int, f_lo=None, f_hi=None) -> np.ndarray:
    """Ascending row edge frequencies, shape ``(height + 1,)`` (index 0 = bottom edge).

    Rows are equal steps in ``u`` between ``to_unit(f_lo)`` and ``to_unit(f_hi)``
    (default: the scale's own range). In log mode, when the view starts at or
    below the floor, the bottom edge is set to the view's ``f_lo`` (usually 0 Hz)
    so the bottom row also covers everything below the floor (DC lumping).
    """
    lo, _hi, u_lo, u_hi = _view_unit_range(scale, f_lo, f_hi)
    u = u_lo + (u_hi - u_lo) * (np.arange(int(height) + 1, dtype=np.float64) / int(height))
    edges = np.asarray(scale.from_unit(u), dtype=np.float64)
    if scale.kind == FrequencyScale.LOG and lo <= scale.floor_hz:
        edges[0] = lo
    return edges


def row_centres_hz(scale: FrequencyScale, height: int, f_lo=None, f_hi=None) -> np.ndarray:
    """Ascending row-centre frequencies (centre in ``u``), shape ``(height,)``."""
    _lo, _hi, u_lo, u_hi = _view_unit_range(scale, f_lo, f_hi)
    u = u_lo + (u_hi - u_lo) * ((np.arange(int(height), dtype=np.float64) + 0.5) / int(height))
    return np.asarray(scale.from_unit(u), dtype=np.float64)


# ---------------------------------------------------------------------------
# musical notes
# ---------------------------------------------------------------------------

NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
_A4_HZ = 440.0
_A4_MIDI = 69


def midi_number(freq):
    """Fractional MIDI note number (A4 = 440 Hz = 69)."""
    f = np.asarray(freq, dtype=np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        m = _A4_MIDI + 12.0 * np.log2(f / _A4_HZ)
    return _ret(m)


def note_name(freq: float, *, cents: bool = False) -> str:
    """Nearest equal-tempered note name, e.g. ``440 -> 'A4'``, ``554.37 -> 'C#5'``.

    Returns ``''`` for non-positive / non-finite input. With ``cents=True`` the
    deviation is appended, e.g. ``'A4+12c'``.
    """
    f = float(freq)
    if not (f > 0 and math.isfinite(f)):
        return ""
    m_exact = _A4_MIDI + 12.0 * math.log2(f / _A4_HZ)
    m = int(math.floor(m_exact + 0.5))
    name = f"{NOTE_NAMES[m % 12]}{m // 12 - 1}"
    if cents:
        c = int(round(100.0 * (m_exact - m)))
        if c:
            name += f"{c:+d}c"
    return name


class Ticks(NamedTuple):
    """Ruler ticks. ``values`` are seconds or Hz; ``positions`` are normalised
    0..1 along the axis (time: 0 = ``t0`` / left; frequency: ``scale.to_unit``,
    0 = bottom); ``labels`` are display strings."""

    values: np.ndarray
    positions: np.ndarray
    labels: list


def note_ticks(f_lo: float, f_hi: float, which: str = "all", scale: FrequencyScale | None = None) -> Ticks:
    """Equal-tempered note frequencies in ``[f_lo, f_hi]`` for a note overlay.

    ``which``: ``'all'`` (every semitone), ``'naturals'`` (no sharps) or ``'C'``
    (octave markers). Positions use ``scale.to_unit`` if a scale is given, else NaN.
    """
    if which not in ("all", "naturals", "C"):
        raise ValueError("which must be 'all', 'naturals' or 'C'")
    lo, hi = max(float(f_lo), 1e-3), float(f_hi)
    if not hi > lo:
        return Ticks(np.zeros(0), np.zeros(0), [])
    m0 = math.ceil(_A4_MIDI + 12.0 * math.log2(lo / _A4_HZ) - 1e-9)
    m1 = math.floor(_A4_MIDI + 12.0 * math.log2(hi / _A4_HZ) + 1e-9)
    ms = np.arange(m0, m1 + 1)
    if which == "naturals":
        ms = ms[np.isin(ms % 12, (0, 2, 4, 5, 7, 9, 11))]
    elif which == "C":
        ms = ms[ms % 12 == 0]
    freqs = _A4_HZ * np.power(2.0, (ms - _A4_MIDI) / 12.0)
    labels = [f"{NOTE_NAMES[m % 12]}{m // 12 - 1}" for m in ms.tolist()]
    pos = np.asarray(scale.to_unit(freqs), dtype=np.float64) if scale is not None else np.full(len(ms), np.nan)
    return Ticks(freqs, pos, labels)


# ---------------------------------------------------------------------------
# ruler ticks
# ---------------------------------------------------------------------------


def _multiples(lo: float, hi: float, step: float) -> np.ndarray:
    i0 = math.ceil(lo / step - 1e-9)
    i1 = math.floor(hi / step + 1e-9)
    if i1 < i0:
        return np.zeros(0)
    return np.arange(i0, i1 + 1, dtype=np.float64) * step


def _count(lo: float, hi: float, step: float) -> int:
    return max(0, math.floor(hi / step + 1e-9) - math.ceil(lo / step - 1e-9) + 1)


def _decade_steps(lo_exp: int, hi_exp: int, mantissas=(1, 2, 5)) -> list[float]:
    return [m * 10.0**e for e in range(lo_exp, hi_exp + 1) for m in mantissas]


_LONG_TIME_STEPS = (1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400)


def _decimals_for(step: float) -> int:
    return max(0, int(math.ceil(-math.log10(step) - 1e-9)))


def format_time(t: float, step: float | None = None, clock: bool | None = None) -> str:
    """Format seconds for a ruler: ``'1.250 s'`` (sub-second steps), ``'12 s'``,
    or clock style ``'1:05'`` / ``'1:02:05'`` (``clock=None``: when ``|t| >= 60``)."""
    t = float(t)
    if step is not None and step < 1.0:
        d = max(3, _decimals_for(step))
        return f"{t:.{d}f} s"
    if clock is None:
        clock = abs(t) >= 60.0
    if not clock:
        if step is None and abs(t - round(t)) > 1e-9:
            return f"{t:.3f} s"
        return f"{t:.0f} s"
    sign = "-" if t < 0 else ""
    total = int(round(abs(t)))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{sign}{h}:{m:02d}:{s:02d}" if h else f"{sign}{m}:{s:02d}"


def _strip(v: float, decimals: int) -> str:
    s = f"{v:.{decimals}f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def format_frequency(f: float) -> str:
    """``'0 Hz'``, ``'62.5 Hz'``, ``'500 Hz'``, ``'1 kHz'``, ``'12.5 kHz'``."""
    f = float(f)
    if abs(f) >= 1000.0:
        return f"{_strip(round(f / 1000.0, 6), 3)} kHz"
    return f"{_strip(round(f, 6), 2)} Hz"


def nice_time_ticks(t0: float, t1: float, max_ticks: int = 10) -> Ticks:
    """Round time ticks (1-2-5 sub-second steps; 1, 2, 5, 10, 15, 30 s; minutes; hours)."""
    t0, t1 = float(t0), float(t1)
    max_ticks = max(1, int(max_ticks))
    if not t1 > t0:
        return Ticks(np.zeros(0), np.zeros(0), [])
    span = t1 - t0
    lo_exp = int(math.floor(math.log10(span / max_ticks))) - 1
    candidates = sorted({s for s in _decade_steps(lo_exp, 0) if s < 1.0} | set(map(float, _LONG_TIME_STEPS)))
    step = None
    for s in candidates:
        if _count(t0, t1, s) <= max_ticks:
            step = s
            break
    if step is None:  # absurdly long span: fall back to decades of days
        step = 86400.0 * 10.0 ** math.ceil(math.log10(span / 86400.0 / max_ticks))
    vals = _multiples(t0, t1, step)
    vals = np.round(vals, 9)
    clock = bool(len(vals)) and step >= 1.0 and float(np.abs(vals).max()) >= 60.0
    labels = [format_time(v, step, clock) for v in vals.tolist()]
    return Ticks(vals, (vals - t0) / span, labels)


def _linear_freq_values(lo: float, hi: float, max_ticks: int) -> np.ndarray:
    span = hi - lo
    lo_exp = int(math.floor(math.log10(span / max_ticks))) - 1
    for s in _decade_steps(lo_exp, lo_exp + 3):
        if _count(lo, hi, s) <= max_ticks:
            return np.round(_multiples(lo, hi, s), 6)
    return np.array([lo, hi])


def nice_freq_ticks(scale: FrequencyScale, max_ticks: int = 8) -> Ticks:
    """Round frequency ticks for ``scale`` (labels like ``'500 Hz'``, ``'1 kHz'``, ``'12.5 kHz'``).

    Linear: 1-2-5 steps. Log: the densest of {1..9, 1/2/5, 1/3, 1} x 10^n that
    fits ``max_ticks``; narrow log views fall back to linear steps.
    """
    max_ticks = max(1, int(max_ticks))
    lo = scale.axis_lo
    hi = scale.f_hi
    if scale.kind == FrequencyScale.LINEAR:
        vals = _linear_freq_values(lo, hi, max_ticks)
    else:
        e0 = int(math.floor(math.log10(lo)))
        e1 = int(math.floor(math.log10(hi)))
        vals = np.zeros(0)
        for mant in ((1, 2, 3, 4, 5, 6, 7, 8, 9), (1, 2, 5), (1, 3), (1,)):
            cand = np.array([m * 10.0**e for e in range(e0, e1 + 1) for m in mant])
            cand = cand[(cand >= lo * (1 - 1e-9)) & (cand <= hi * (1 + 1e-9))]
            if len(cand) <= max_ticks:
                vals = cand
                break
        if len(vals) < 2:
            vals = _linear_freq_values(lo, hi, max_ticks)
    vals = np.asarray(vals, dtype=np.float64)
    labels = [format_frequency(v) for v in vals.tolist()]
    return Ticks(vals, np.asarray(scale.to_unit(vals), dtype=np.float64).reshape(-1), labels)
