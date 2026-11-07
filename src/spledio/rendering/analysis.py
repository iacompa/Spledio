"""Display spectrogram analysis: tiled, cached, level-of-detail STFT magnitudes.

Everything here is *display only*. The analyser reads audio through a provider
callable and never writes to it; display settings (colour map, dB range, gamma,
zoom) never reach this module, so changing them can never modify audio and
never re-runs the FFT – the UI re-colours the cached dB data of a
:class:`ViewData` with :func:`spledio.rendering.image.colorize`.

Grid and levels of detail
-------------------------
Analysis uses the shared STFT grid of :mod:`spledio.dsp.stft` for the display
config: frame ``k`` is centred on sample ``k*hop``, bin ``b`` is at
``b*sr/n_fft`` Hz, windows are periodic. At *stride* ``s`` (a power of two) only
frames ``k = s*j`` are analysed – honest decimation: every displayed column is
a real, complete FFT of real audio; nothing is interpolated in time. (A
consequence: at coarse strides an event shorter than the gap between analysed
frames, ``s*hop - n_fft`` samples, can fall between frames and not appear.)
Stride-1 tiles are computed with :func:`spledio.dsp.stft.stft` itself; strided
tiles use the same window and framing, batched.

Tiles and cache
---------------
A tile is ``TILE_FRAMES`` consecutive analysed frames ``j = tile*TILE_FRAMES ...``
at one stride. Tiles are stored as read-only ``float32`` **dB** arrays of shape
``(frames, n_bins)`` (dB is monotonic in magnitude, so max-pooling dB equals
max-pooling magnitude). The cache key is
``(settings, stride, tile_index, audio_version)`` where ``settings`` carries
``n_fft, hop, window, channel_mode``. The cache is an LRU bounded by bytes.

Level
-----
``dB = 20*log10(|X| / magnitude_reference(config) + 1e-12)`` – dB re
full-scale sine, display only, not calibrated loudness. A full-scale (amplitude
1.0) sine centred on a bin reads 0 dB; amplitude 0.5 reads -6.02 dB. The
smallest possible value is :data:`DB_FLOOR` (-240 dB). ``channel_mode='mix'``
uses the mean of the per-channel **power** ``|X_c|^2`` (so a signal present on
one of two channels reads 3 dB lower than on its own channel); ``'chN'`` shows
channel ``N`` only.

Rows
----
Each view row covers a frequency span ``[f_a, f_b)`` (see
:func:`spledio.rendering.scales.row_edges_hz`; the top row is closed). If the
span contains at least one bin centre the row shows the **maximum** over those
bins, which keeps narrow resonances visible when zoomed out. Otherwise (span
narrower than a bin, between two bin centres) the row shows a linear
interpolation (in dB) between the two neighbouring bins at the row-centre
frequency – display-only interpolation that creates no new information. Taking
the bin value whenever a bin centre falls inside a row also means a peak's
displayed level never depends on zoom.

Threads
-------
Tiles are computed on a ``concurrent.futures`` thread executor (the default is
a private ``ThreadPoolExecutor``; a process pool is not supported).
:meth:`SpectrogramAnalyzer.view_data` never blocks on analysis: missing tiles
are requested and reported as ``PENDING``. ``on_tile_ready`` is called **from a
worker thread** after each tile completes (successfully or not); the UI must
marshal it to the GUI thread (e.g. emit a Qt signal) and call ``view_data``
again. Internal dictionaries are guarded by one lock that is held only for
bookkeeping, never while reading audio or running FFTs. A worker exception
marks the tile ``FAILED`` (never fabricated data); failed tiles are retried on
the next request after :meth:`~SpectrogramAnalyzer.invalidate`,
:meth:`~SpectrogramAnalyzer.set_audio` or
:meth:`~SpectrogramAnalyzer.retry_failed`.
"""

from __future__ import annotations

import concurrent.futures as cf
import logging
import math
import os
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Callable

import numpy as np
import scipy.fft as sfft

from spledio.dsp.stft import STFTConfig, full_frame_range, get_window, magnitude_reference, stft
from spledio.rendering.scales import (
    FrequencyScale,
    column_centre_times,
    nearest_index,
    row_centres_hz,
    row_edges_hz,
)

__all__ = [
    "TILE_FRAMES",
    "DB_EPSILON",
    "DB_FLOOR",
    "TileStatus",
    "DisplayAnalysisSettings",
    "ViewData",
    "SpectrogramAnalyzer",
    "AudioProvider",
]

log = logging.getLogger(__name__)

TILE_FRAMES = 128
DB_EPSILON = 1e-12
DB_FLOOR = float(20.0 * math.log10(DB_EPSILON))  # -240 dB: value of digital silence / outside the file
_MAX_STRIDE = 1 << 24
# Strided tiles whose analysed frames are at most this many FFT lengths apart are
# fetched as one contiguous block; sparser ones fetch each frame separately so a
# zoomed-out view of a long file never pulls the whole file into memory.
_CONTIGUOUS_GAP_FFTS = 2

AudioProvider = Callable[[int, int], np.ndarray]
_CHANNEL_RE = re.compile(r"ch(\d+)")


class TileStatus(IntEnum):
    READY = 0
    PENDING = 1
    FAILED = 2
    OUTSIDE = 3  # column centre outside [0, duration): no audio there (db = DB_FLOOR)
    # the tile's audio changed and it is being recomputed; ``db`` holds its previous analysis so an
    # edit does not make the picture blink (only with ``view_data(..., stale_ok=True)``)
    STALE = 4


@dataclass(frozen=True)
class DisplayAnalysisSettings:
    """Display analysis parameters. Independent of any edit's STFT config."""

    config: STFTConfig = STFTConfig(4096, 1024, "hann")
    channel_mode: str = "mix"  # 'mix' = mean of per-channel power, or 'ch0', 'ch1', ...

    def __post_init__(self):
        self.config.validate()
        if self.channel_mode != "mix" and not _CHANNEL_RE.fullmatch(self.channel_mode):
            raise ValueError(f"channel_mode must be 'mix' or 'chN', got {self.channel_mode!r}")

    def channel_indices(self, n_channels: int) -> tuple[int, ...]:
        if self.channel_mode == "mix":
            return tuple(range(n_channels))
        idx = int(self.channel_mode[2:])
        if idx >= n_channels:
            raise ValueError(f"channel_mode {self.channel_mode!r} but the audio has {n_channels} channel(s)")
        return (idx,)


@dataclass(frozen=True, eq=False)
class ViewData:
    """Analysis of one view, ready for colouring.

    ``db``: ``float32 (height, width)``, row 0 = top = highest frequency. Only
    ``READY`` pixels carry data; ``PENDING``/``FAILED`` pixels are NaN (no data,
    never fabricated) and ``OUTSIDE`` pixels are :data:`DB_FLOOR`.
    ``status``: ``uint8 (height, width)`` of :class:`TileStatus`.
    ``frame_index``: ``int64 (width,)`` processing-grid frame ``k`` shown in each
    column (``-1`` outside the audio).
    """

    db: np.ndarray
    status: np.ndarray
    frame_index: np.ndarray
    stride: int
    settings: DisplayAnalysisSettings
    version: int = 0
    sample_rate: float = 0.0
    t0: float = 0.0
    t1: float = 0.0
    f_lo: float = 0.0
    f_hi: float = 0.0
    pending_tiles: int = 0
    failed_tiles: int = 0
    stale_tiles: int = 0

    @property
    def column_status(self) -> np.ndarray:
        return self.status[0]

    @property
    def complete(self) -> bool:
        """True when no pixel is PENDING or STALE (FAILED pixels count as complete)."""
        cs = self.column_status
        return not bool(np.any((cs == TileStatus.PENDING) | (cs == TileStatus.STALE)))


@dataclass(frozen=True)
class _Audio:
    provider: AudioProvider
    n_frames: int
    channels: int
    sample_rate: float
    generation: int


class _Task:
    __slots__ = ("tkey", "settings", "stride", "tile", "version", "audio", "stale", "future")

    def __init__(self, tkey, settings, stride, tile, version, audio):
        self.tkey = tkey
        self.settings = settings
        self.stride = stride
        self.tile = tile
        self.version = version
        self.audio = audio
        self.stale = False
        self.future: cf.Future | None = None


@dataclass(frozen=True)
class _Outcome:
    key: tuple | None  # full cache key the result belongs to (None if discarded)
    db: np.ndarray | None
    failed: bool


_FAILED = object()


@dataclass
class _Plan:
    settings: DisplayAnalysisSettings
    audio: _Audio | None
    version: int
    stride: int
    inside: np.ndarray  # (W,) bool
    j: np.ndarray  # (W,) analysed-frame index at stride (valid where inside)
    frame_index: np.ndarray  # (W,) k or -1
    tiles: np.ndarray  # unique tile indices needed
    t0: float = 0.0
    t1: float = 0.0
    extra_tiles: list = field(default_factory=list)


def _default_workers() -> int:
    return max(2, min(8, (os.cpu_count() or 4) // 2))


def _j_max(n_frames: int, cfg: STFTConfig, stride: int) -> int:
    """Largest analysed-frame index ``j`` (frame ``k = stride*j``) inside the full frame range."""
    k_stop = full_frame_range(n_frames, cfg.n_fft, cfg.hop)[1]
    return (k_stop - 1) // stride


def _tile_affected(cfg: STFTConfig, stride: int, tile: int, start: int, end: int) -> bool:
    """Does any analysed frame of ``tile`` have support overlapping samples ``[start, end)``?

    Frame ``k`` covers ``[k*hop - n_fft/2, k*hop + n_fft/2)``, i.e. it is affected
    iff its centre lies in ``(start - n_fft/2, end + n_fft/2)``.
    """
    if end <= start:
        return False
    ka, kb = cfg.frames_touching(start, end)
    ja = -(-ka // stride)
    jb = -(-kb // stride)
    lo = max(ja, tile * TILE_FRAMES)
    hi = min(jb, (tile + 1) * TILE_FRAMES)
    return hi > lo


def _fetch(audio: _Audio, a: int, b: int) -> np.ndarray:
    x = np.asarray(audio.provider(int(a), int(b)))
    if x.ndim == 1:
        x = x[None, :]
    if x.shape != (audio.channels, b - a):
        raise ValueError(f"audio provider returned shape {x.shape} for [{a}, {b}); expected {(audio.channels, b - a)}")
    if not np.issubdtype(x.dtype, np.floating):
        raise TypeError(f"audio provider must return floating-point samples, got {x.dtype}")
    return x


def compute_tile_db(audio: _Audio, settings: DisplayAnalysisSettings, stride: int, tile: int) -> np.ndarray:
    """Analyse one tile; returns read-only ``float32 (frames, n_bins)`` dB. Raises on any problem."""
    cfg = settings.config
    n_fft, hop, half = cfg.n_fft, cfg.hop, cfg.half
    chans = settings.channel_indices(audio.channels)
    j0 = tile * TILE_FRAMES
    j1 = min(j0 + TILE_FRAMES, _j_max(audio.n_frames, cfg, stride) + 1)
    m = j1 - j0
    if m <= 0 or j0 < 0:
        raise ValueError(f"tile {tile} at stride {stride} is outside the audio")
    k_first = j0 * stride
    power = np.zeros((m, cfg.n_bins), dtype=np.float64)
    if stride == 1:
        a = k_first * hop - half
        b = (k_first + m - 1) * hop + half
        seg = _fetch(audio, a, b)
        kl = half // hop  # local index of frame k_first within seg
        for c in chans:
            X = stft(seg[c], cfg, kl, kl + m)
            power += X.real * X.real + X.imag * X.imag
    else:
        w = get_window(cfg.window, n_fft)
        step = stride * hop
        if step <= _CONTIGUOUS_GAP_FFTS * n_fft:
            a = k_first * hop - half
            b = (k_first + (m - 1) * stride) * hop + half
            seg = _fetch(audio, a, b)
            for c in chans:
                frames = np.lib.stride_tricks.sliding_window_view(seg[c], n_fft)[::step]
                X = sfft.rfft(frames * w, axis=-1)
                power += X.real * X.real + X.imag * X.imag
        else:
            frames = np.empty((len(chans), m, n_fft), dtype=np.float64)
            for i in range(m):
                a = (k_first + i * stride) * hop - half
                frames[:, i, :] = _fetch(audio, a, a + n_fft)[list(chans)]
            frames *= w
            X = sfft.rfft(frames, axis=-1)
            power += (X.real * X.real + X.imag * X.imag).sum(axis=0)
    if len(chans) > 1:
        power /= len(chans)
    ref = magnitude_reference(cfg)
    db = np.sqrt(power)
    db *= 1.0 / ref
    db += DB_EPSILON
    np.log10(db, out=db)
    db *= 20.0
    out = db.astype(np.float32)
    out.setflags(write=False)
    return out


class _RowPlan:
    """Row -> bin mapping for one (scale, view range, height, bin grid).

    Max rows use a sparse-table range maximum: level ``L`` holds
    ``max(x[b : b + 2**L])``, so a group ``[lo, hi)`` is
    ``max(level[lo], level[hi - 2**L])`` with ``2**L <= hi - lo < 2**(L+1)`` –
    a few whole-array ``np.maximum`` passes instead of one tiny reduction per
    (row, column) pair. Output rows are top-first (row 0 = highest frequency).
    """

    __slots__ = ("height", "bmin", "bmax", "levels", "interp_out", "b0", "frac")

    def __init__(self, scale: FrequencyScale, f_lo, f_hi, height: int, df: float, n_bins: int):
        edges = row_edges_hz(scale, height, f_lo, f_hi) / df  # ascending, in bins
        centres = row_centres_hz(scale, height, f_lo, f_hi) / df
        lo = np.ceil(edges[:-1]).astype(np.int64)
        hi = np.ceil(edges[1:]).astype(np.int64)
        hi[-1] = int(math.floor(edges[-1])) + 1  # top row is closed: include a bin exactly at f_hi
        lo = np.clip(lo, 0, n_bins)
        hi = np.clip(hi, 0, n_bins)
        hi = np.maximum(hi, lo)
        is_max = hi > lo
        out_row = (height - 1) - np.arange(height)  # ascending index -> image row
        b0 = np.clip(np.floor(centres).astype(np.int64), 0, n_bins - 2)
        frac = np.clip(centres - b0, 0.0, 1.0).astype(np.float32)
        self.height = int(height)
        lo_c, hi_c = [], []
        if is_max.any():
            lo_c.append(int(lo[is_max].min()))
            hi_c.append(int(hi[is_max].max()))
        if (~is_max).any():
            lo_c.append(int(b0[~is_max].min()))
            hi_c.append(int(b0[~is_max].max()) + 2)
        self.bmin, self.bmax = min(lo_c), max(hi_c)
        # per sparse-table level: (image rows, lo, hi - 2**level) relative to bmin
        self.levels = []
        if is_max.any():
            mlo, mhi, mrow = lo[is_max] - self.bmin, hi[is_max] - self.bmin, out_row[is_max]
            lev = np.floor(np.log2(mhi - mlo)).astype(np.int64)
            for L in range(int(lev.max()) + 1):
                sel = lev == L
                self.levels.append((mrow[sel], mlo[sel], mhi[sel] - (1 << L), int(mlo[lev >= L].min()) if (lev >= L).any() else 0))
        self.interp_out = out_row[~is_max]
        self.b0 = b0[~is_max] - self.bmin
        self.frac = frac[~is_max][:, None]

    def reduce(self, spec: np.ndarray, out: np.ndarray):
        """``spec``: ``(bmax-bmin, U)`` dB (bins x frames). Fills ``out[:, :U]`` (image rows x frames)."""
        U = spec.shape[1]
        M, base = spec, 0  # M row i holds max over bins [base+i, base+i+2**L)
        for L, (rows, glo, ghi, need_from) in enumerate(self.levels):
            if L > 0:
                s = 1 << (L - 1)
                M = M[need_from - base :]
                base = need_from
                M = np.maximum(M[:-s], M[s:])
            if len(rows):
                out[rows, :U] = np.maximum(M[glo - base], M[ghi - base])
        if len(self.interp_out):
            b = self.b0
            out[self.interp_out, :U] = spec[b] * (1.0 - self.frac) + spec[b + 1] * self.frac


class SpectrogramAnalyzer:
    """Tiled, cached, asynchronous display analysis. No Qt.

    ``on_tile_ready`` is invoked from a **worker thread** after each tile completes
    (or fails); marshal it to the GUI thread (Qt signal) before touching widgets.
    """

    def __init__(
        self,
        max_cache_bytes: int = 512 * 2**20,
        executor: cf.Executor | None = None,
        on_tile_ready: Callable[[], None] | None = None,
    ):
        self.max_cache_bytes = int(max_cache_bytes)
        self._own_executor = executor is None
        self._executor = executor or cf.ThreadPoolExecutor(
            max_workers=_default_workers(), thread_name_prefix="spledio-spectrogram"
        )
        self.on_tile_ready = on_tile_ready
        self._lock = threading.Lock()
        self._idle = threading.Condition(self._lock)
        self._cache: OrderedDict[tuple, np.ndarray] = OrderedDict()
        self._cache_bytes = 0
        # previous data of tiles dropped by invalidate(), shown while they are recomputed (no blinking)
        self._stale: OrderedDict[tuple, np.ndarray] = OrderedDict()
        self._stale_bytes = 0
        self._failed: dict[tuple, int] = {}
        self._inflight: dict[tuple, _Task] = {}
        self._stale_running: set[_Task] = set()  # dropped jobs that may still be executing
        self._settings = DisplayAnalysisSettings()
        self._audio: _Audio | None = None
        self._version = 0
        self._generation = 0
        self._closed = False
        self._row_plans: OrderedDict[tuple, _RowPlan] = OrderedDict()
        self.last_error: BaseException | None = None
        self._stats = dict(tiles_computed=0, tiles_failed=0, tiles_discarded=0, tiles_evicted=0, tiles_cancelled=0)

    # ------------------------------------------------------------------ setup
    @property
    def settings(self) -> DisplayAnalysisSettings:
        return self._settings

    @property
    def version(self) -> int:
        return self._version

    def set_audio(self, audio_provider: AudioProvider, n_frames: int, channels: int, sample_rate: float, version: int):
        """Bind the audio to display.

        ``audio_provider(start, end)`` must return a ``(channels, end-start)`` float
        array for samples ``[start, end)`` with zeros outside ``[0, n_frames)``. It
        is called from worker threads and must not be mutated by the caller while
        a version is current (call :meth:`invalidate` after changing audio). The
        analyser never writes to the returned array.

        If ``version``, length, channel count and rate are unchanged the cache is
        kept (re-binding the same audio); otherwise all cached tiles are dropped.
        """
        if not callable(audio_provider):
            raise TypeError("audio_provider must be callable")
        n_frames, channels, sample_rate = int(n_frames), int(channels), float(sample_rate)
        if n_frames < 0 or channels < 1 or not sample_rate > 0:
            raise ValueError("invalid audio description")
        with self._lock:
            old = self._audio
            same = (
                old is not None
                and (old.n_frames, old.channels, old.sample_rate) == (n_frames, channels, sample_rate)
                and int(version) == self._version
            )
            if not same:
                self._generation += 1
                self._drop_inflight_locked(lambda t: True)
                self._cache.clear()
                self._cache_bytes = 0
                self._stale.clear()
                self._stale_bytes = 0
            self._failed.clear()
            self._audio = _Audio(audio_provider, n_frames, channels, sample_rate, self._generation)
            self._version = int(version)
            self._idle.notify_all()

    def set_settings(self, settings: DisplayAnalysisSettings):
        """Switch display analysis settings (a separate cache namespace; old tiles stay
        cached until evicted, so switching back is instant). Queued tiles of other
        settings that have not started are cancelled."""
        if not isinstance(settings, DisplayAnalysisSettings):
            raise TypeError("settings must be DisplayAnalysisSettings")
        with self._lock:
            if self._audio is not None:
                settings.channel_indices(self._audio.channels)  # validate channel_mode
            self._settings = settings
            self._cancel_locked(lambda t: t.settings != settings)

    def invalidate(self, start_sample: int, end_sample: int, new_version: int):
        """Audio samples ``[start_sample, end_sample)`` changed (same length).

        Only tiles with an analysed frame whose support overlaps the changed
        samples (frame centre within ``n_fft/2`` of the region) are dropped and
        recomputed on the next request; all other tiles keep their data and are
        re-keyed to ``new_version``. Failed tiles become retryable. If the audio
        length, channel count or provider changed, call :meth:`set_audio` instead.
        """
        start, end = int(start_sample), int(end_sample)
        with self._lock:
            new_version = int(new_version)
            rekeyed: OrderedDict[tuple, np.ndarray] = OrderedDict()
            for (st, stride, tile, _ver), arr in self._cache.items():
                if _tile_affected(st.config, stride, tile, start, end):
                    self._cache_bytes -= arr.nbytes
                    self._keep_stale_locked((st, stride, tile), arr)
                else:
                    rekeyed[(st, stride, tile, new_version)] = arr
            self._cache = rekeyed
            self._drop_inflight_locked(lambda t: _tile_affected(t.settings.config, t.stride, t.tile, start, end))
            for task in self._inflight.values():
                task.version = new_version
            self._failed.clear()
            self._version = new_version
            self._idle.notify_all()

    def _keep_stale_locked(self, tkey: tuple, arr: np.ndarray) -> None:
        old = self._stale.pop(tkey, None)
        if old is not None:
            self._stale_bytes -= old.nbytes
        self._stale[tkey] = arr
        self._stale_bytes += arr.nbytes
        limit = self.max_cache_bytes // 4
        while self._stale_bytes > limit and self._stale:
            _k, ev = self._stale.popitem(last=False)
            self._stale_bytes -= ev.nbytes

    def retry_failed(self):
        """Forget FAILED markers so the next request retries those tiles."""
        with self._lock:
            self._failed.clear()

    # ------------------------------------------------------------------ LOD
    def choose_stride(self, frames_per_pixel: float) -> int:
        """Largest power of two ``<= frames_per_pixel`` (at least 1), so every column
        still gets its own analysed frame while no more than ~2x the visible
        number of frames is analysed."""
        try:
            fpp = float(frames_per_pixel)
        except (TypeError, ValueError):
            return 1
        if not fpp >= 2.0 or not math.isfinite(fpp):
            return 1
        return int(min(_MAX_STRIDE, 1 << int(math.floor(math.log2(fpp)))))

    # ------------------------------------------------------------------ views
    def view_data(
        self, t0, t1, f_lo, f_hi, width, height, scale: FrequencyScale, *, prefetch_tiles: int = 0,
        stale_ok: bool = False,
    ) -> ViewData:
        """Non-blocking view analysis. Missing tiles are requested and reported PENDING.

        With ``stale_ok`` a tile whose audio just changed (see :meth:`invalidate`) is shown
        with its previous analysis, status ``STALE``, until the new one is ready – so an
        edit updates the picture in place instead of blinking to the pending hatch.

        ``f_lo``/``f_hi`` (``None`` = the scale's range) choose the part of
        ``scale`` shown; pass the same range as the scale for the usual case.
        ``prefetch_tiles`` extra tiles are requested on each side for smooth panning.
        """
        width, height = self._check_view(t0, t1, width, height, scale)
        with self._lock:
            plan = self._plan_locked(float(t0), float(t1), width, prefetch_tiles)
            results, to_submit, _futs = self._lookup_locked(plan, request=True, local=None)
            stale = {}
            if stale_ok:
                for tile in plan.tiles.tolist():
                    if results.get(int(tile)) is None:
                        arr = self._stale.get((plan.settings, plan.stride, int(tile)))
                        if arr is not None:
                            stale[int(tile)] = arr
        # build the (partly PENDING) view first so freshly started workers do not
        # compete with this call for the GIL; the jobs are already registered in-flight
        view = self._assemble(plan, results, scale, f_lo, f_hi, height, stale)
        self._submit(to_submit)
        return view

    def compute_view_sync(
        self, t0, t1, f_lo, f_hi, width, height, scale: FrequencyScale, *, timeout: float | None = None
    ) -> ViewData:
        """Like :meth:`view_data` but blocks until every needed tile is READY or FAILED
        (for tests and image export). Raises ``TimeoutError`` after ``timeout`` s."""
        width, height = self._check_view(t0, t1, width, height, scale)
        deadline = None if timeout is None else time.monotonic() + float(timeout)
        local: dict[tuple, object] = {}
        while True:
            with self._lock:
                plan = self._plan_locked(float(t0), float(t1), width, 0)
                results, to_submit, futs = self._lookup_locked(plan, request=True, local=local)
            futs += self._submit(to_submit)
            missing = any(results.get(int(t)) is None for t in plan.tiles)
            if not missing:
                break
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("spectrogram analysis did not finish in time")
            if not futs:
                # another thread is just submitting the job; give it a moment
                with self._idle:
                    self._idle.wait(0.005)
                continue
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            done, not_done = cf.wait(futs, timeout=remaining)
            for f in done:
                if f.cancelled():
                    continue
                out: _Outcome = f.result()
                if out.key is not None:
                    local[out.key] = _FAILED if out.failed else out.db
            if not_done and deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("spectrogram analysis did not finish in time")
        return self._assemble(plan, results, scale, f_lo, f_hi, height)

    # ------------------------------------------------------------- lifecycle
    def cache_bytes(self) -> int:
        with self._lock:
            return self._cache_bytes

    def stats(self) -> dict:
        with self._lock:
            return dict(
                self._stats,
                cached_tiles=len(self._cache),
                cache_bytes=self._cache_bytes,
                inflight=len(self._inflight),
                stale_running=len(self._stale_running),
            )

    def cancel_pending(self):
        """Cancel queued (not yet running) tile jobs. Running jobs finish and are cached."""
        with self._lock:
            self._cancel_locked(lambda t: True)

    def wait_idle(self, timeout: float | None = None) -> bool:
        """Block until no tile job is queued or running. Returns False on timeout."""
        with self._idle:
            return self._idle.wait_for(lambda: not self._inflight and not self._stale_running, timeout)

    def shutdown(self, wait: bool = True):
        """Cancel queued jobs; shut down the executor if this analyser created it."""
        with self._lock:
            self._closed = True
            self._cancel_locked(lambda t: True)
        if self._own_executor:
            self._executor.shutdown(wait=wait, cancel_futures=True)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.shutdown()

    # -------------------------------------------------------------- internals
    @staticmethod
    def _check_view(t0, t1, width, height, scale):
        width, height = int(width), int(height)
        if width < 1 or height < 1:
            raise ValueError("view width and height must be >= 1")
        if not float(t1) > float(t0):
            raise ValueError("t1 must be greater than t0")
        if not isinstance(scale, FrequencyScale):
            raise TypeError("scale must be a FrequencyScale")
        return width, height

    def _plan_locked(self, t0: float, t1: float, width: int, prefetch: int) -> _Plan:
        settings, audio, version = self._settings, self._audio, self._version
        cfg = settings.config
        if audio is None:
            z = np.zeros(width, dtype=np.int64)
            return _Plan(settings, None, version, 1, np.zeros(width, bool), z, z - 1, np.zeros(0, np.int64), t0, t1)
        sr, hop = audio.sample_rate, cfg.hop
        stride = self.choose_stride((t1 - t0) * sr / hop / width)
        tc = column_centre_times(t0, t1, width)
        inside = (tc >= 0.0) & (tc < audio.n_frames / sr)
        j_max = _j_max(audio.n_frames, cfg, stride)
        j = np.clip(nearest_index(tc * (sr / (hop * stride))), 0, max(j_max, 0))
        frame_index = np.where(inside, j * stride, -1).astype(np.int64)
        tiles = np.unique(j[inside] // TILE_FRAMES)
        extra = []
        if prefetch > 0 and len(tiles):
            last_tile = j_max // TILE_FRAMES
            for d in range(1, int(prefetch) + 1):
                for t in (int(tiles[0]) - d, int(tiles[-1]) + d):
                    if 0 <= t <= last_tile:
                        extra.append(t)
        return _Plan(settings, audio, version, stride, inside, j, frame_index, tiles, t0, t1, extra)

    def _lookup_locked(self, plan: _Plan, request: bool, local: dict | None):
        """Classify needed tiles. Returns (results{tile: array|_FAILED|None}, tasks to submit, futures)."""
        results: dict[int, object] = {}
        to_submit: list[_Task] = []
        futs: list[cf.Future] = []
        if plan.audio is None:
            return results, to_submit, futs
        if self._closed:
            raise RuntimeError("SpectrogramAnalyzer has been shut down")
        st, stride, ver = plan.settings, plan.stride, plan.version
        all_tiles = [int(t) for t in plan.tiles] + list(plan.extra_tiles)
        n_needed = len(plan.tiles)
        for i, tile in enumerate(all_tiles):
            key = (st, stride, tile, ver)
            tkey = (st, stride, tile)
            if local is not None and key in local:
                results[tile] = local[key]
                continue
            arr = self._cache.get(key)
            if arr is not None:
                self._cache.move_to_end(key)
                results[tile] = arr
                continue
            if self._failed.get(tkey) == ver:
                results[tile] = _FAILED
                continue
            results[tile] = None
            task = self._inflight.get(tkey)
            if task is None and request:
                task = _Task(tkey, st, stride, tile, ver, plan.audio)
                self._inflight[tkey] = task
                to_submit.append(task)
            elif task is not None and task.future is not None and i < n_needed:
                futs.append(task.future)
        return results, to_submit, futs

    def _submit(self, tasks: list[_Task]) -> list[cf.Future]:
        futs = []
        for task in tasks:
            try:
                fut = self._executor.submit(self._run, task)
            except RuntimeError as exc:  # executor shut down
                with self._lock:
                    if self._inflight.get(task.tkey) is task:
                        del self._inflight[task.tkey]
                    self._idle.notify_all()
                raise RuntimeError("SpectrogramAnalyzer executor is not accepting work") from exc
            with self._lock:
                task.future = fut
            futs.append(fut)
        return futs

    def _drop_inflight_locked(self, pred):
        """Mark matching jobs stale: queued ones are cancelled, running ones are
        discarded when they finish (their result is never cached)."""
        for tkey, task in list(self._inflight.items()):
            if pred(task):
                task.stale = True
                if task.future is not None and task.future.cancel():
                    self._stats["tiles_cancelled"] += 1
                else:
                    self._stale_running.add(task)
                del self._inflight[tkey]

    def _cancel_locked(self, pred):
        for tkey, task in list(self._inflight.items()):
            if pred(task) and task.future is not None and task.future.cancel():
                del self._inflight[tkey]
                self._stats["tiles_cancelled"] += 1
        self._idle.notify_all()

    def _insert_locked(self, key: tuple, arr: np.ndarray):
        old = self._cache.pop(key, None)
        if old is not None:
            self._cache_bytes -= old.nbytes
        self._cache[key] = arr
        self._cache_bytes += arr.nbytes
        while self._cache_bytes > self.max_cache_bytes and self._cache:
            _k, ev = self._cache.popitem(last=False)
            self._cache_bytes -= ev.nbytes
            self._stats["tiles_evicted"] += 1

    def _run(self, task: _Task) -> _Outcome:
        with self._lock:
            if task.stale or self._closed:
                if self._inflight.get(task.tkey) is task:
                    del self._inflight[task.tkey]
                self._stale_running.discard(task)
                self._idle.notify_all()
                return _Outcome(None, None, False)
        db, err = None, None
        try:
            db = compute_tile_db(task.audio, task.settings, task.stride, task.tile)
        except Exception as exc:  # any provider / analysis failure -> FAILED, never fake data
            err = exc
        with self._lock:
            if self._inflight.get(task.tkey) is task:
                del self._inflight[task.tkey]
            self._stale_running.discard(task)
            if task.stale or task.audio.generation != self._generation:
                self._stats["tiles_discarded"] += 1
                outcome = _Outcome(None, None, False)
            elif err is not None:
                self._failed[task.tkey] = task.version
                self.last_error = err
                self._stats["tiles_failed"] += 1
                outcome = _Outcome((*task.tkey, task.version), None, True)
            else:
                key = (*task.tkey, task.version)
                self._insert_locked(key, db)
                old = self._stale.pop(task.tkey, None)
                if old is not None:
                    self._stale_bytes -= old.nbytes
                self._stats["tiles_computed"] += 1
                outcome = _Outcome(key, db, False)
            self._idle.notify_all()
        if err is not None and outcome.key is not None:
            log.warning("spectrogram tile %s failed: %r", task.tile, err)
        cb = self.on_tile_ready
        if cb is not None and outcome.key is not None:
            try:
                cb()
            except Exception:  # never let a UI callback kill the worker
                log.exception("on_tile_ready callback raised")
        return outcome

    def _row_plan(self, scale, f_lo, f_hi, height, df, n_bins) -> _RowPlan:
        key = (scale, f_lo, f_hi, height, df, n_bins)
        with self._lock:
            rp = self._row_plans.get(key)
            if rp is not None:
                self._row_plans.move_to_end(key)
                return rp
        rp = _RowPlan(scale, f_lo, f_hi, height, df, n_bins)
        with self._lock:
            self._row_plans[key] = rp
            while len(self._row_plans) > 16:
                self._row_plans.popitem(last=False)
        return rp

    def _assemble(self, plan: _Plan, results: dict, scale, f_lo, f_hi, height: int,
                  stale: dict | None = None) -> ViewData:
        width = len(plan.inside)
        cfg = plan.settings.config
        col_status = np.full(width, TileStatus.OUTSIDE, dtype=np.uint8)
        pending = failed = n_stale = 0
        stale = stale or {}
        sr = plan.audio.sample_rate if plan.audio is not None else 0.0
        lo_hz = scale.f_lo if f_lo is None else float(f_lo)
        hi_hz = scale.f_hi if f_hi is None else float(f_hi)
        db = None
        if plan.audio is not None and plan.inside.any():
            col_tile = plan.j // TILE_FRAMES
            ready_cols = np.zeros(width, dtype=bool)
            for tile in plan.tiles.tolist():
                r = results.get(tile)
                cols = plan.inside & (col_tile == tile)
                if r is None and tile in stale:
                    col_status[cols] = TileStatus.STALE
                    n_stale += 1
                    ready_cols |= cols
                elif r is None:
                    col_status[cols] = TileStatus.PENDING
                    pending += 1
                elif r is _FAILED:
                    col_status[cols] = TileStatus.FAILED
                    failed += 1
                else:
                    col_status[cols] = TileStatus.READY
                    ready_cols |= cols
            if ready_cols.any():
                rp = self._row_plan(scale, f_lo, f_hi, height, cfg.bin_spacing(sr), cfg.n_bins)
                uj, inv = np.unique(plan.j[ready_cols], return_inverse=True)
                U = len(uj)
                spec = np.empty((rp.bmax - rp.bmin, U), dtype=np.float32)  # bins x frames
                ut = uj // TILE_FRAMES
                bounds = np.flatnonzero(np.diff(ut)) + 1
                for a, b in zip(np.r_[0, bounds].tolist(), np.r_[bounds, U].tolist()):
                    tile = int(ut[a])
                    data = results.get(tile)
                    if data is None or data is _FAILED:
                        data = stale[tile]
                    spec[:, a:b] = data[uj[a:b] - tile * TILE_FRAMES, rp.bmin : rp.bmax].T
                # two spare columns: U = no data (NaN, pending/failed), U+1 = outside (DB_FLOOR)
                rows = np.empty((height, U + 2), dtype=np.float32)
                rp.reduce(spec, rows)
                rows[:, U] = np.nan
                rows[:, U + 1] = DB_FLOOR
                src = np.where(col_status == TileStatus.OUTSIDE, U + 1, U)
                src[ready_cols] = inv
                db = np.ascontiguousarray(rows[:, src])  # fancy gather yields F order
        if db is None:
            db = np.full((height, width), DB_FLOOR, dtype=np.float32)
            db[:, (col_status == TileStatus.PENDING) | (col_status == TileStatus.FAILED)] = np.nan
        status = np.ascontiguousarray(np.broadcast_to(col_status, (height, width)))
        return ViewData(
            db=db,
            status=status,
            frame_index=plan.frame_index,
            stride=plan.stride,
            settings=plan.settings,
            version=plan.version,
            sample_rate=sr,
            t0=plan.t0,
            t1=plan.t1,
            f_lo=lo_hz,
            f_hi=hi_hz,
            pending_tiles=pending,
            failed_tiles=failed,
            stale_tiles=n_stale,
        )
