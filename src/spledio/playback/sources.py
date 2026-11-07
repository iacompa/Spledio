"""Read-only playback sources for the real-time audio callback.

A :class:`PlaybackSource` is an immutable, pre-rendered view of audio on the
*project timeline* (frame 0 = project start).  The PortAudio callback pulls
blocks from it with :meth:`PlaybackSource.read_into`, so every source obeys
the real-time rules:

* the object and its sample buffers never change after construction
  (buffers are flagged read-only), so the audio thread can read them without
  locks while the GUI thread builds and swaps in new sources;
* ``read_into`` only slices and copies into the caller's ``out`` array
  (``np.copyto`` / ``ndarray.fill``) – no FFT, no I/O, no large allocations.

Samples are stored as C-contiguous ``(frames, channels)`` ``float32`` – the
PortAudio layout – so the transpose from the engine-wide
``(channels, frames)`` convention happens once, at construction time, never
in the callback.  Non-finite input samples (NaN/inf) are replaced by 0 when a
source is built, so a broken render can never blast the monitors.
"""

from __future__ import annotations

import numpy as np

__all__ = [
    "PlaybackSource",
    "ArraySource",
    "OverlaySource",
    "SilenceSource",
]


def _frames_in_range(start: int, n: int, total: int) -> int:
    """Number of frames of ``[start, start+n)`` that lie inside ``[0, total)``."""
    a = start if start > 0 else 0
    b = start + n
    if b > total:
        b = total
    return b - a if b > a else 0


def _to_playback_layout(audio, what: str) -> np.ndarray:
    """(channels, frames) float array -> owned, read-only (frames, channels) float32."""
    arr = np.asarray(audio)
    if not (np.issubdtype(arr.dtype, np.floating)):
        raise TypeError(f"{what} must have a real floating-point dtype, got {arr.dtype}")
    if arr.ndim == 1:  # convenience: a 1-D array is mono
        arr = arr[np.newaxis, :]
    if arr.ndim != 2:
        raise ValueError(f"{what} must be (channels, frames), got shape {arr.shape}")
    if arr.shape[0] < 1:
        raise ValueError(f"{what} must have at least one channel")
    # copy=True guarantees we own the data even if ``arr.T`` is already a
    # C-contiguous float32 view of somebody else's buffer.
    data = np.array(arr.T, dtype=np.float32, order="C", copy=True)
    finite = np.isfinite(data)
    if not finite.all():
        data[~finite] = 0.0
    data.flags.writeable = False
    return data


class PlaybackSource:
    """Base class: an immutable block of audio on the project timeline.

    Attributes (read-only): ``sample_rate`` (Hz, int), ``channels`` (int),
    ``n_frames`` (timeline length in frames; reads at or beyond it are
    silence) and ``label`` (free text shown in the transport, e.g. "Mix" or
    "Preview: De-hum").
    """

    __slots__ = ("_sample_rate", "_channels", "_n_frames", "_label", "_frozen")

    def __init__(self, sample_rate: int, channels: int, n_frames: int, label: str = "") -> None:
        sr = int(sample_rate)
        ch = int(channels)
        nf = int(n_frames)
        if sr <= 0:
            raise ValueError(f"sample_rate must be positive, got {sample_rate!r}")
        if ch <= 0:
            raise ValueError(f"channels must be positive, got {channels!r}")
        if nf < 0:
            raise ValueError(f"n_frames must be >= 0, got {n_frames!r}")
        object.__setattr__(self, "_sample_rate", sr)
        object.__setattr__(self, "_channels", ch)
        object.__setattr__(self, "_n_frames", nf)
        object.__setattr__(self, "_label", str(label))

    # Immutability guard: subclasses finish __init__ with ``self._freeze()``.
    def _freeze(self) -> None:
        object.__setattr__(self, "_frozen", True)

    def __setattr__(self, name, value):
        if getattr(self, "_frozen", False):
            raise AttributeError(f"{type(self).__name__} is immutable")
        object.__setattr__(self, name, value)

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @property
    def channels(self) -> int:
        return self._channels

    @property
    def n_frames(self) -> int:
        return self._n_frames

    @property
    def label(self) -> str:
        return self._label

    @property
    def duration_seconds(self) -> float:
        return self._n_frames / self._sample_rate

    def _check_out(self, out: np.ndarray) -> int:
        if out.ndim != 2 or out.shape[1] != self._channels:
            raise ValueError(
                f"out must be (frames, {self._channels}), got shape {out.shape}"
            )
        return out.shape[0]

    def read_into(self, start: int, out: np.ndarray) -> int:
        """Fill ``out`` (shape ``(n, channels)``, float32) with frames ``[start, start+n)``.

        Frames before 0 or at/after ``n_frames`` are written as zeros.  Returns
        the number of frames of the request that lie inside the source
        timeline ``[0, n_frames)`` (padding outside the timeline is not
        counted).  Real-time safe: no allocation beyond small view objects.
        """
        raise NotImplementedError

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(label={self._label!r}, sample_rate={self._sample_rate}, "
            f"channels={self._channels}, n_frames={self._n_frames})"
        )


class SilenceSource(PlaybackSource):
    """``n_frames`` of digital silence (useful as a placeholder transport source)."""

    __slots__ = ()

    def __init__(self, sample_rate: int, channels: int, n_frames: int, label: str = "") -> None:
        super().__init__(sample_rate, channels, n_frames, label)
        self._freeze()

    def read_into(self, start: int, out: np.ndarray) -> int:
        n = self._check_out(out)
        out.fill(0.0)
        return _frames_in_range(int(start), n, self._n_frames)


class ArraySource(PlaybackSource):
    """A pre-rendered buffer placed on the project timeline.

    ``audio`` is ``(channels, frames)`` of any real float dtype (a 1-D array is
    treated as mono).  A private C-contiguous ``(frames, channels)`` float32
    copy is stored.  ``offset`` is the project frame at which ``audio[:, 0]``
    plays, so a short audition render can sit at its true project position;
    frames outside the buffer read as silence.  ``n_frames`` is
    ``max(0, offset + buffer_length)`` unless ``total_frames`` is given
    (e.g. the full project length, so the transport range is the project).
    """

    __slots__ = ("_data", "_offset", "_buf_len")

    def __init__(
        self,
        audio,
        sample_rate: int,
        label: str = "",
        offset: int = 0,
        total_frames: int | None = None,
    ) -> None:
        data = _to_playback_layout(audio, "audio")
        off = int(offset)
        buf_len = data.shape[0]
        if total_frames is None:
            n_frames = max(0, off + buf_len)
        else:
            n_frames = int(total_frames)
            if n_frames < 0:
                raise ValueError(f"total_frames must be >= 0, got {total_frames!r}")
        super().__init__(sample_rate, data.shape[1], n_frames, label)
        self._data = data
        self._offset = off
        self._buf_len = buf_len
        self._freeze()

    @property
    def offset(self) -> int:
        return self._offset

    @property
    def buffer_frames(self) -> int:
        return self._buf_len

    @property
    def data(self) -> np.ndarray:
        """The stored read-only ``(frames, channels)`` float32 buffer."""
        return self._data

    def read_into(self, start: int, out: np.ndarray) -> int:
        n = self._check_out(out)
        start = int(start)
        total = self._n_frames
        # Part of the request inside the timeline [0, total).
        a = start if start > 0 else 0
        b = start + n
        if b > total:
            b = total
        # ...intersected with the buffer's span [offset, offset + buf_len).
        off = self._offset
        ba = a if a > off else off
        bb = off + self._buf_len
        if bb > b:
            bb = b
        if bb > ba:
            i0 = ba - start
            i1 = bb - start
            if i0 > 0:
                out[:i0].fill(0.0)
            np.copyto(out[i0:i1], self._data[ba - off : bb - off])
            if i1 < n:
                out[i1:].fill(0.0)
        else:
            out.fill(0.0)
        return b - a if b > a else 0


class OverlaySource(PlaybackSource):
    """``base`` with ``[overlay_start, overlay_start + len)`` replaced by ``overlay``.

    Used to A/B a patched region against the unpatched mix without copying
    the whole mix: ``OverlaySource(mix, k, patched)`` and ``mix`` share the
    base buffer and differ only inside the overlay span.  ``overlay`` is
    ``(channels, frames)`` with the same channel count as ``base``.  The
    timeline length is ``max(base.n_frames, overlay_start + len(overlay))``.
    """

    __slots__ = ("_base", "_overlay", "_ov_start", "_ov_len")

    def __init__(self, base: PlaybackSource, overlay_start: int, overlay, label: str = "") -> None:
        if not isinstance(base, PlaybackSource):
            raise TypeError("base must be a PlaybackSource")
        ov = _to_playback_layout(overlay, "overlay")
        if ov.shape[1] != base.channels:
            raise ValueError(
                f"overlay has {ov.shape[1]} channel(s) but base has {base.channels}"
            )
        ov_start = int(overlay_start)
        ov_len = ov.shape[0]
        n_frames = max(base.n_frames, ov_start + ov_len)
        super().__init__(base.sample_rate, base.channels, n_frames, label or base.label)
        self._base = base
        self._overlay = ov
        self._ov_start = ov_start
        self._ov_len = ov_len
        self._freeze()

    @property
    def base(self) -> PlaybackSource:
        return self._base

    @property
    def overlay_start(self) -> int:
        return self._ov_start

    @property
    def overlay_frames(self) -> int:
        return self._ov_len

    def read_into(self, start: int, out: np.ndarray) -> int:
        n = self._check_out(out)
        start = int(start)
        self._base.read_into(start, out)
        total = self._n_frames
        a = start if start > 0 else 0
        b = start + n
        if b > total:
            b = total
        os_ = self._ov_start
        oa = a if a > os_ else os_
        ob = os_ + self._ov_len
        if ob > b:
            ob = b
        if ob > oa:
            np.copyto(out[oa - start : ob - start], self._overlay[oa - os_ : ob - os_])
        return b - a if b > a else 0
