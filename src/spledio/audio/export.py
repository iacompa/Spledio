"""Audio export: format/subtype validation, clipping policy, dither, optional
sample-rate conversion and atomic writing (libsndfile via ``soundfile``).

Input audio uses the engine convention: ``numpy.ndarray`` of shape
**(channels, frames)**, any floating dtype. Processing math is float64; data
is converted to soundfile's (frames, channels) layout only when it is handed
to libsndfile, one block at a time.

What happens to the samples, in order:

1. **Validation** – shape, channel count (1–64), frames > 0, finite values,
   format / subtype (:data:`EXPORT_FORMATS`), sample rates, container limits
   (FLAC: ≤ 8 channels, ≤ 655350 Hz; WAV: ≤ 4 GiB of sample data) and the
   destination (see *Safety* below). Nothing is written if any check fails.
2. **Sample-rate conversion** – only if ``settings.sample_rate`` differs from
   the project rate. ``scipy.signal.resample_poly`` with the exact rational
   factor ``up/down = target/source`` (reduced by their gcd) and a linear-phase
   Kaiser-windowed FIR (β = :data:`SRC_KAISER_BETA`, ``128·max(up, down) + 1``
   taps, cutoff :data:`SRC_CUTOFF` × the lower of the two Nyquist
   frequencies). Measured response: passband ripple < ±0.0001 dB up to
   0.9 × the lower Nyquist (≈ 19.8 kHz for 44.1 kHz), ≥ 99 dB attenuation
   at and above the lower Nyquist. Edges are zero-padded; output length is
   ``ceil(frames · up / down)``. The whole signal is converted in memory
   (float64, one channel at a time).
3. **Peak analysis** (:func:`analyze_peaks`) of the signal that is about to be
   written (i.e. *after* sample-rate conversion, which can create new overs).
4. **32-bit float export (``FLOAT``)** writes the values as float32 with no
   clipping, no normalisation and no dither; values beyond ±1.0 are kept.
5. **Integer export (``PCM_16`` / ``PCM_24``)** – if any ``|x| > 1.0``,
   ``settings.clip_policy`` decides: ``error`` raises :class:`ClippingError`
   (default; nothing is written); ``reduce_level`` applies one static gain to
   the whole file so its peak lands at :data:`CEILING_DBFS`; ``limit`` runs the
   basic look-ahead peak limiter described in :func:`limiter_gain`;
   ``clip`` hard-clips to ±1.0 because the user explicitly asked for it. When
   nothing exceeds ±1.0 no policy is applied and no gain is changed.
   Then TPDF dither (if ``settings.dither == 'tpdf'``) is added and the result
   is rounded to integers by this module (libsndfile receives int16 / int32
   samples, so its own float→int conversion, which scales by ``2**(b-1) - 1``
   instead of ``2**(b-1)``, is never used and integer sources round-trip
   exactly). Two's-complement PCM cannot represent +1.0 exactly: a value that
   rounds (with dither) one code above the largest positive code, or below
   the most negative one, is held at that code and counted in
   ``ExportResult.saturated_samples`` – this is at most ~1.5 LSB and is
   reported, never silent.

Dither: TPDF (triangular probability density) noise is the sum of two
independent uniform variables of ±0.5 LSB of the *target* bit depth, i.e.
spread over (−1, +1) LSB with RMS 1/√6 LSB. Added before rounding, it makes
the total quantisation error independent of the signal (RMS 0.5 LSB, flat
spectrum) instead of signal-correlated distortion. It is plain white TPDF,
not noise-shaped; it is applied everywhere, including digital silence; it is
never applied to float export. ``settings.seed`` makes it reproducible.

Safety: the file is written to a hidden temporary file in the destination
folder (``.<name>.<random>.partial<ext>``), fsynced (plus ``F_FULLFSYNC`` on
macOS), then moved into place – with a hard link (atomic "create only if
absent") when overwriting is not allowed, or ``os.replace`` when it is. On
any failure the temporary file is deleted and a pre-existing destination is
untouched. An existing destination raises :class:`OverwriteError` unless
``settings.overwrite``; a destination that is (after resolving symlinks, or
the same file by inode) one of ``protected_paths`` – the project's original
source files – is always refused.
"""

from __future__ import annotations

import contextlib
import math
import os
import secrets
import sys
from dataclasses import dataclass, replace
from functools import lru_cache
from typing import Any, Callable, Iterable, TypeVar

import numpy as np
import soundfile as sf
from scipy import ndimage, signal

from .io import MAX_CHANNELS, MAX_SAMPLE_RATE, MIN_SAMPLE_RATE

try:  # POSIX only
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

__all__ = [
    "EXPORT_FORMATS",
    "DITHER_MODES",
    "CLIP_POLICIES",
    "CEILING_DBFS",
    "ExportError",
    "ClippingError",
    "OverwriteError",
    "PeakReport",
    "ExportSettings",
    "ExportResult",
    "analyze_peaks",
    "quantization_step",
    "tpdf_dither",
    "limiter_gain",
    "export_audio",
]

_WANTED_EXPORT_FORMATS = {"WAV": ("PCM_16", "PCM_24", "FLOAT"), "FLAC": ("PCM_16", "PCM_24")}

#: Export containers and subtypes, filtered to what this libsndfile build accepts.
EXPORT_FORMATS: dict[str, tuple[str, ...]] = {
    fmt: subs
    for fmt, subs in (
        (fmt, tuple(s for s in wanted if sf.check_format(fmt, s)))
        for fmt, wanted in _WANTED_EXPORT_FORMATS.items()
        if fmt in sf.available_formats()
    )
    if subs
}

DITHER_MODES = ("none", "tpdf")
CLIP_POLICIES = ("error", "reduce_level", "limit", "clip")

#: Target peak of the ``reduce_level`` and ``limit`` policies.
CEILING_DBFS = -0.1
_CEILING = 10.0 ** (CEILING_DBFS / 20.0)

#: Limiter timing (see :func:`limiter_gain`).
LIMITER_LOOKAHEAD_S = 0.0015
LIMITER_HOLD_S = 0.005
LIMITER_RELEASE_DB_PER_S = 100.0

#: Sample-rate conversion filter (see module docstring).
SRC_KAISER_BETA = 10.0
SRC_HALF_LENGTH_PER_FACTOR = 64
SRC_CUTOFF = 0.95
SRC_MAX_FACTOR = 16384

FLAC_MAX_CHANNELS = 8
FLAC_MAX_SAMPLE_RATE = 655_350
#: RIFF/WAV chunk sizes are 32-bit; keep a margin for header chunks.
WAV_MAX_DATA_BYTES = 0xFFFFFFFF - 65536

_INT_BITS = {"PCM_S8": 8, "PCM_U8": 8, "PCM_16": 16, "PCM_24": 24, "PCM_32": 32}
_FLOAT_BYTES = {"FLOAT": 4, "DOUBLE": 8}
_BLOCK = 1 << 16
_FLOAT32_MAX = float(np.finfo(np.float32).max)

T = TypeVar("T")


# --------------------------------------------------------------------------- errors


class ExportError(Exception):
    """Export failed or was refused. ``str(e)`` is a user-facing message."""

    def __init__(self, message: str, path: str | None = None):
        super().__init__(message)
        self.message = message
        self.path = path


class ClippingError(ExportError):
    """Integer export with ``clip_policy='error'`` and samples beyond full scale."""

    def __init__(self, message: str, report: "PeakReport", path: str | None = None):
        super().__init__(message, path)
        self.report = report


class OverwriteError(ExportError):
    """Destination exists and ``ExportSettings.overwrite`` is False."""


# --------------------------------------------------------------------------- peaks


@dataclass(frozen=True)
class PeakReport:
    """Sample-peak statistics (no inter-sample / true-peak estimation).

    ``samples_over`` counts individual samples (frame × channel) with
    ``|x| > 1.0``, i.e. beyond integer full scale.
    """

    peak: float
    peak_dbfs: float
    channel_peaks: tuple[float, ...]
    samples_over: int

    @property
    def clips_integer(self) -> bool:
        """True iff some ``|x| > 1.0`` (would clip in an integer format).

        Exactly 1.0 does not count; it is written as the largest positive code
        (see the module docstring on saturation).
        """
        return self.samples_over > 0


def _as_float_2d(data: Any, what: str = "audio data") -> np.ndarray:
    arr = data if isinstance(data, np.ndarray) else np.asarray(data)
    if arr.ndim != 2:
        raise ExportError(f"{what} must be a 2-D (channels, frames) array; got shape {arr.shape}")
    if not np.issubdtype(arr.dtype, np.floating):
        raise ExportError(f"{what} must be floating point; got dtype {arr.dtype}")
    return arr


def analyze_peaks(data: Any) -> PeakReport:
    """Per-channel sample peaks of ``(channels, frames)`` float data.

    Raises :class:`ExportError` ("non-finite samples") if any value is NaN or
    infinite. Processed in blocks, so no full-size temporary is allocated.
    """
    arr = _as_float_2d(data)
    channels, frames = arr.shape
    chan = np.zeros(channels, dtype=np.float64)
    over = 0
    for a in range(0, frames, _BLOCK):
        blk = np.abs(arr[:, a : a + _BLOCK])
        m = blk.max(axis=1)
        if not np.all(np.isfinite(m)):
            bad = [int(c) for c in np.flatnonzero(~np.isfinite(m))]
            raise ExportError(
                f"Cannot export: non-finite samples (NaN or infinity) in channel(s) {bad} "
                f"near frame {a}."
            )
        np.maximum(chan, m, out=chan)
        over += int(np.count_nonzero(blk > 1.0))
    peak = float(chan.max()) if channels else 0.0
    dbfs = 20.0 * math.log10(peak) if peak > 0.0 else float("-inf")
    return PeakReport(
        peak=peak,
        peak_dbfs=dbfs,
        channel_peaks=tuple(float(x) for x in chan),
        samples_over=over,
    )


# --------------------------------------------------------------------------- settings / result


def _check_rate(rate: Any, what: str) -> int:
    if isinstance(rate, bool) or not isinstance(rate, (int, np.integer)):
        raise ExportError(f"{what} must be a positive integer number of Hz, got {rate!r}")
    rate = int(rate)
    if rate <= 0:
        raise ExportError(f"{what} must be positive, got {rate}")
    if rate < MIN_SAMPLE_RATE or rate > MAX_SAMPLE_RATE:
        raise ExportError(f"{what} {rate} Hz is outside the supported range {MIN_SAMPLE_RATE}–{MAX_SAMPLE_RATE} Hz")
    return rate


@dataclass(frozen=True)
class ExportSettings:
    """Export options. ``validated()`` returns a normalised copy or raises :class:`ExportError`.

    * ``format`` / ``subtype``: see :data:`EXPORT_FORMATS` (case-insensitive).
    * ``dither``: ``'none'`` or ``'tpdf'``; only used for integer subtypes.
    * ``clip_policy``: ``'error'`` | ``'reduce_level'`` | ``'limit'`` | ``'clip'``;
      only used for integer subtypes and only when some ``|x| > 1.0``.
    * ``sample_rate``: ``None`` keeps the project rate; otherwise convert.
    * ``overwrite``: allow replacing an existing (non-protected) destination.
    * ``seed``: seed for the dither RNG (``None`` = fresh entropy).
    """

    format: str = "WAV"
    subtype: str = "FLOAT"
    dither: str = "tpdf"
    clip_policy: str = "error"
    sample_rate: int | None = None
    overwrite: bool = False
    seed: int | None = None

    def validated(self) -> "ExportSettings":
        if not isinstance(self.format, str) or self.format.upper() not in EXPORT_FORMATS:
            raise ExportError(
                f"Unsupported export format {self.format!r}; choose one of {', '.join(EXPORT_FORMATS)}"
            )
        fmt = self.format.upper()
        if not isinstance(self.subtype, str) or self.subtype.upper() not in EXPORT_FORMATS[fmt]:
            raise ExportError(
                f"{fmt} export does not support subtype {self.subtype!r}; "
                f"choose one of {', '.join(EXPORT_FORMATS[fmt])}"
            )
        subtype = self.subtype.upper()
        if not isinstance(self.dither, str) or self.dither.lower() not in DITHER_MODES:
            raise ExportError(f"Unknown dither {self.dither!r}; choose one of {', '.join(DITHER_MODES)}")
        if not isinstance(self.clip_policy, str) or self.clip_policy.lower() not in CLIP_POLICIES:
            raise ExportError(
                f"Unknown clip policy {self.clip_policy!r}; choose one of {', '.join(CLIP_POLICIES)}"
            )
        rate = None if self.sample_rate is None else _check_rate(self.sample_rate, "Export sample rate")
        if not isinstance(self.overwrite, bool):
            raise ExportError(f"overwrite must be True or False, got {self.overwrite!r}")
        if self.seed is not None and (
            isinstance(self.seed, bool) or not isinstance(self.seed, (int, np.integer)) or self.seed < 0
        ):
            raise ExportError(f"seed must be None or a non-negative integer, got {self.seed!r}")
        return replace(
            self,
            format=fmt,
            subtype=subtype,
            dither=self.dither.lower(),
            clip_policy=self.clip_policy.lower(),
            sample_rate=rate,
        )


@dataclass(frozen=True)
class ExportResult:
    """What was written.

    * ``gain_applied_db``: static gain of ``reduce_level`` (0.0 otherwise).
    * ``limited_samples``: samples (frame × channel) the limiter turned down.
    * ``clipped_samples``: samples hard-clipped by ``clip_policy='clip'``.
    * ``saturated_samples``: samples held at the largest/smallest integer code
      after dither and rounding (≤ ~1.5 LSB; see module docstring). With
      ``clip_policy='clip'`` this overlaps ``clipped_samples``: samples clipped
      to +1.0 are then held at the largest positive code.
    * ``peak_before``: peaks of the signal entering the clip policy (after
      sample-rate conversion, before gain / limiting / clipping / dither).
    """

    path: str
    frames: int
    sample_rate: int
    channels: int
    subtype: str
    gain_applied_db: float
    limited_samples: int
    clipped_samples: int
    dithered: bool
    peak_before: PeakReport
    format: str
    saturated_samples: int


def quantization_step(subtype: str) -> float | None:
    """Size of one LSB in full-scale units (``2**-(bits-1)``), ``None`` for float subtypes."""
    key = str(subtype).upper()
    if key in _INT_BITS:
        return 2.0 ** -(_INT_BITS[key] - 1)
    if key in _FLOAT_BYTES:
        return None
    raise ExportError(f"Unknown sample subtype {subtype!r}")


# --------------------------------------------------------------------------- dither / limiter


def tpdf_dither(shape: Any, rng: np.random.Generator) -> np.ndarray:
    """TPDF dither in LSB units: ``U(0,1) − U(0,1)``, range (−1, 1), variance 1/6."""
    noise = rng.random(shape)
    noise -= rng.random(shape)
    return noise


def limiter_gain(data: np.ndarray, ceiling: float, sample_rate: int) -> tuple[int, np.ndarray | None]:
    """Gain curve of a basic, offline, look-ahead peak limiter.

    The channels are linked (one gain for all channels, so the stereo image
    does not shift). For the frame peak ``p[n] = max_c |x[c, n]|`` the required
    gain is ``r[n] = min(1, ceiling / p[n])``. The curve is then built so that
    ``gain[n] <= r[n]`` holds at every sample (the output never exceeds
    ``ceiling``):

    1. sliding minimum of ``r`` over ``[n - hold, n + lookahead]``;
    2. moving average over ``[n - lookahead, n]`` – a linear attack ramp that
       starts :data:`LIMITER_LOOKAHEAD_S` before each peak (the minimum filter
       guarantees the average is still ≤ ``r`` at the peak);
    3. release: the gain may rise by at most :data:`LIMITER_RELEASE_DB_PER_S`
       (linear in dB) after the :data:`LIMITER_HOLD_S` hold.

    It is a peak (not true-peak) limiter with no program-dependent release, no
    soft knee and no oversampling; heavy limiting will be audible. Samples
    outside the influence of any over get a gain of exactly 1.0.

    Returns ``(start, gain)`` where ``gain`` covers frames
    ``[start, start + len(gain))`` and is 1.0 everywhere else, or
    ``(0, None)`` if no frame exceeds ``ceiling``.
    """
    channels, n = data.shape
    first = last = -1
    for a in range(0, n, _BLOCK):
        hot = np.flatnonzero((np.abs(data[:, a : a + _BLOCK]) > ceiling).any(axis=0))
        if hot.size:
            if first < 0:
                first = a + int(hot[0])
            last = a + int(hot[-1])
    if first < 0:
        return 0, None

    look = max(1, int(round(LIMITER_LOOKAHEAD_S * sample_rate)))
    hold = max(0, int(round(LIMITER_HOLD_S * sample_rate)))
    rate_ln = LIMITER_RELEASE_DB_PER_S / sample_rate * math.log(10.0) / 20.0  # ln(gain) per sample

    lo = max(0, first - look - 1)
    peak = 0.0
    for a in range(first, last + 1, _BLOCK):
        peak = max(peak, float(np.abs(data[:, a : min(a + _BLOCK, last + 1)]).max()))
    release = int(math.ceil(math.log(peak / ceiling) / rate_ln)) + 1
    hi = min(n, last + hold + look + release + 2)
    span = hi - lo

    # r: required gain per frame (1.0 where the frame is below the ceiling)
    r = np.empty(span, dtype=np.float64)
    for a in range(0, span, _BLOCK):
        b = min(a + _BLOCK, span)
        r[a:b] = np.abs(data[:, lo + a : lo + b]).max(axis=0)
    hot = r > ceiling
    np.divide(ceiling, r, out=r, where=hot)
    r[~hot] = 1.0
    del hot

    # 1. hold/look-ahead minimum over [n - hold, n + look] (exact)
    width = hold + look + 1
    dev = ndimage.minimum_filter1d(r, size=width, mode="constant", cval=1.0, origin=hold - width // 2)
    # 2. attack: mean over [n - look, n], computed on the deviation from unity so
    #    that regions without gain reduction stay exactly 1.0
    np.subtract(1.0, dev, out=dev)
    csum = np.empty(span + 1, dtype=np.float64)
    csum[0] = 0.0
    np.cumsum(dev, out=csum[1:])
    attack = dev  # reuse buffer
    attack[:] = csum[1:]
    if span > look:
        attack[look:] -= csum[: span - look]
    del csum
    attack /= look + 1
    np.subtract(1.0, attack, out=attack)
    np.minimum(attack, r, out=attack)  # numerical guard (rounding in the running sum)
    del r
    # 3. release: log-gain may rise by at most rate_ln per sample
    gain = np.log(attack)
    ramp = np.arange(span, dtype=np.float64)
    ramp *= rate_ln
    gain -= ramp
    np.minimum.accumulate(gain, out=gain)
    gain += ramp
    del ramp
    np.exp(gain, out=gain)
    np.minimum(gain, attack, out=gain)
    return lo, gain


# --------------------------------------------------------------------------- sample-rate conversion


def _ratio(src: int, dst: int) -> tuple[int, int]:
    g = math.gcd(src, dst)
    return dst // g, src // g


def _resampled_length(frames: int, up: int, down: int) -> int:
    total = frames * up
    return total // down + (1 if total % down else 0)


@lru_cache(maxsize=16)
def _src_filter(up: int, down: int) -> np.ndarray:
    m = max(up, down)
    half = SRC_HALF_LENGTH_PER_FACTOR * m
    h = signal.firwin(2 * half + 1, SRC_CUTOFF / m, window=("kaiser", SRC_KAISER_BETA))
    h.setflags(write=False)
    return h


def _resample(arr: np.ndarray, up: int, down: int) -> np.ndarray:
    h = _src_filter(up, down)
    channels, frames = arr.shape
    out = np.empty((channels, _resampled_length(frames, up, down)), dtype=np.float64)
    for c in range(channels):
        out[c] = signal.resample_poly(np.asarray(arr[c], dtype=np.float64), up, down, window=h)
    return out


# --------------------------------------------------------------------------- destination / atomic write


def _normalise_dest(path: Any) -> str:
    try:
        p = os.fspath(path)
    except TypeError:
        raise ExportError(f"Not a file path: {path!r}") from None
    if isinstance(p, bytes):
        p = os.fsdecode(p)
    if not p:
        raise ExportError("No destination path given")
    return os.path.abspath(p)


def _is_protected(dest: str, protected: Iterable[Any]) -> bool:
    dest_real = os.path.normcase(os.path.realpath(dest))
    dest_exists = os.path.exists(dest)
    for item in protected:
        try:
            p = os.fspath(item)
        except TypeError:
            raise ExportError(f"protected_paths must contain file paths, got {item!r}") from None
        if isinstance(p, bytes):
            p = os.fsdecode(p)
        if os.path.normcase(os.path.realpath(p)) == dest_real:
            return True
        if dest_exists:
            with contextlib.suppress(OSError):
                if os.path.samefile(dest, p):
                    return True
    return False


def _check_destination(dest: str, overwrite: bool, protected: Iterable[Any]) -> None:
    name = os.path.basename(dest)
    if os.path.isdir(dest):
        raise ExportError(f"Cannot export to '{name}': it is a folder.", dest)
    parent = os.path.dirname(dest)
    if not os.path.isdir(parent):
        raise ExportError(f"Cannot export: the folder '{parent}' does not exist.", dest)
    if _is_protected(dest, protected):
        raise ExportError(f"Refusing to overwrite an original source file: '{dest}'.", dest)
    if os.path.lexists(dest) and not overwrite:
        raise OverwriteError(f"'{name}' already exists; enable overwrite to replace it.", dest)


def _create_temp(directory: str, base: str) -> tuple[int, str]:
    ext = os.path.splitext(base)[1]
    stem = base.encode("utf-8")[:96].decode("utf-8", "ignore")
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    for _ in range(64):
        tmp = os.path.join(directory, f".{stem}.{secrets.token_hex(6)}.partial{ext}")
        try:
            return os.open(tmp, flags, 0o666), tmp  # mode is filtered by the umask
        except FileExistsError:
            continue
    raise ExportError(f"Could not create a temporary file in '{directory}'.")


def _fsync_fd(fd: int) -> None:
    os.fsync(fd)
    if sys.platform == "darwin" and fcntl is not None and hasattr(fcntl, "F_FULLFSYNC"):
        with contextlib.suppress(OSError):  # not supported on every filesystem
            fcntl.fcntl(fd, fcntl.F_FULLFSYNC)


def _fsync_dir(directory: str) -> None:
    if os.name != "posix":
        return
    with contextlib.suppress(OSError):
        dfd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)


def _commit(tmp: str, dest: str, overwrite: bool) -> None:
    if overwrite:
        os.replace(tmp, dest)
        return
    try:
        os.link(tmp, dest)  # atomic: fails if dest appeared meanwhile
    except FileExistsError:
        raise OverwriteError(f"'{os.path.basename(dest)}' already exists; enable overwrite to replace it.", dest)
    except OSError:
        # Filesystem without hard links (FAT/exFAT, some network shares).
        if os.path.lexists(dest):
            raise OverwriteError(
                f"'{os.path.basename(dest)}' already exists; enable overwrite to replace it.", dest
            ) from None
        os.replace(tmp, dest)
        return
    with contextlib.suppress(OSError):
        os.unlink(tmp)


def _atomic_write(dest: str, overwrite: bool, write: Callable[[int], T]) -> T:
    directory, base = os.path.split(dest)
    fd, tmp = _create_temp(directory, base)
    pending: str | None = tmp
    try:
        try:
            result = write(fd)
            _fsync_fd(fd)
        finally:
            os.close(fd)
        _commit(tmp, dest, overwrite)
        pending = None
    finally:
        if pending is not None:
            with contextlib.suppress(OSError):
                os.unlink(pending)
    _fsync_dir(directory)
    return result


# --------------------------------------------------------------------------- export


def _check_container(settings: ExportSettings, channels: int, rate: int, frames: int, dest: str) -> None:
    if settings.format == "FLAC":
        if channels > FLAC_MAX_CHANNELS:
            raise ExportError(
                f"FLAC supports at most {FLAC_MAX_CHANNELS} channels; this audio has {channels}. Export as WAV instead.",
                dest,
            )
        if rate > FLAC_MAX_SAMPLE_RATE:
            raise ExportError(f"FLAC supports sample rates up to {FLAC_MAX_SAMPLE_RATE} Hz, not {rate} Hz.", dest)
    if settings.format == "WAV":
        bits = _INT_BITS.get(settings.subtype)
        width = bits // 8 if bits else _FLOAT_BYTES[settings.subtype]
        nbytes = frames * channels * width
        if nbytes > WAV_MAX_DATA_BYTES:
            raise ExportError(
                f"The export would contain {nbytes / 2**30:.2f} GiB of sample data; WAV files are limited "
                "to 4 GiB. Export as FLAC, use a smaller sample format, or export a shorter range.",
                dest,
            )


def export_audio(
    data: Any,
    sample_rate: int,
    path: Any,
    settings: ExportSettings | None = None,
    protected_paths: Iterable[Any] = (),
) -> ExportResult:
    """Write ``(channels, frames)`` float ``data`` to ``path``. See the module docstring.

    Raises :class:`ExportError` (or its subclasses :class:`ClippingError`,
    :class:`OverwriteError`); on any failure nothing is left behind and an
    existing destination file is unchanged.
    """
    if settings is None:
        settings = ExportSettings()
    if not isinstance(settings, ExportSettings):
        raise ExportError("settings must be an ExportSettings instance")
    s = settings.validated()
    if isinstance(protected_paths, (str, bytes, os.PathLike)):
        protected_paths = (protected_paths,)
    protected = tuple(protected_paths)

    arr = _as_float_2d(data)
    channels, in_frames = arr.shape
    if channels < 1 or channels > MAX_CHANNELS:
        hint = " (is the array (frames, channels) instead of (channels, frames)?)" if in_frames <= MAX_CHANNELS else ""
        raise ExportError(f"Cannot export {channels} channels; 1–{MAX_CHANNELS} are supported{hint}.")
    if in_frames <= 0:
        raise ExportError("Cannot export: there are no audio frames.")
    src_rate = _check_rate(sample_rate, "Project sample rate")
    out_rate = src_rate if s.sample_rate is None else s.sample_rate
    up, down = _ratio(src_rate, out_rate)
    if max(up, down) > SRC_MAX_FACTOR:
        raise ExportError(
            f"Converting {src_rate} Hz to {out_rate} Hz needs the ratio {up}:{down}, which is too complex "
            f"(largest factor {SRC_MAX_FACTOR}); choose a standard sample rate."
        )
    out_frames = _resampled_length(in_frames, up, down)
    dest = _normalise_dest(path)
    _check_container(s, channels, out_rate, out_frames, dest)
    _check_destination(dest, s.overwrite, protected)

    peaks = analyze_peaks(arr)  # also rejects NaN / inf before any work
    if (up, down) != (1, 1):
        work = _resample(arr, up, down)
        peaks = analyze_peaks(work)
    else:
        work = arr

    step = quantization_step(s.subtype)
    gain = 1.0
    lim_start, lim_gain = 0, None
    hard_clip = False
    clipped = 0
    limited = 0

    if step is None:
        if peaks.peak > _FLOAT32_MAX:
            raise ExportError("Cannot export: sample values exceed the 32-bit float range.", dest)
        dithered = False
    else:
        dithered = s.dither == "tpdf"
        if peaks.clips_integer:
            # Target that keeps the *written* integers at or below CEILING_DBFS
            # even after rounding (0.5 LSB) and dither (< 1 LSB).
            target = _CEILING - ((1.0 if dithered else 0.0) + 0.5) * step
            if s.clip_policy == "error":
                raise ClippingError(
                    f"Export would clip: peak {peaks.peak_dbfs:+.2f} dBFS, {peaks.samples_over} samples above "
                    f"full scale cannot be represented in {s.subtype}. Choose 'reduce level', 'limit' or "
                    "'clip', or export 32-bit float.",
                    peaks,
                    dest,
                )
            if s.clip_policy == "reduce_level":
                gain = target / peaks.peak
            elif s.clip_policy == "limit":
                lim_start, lim_gain = limiter_gain(work, target, out_rate)
                if lim_gain is not None:
                    limited = int(np.count_nonzero(lim_gain < 1.0)) * channels
            else:  # "clip"
                hard_clip = True
                clipped = peaks.samples_over

    def write(fd: int) -> int:
        saturated = 0
        rng = np.random.default_rng(s.seed) if dithered else None
        if step is not None:
            scale = 1.0 / step
            lo_code, hi_code = -scale, scale - 1.0
        with sf.SoundFile(
            fd, mode="w", samplerate=out_rate, channels=channels, format=s.format, subtype=s.subtype, closefd=False
        ) as f:
            for a in range(0, out_frames, _BLOCK):
                b = min(a + _BLOCK, out_frames)
                if step is None:
                    f.write(np.ascontiguousarray(work[:, a:b].T, dtype=np.float32))
                    continue
                y = np.array(work[:, a:b], dtype=np.float64)
                if gain != 1.0:
                    y *= gain
                if lim_gain is not None:
                    i0, i1 = max(a, lim_start), min(b, lim_start + lim_gain.size)
                    if i0 < i1:
                        y[:, i0 - a : i1 - a] *= lim_gain[i0 - lim_start : i1 - lim_start]
                if hard_clip:
                    np.clip(y, -1.0, 1.0, out=y)
                y *= scale
                if rng is not None:
                    y += tpdf_dither(y.shape, rng)
                np.rint(y, out=y)
                saturated += int(np.count_nonzero(y > hi_code)) + int(np.count_nonzero(y < lo_code))
                np.clip(y, lo_code, hi_code, out=y)
                if s.subtype == "PCM_16":
                    q = y.astype(np.int16)
                else:  # PCM_24: libsndfile takes the top 24 bits of an int32
                    q = y.astype(np.int32)
                    np.left_shift(q, 8, out=q)
                f.write(np.ascontiguousarray(q.T))
        return saturated

    try:
        saturated = _atomic_write(dest, s.overwrite, write)
    except ExportError:
        raise
    except (RuntimeError, OSError, ValueError, TypeError, MemoryError) as e:
        detail = getattr(e, "error_string", None) or getattr(e, "strerror", None) or str(e)
        raise ExportError(f"Writing '{os.path.basename(dest)}' failed: {detail}", dest) from e

    return ExportResult(
        path=dest,
        frames=out_frames,
        sample_rate=out_rate,
        channels=channels,
        subtype=s.subtype,
        gain_applied_db=20.0 * math.log10(gain),
        limited_samples=limited,
        clipped_samples=clipped,
        dithered=dithered,
        peak_before=peaks,
        format=s.format,
        saturated_samples=saturated,
    )
