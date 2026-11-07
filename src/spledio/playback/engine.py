"""Real-time playback / audition engine (no Qt).

Overview
--------
:class:`PlaybackEngine` plays an immutable :class:`~spledio.playback.sources.PlaybackSource`
through a :class:`PlaybackBackend` (PortAudio via :class:`SoundDeviceBackend` in the
app, :class:`FakeBackend` in tests).  It provides a transport (play / pause / stop /
seek / play range / loop), a click-free A/B source swap (:meth:`PlaybackEngine.set_source`
crossfades at the *same* timeline position) and a monitor volume that never touches
the source data, so it can never affect renders or exports.

Threading model
---------------
Two threads touch the engine:

* the **control thread** (normally the GUI thread) calls the public methods;
* the **audio thread** (PortAudio) calls :meth:`PlaybackEngine._audio_callback`.

There are no locks.  Communication relies only on GIL-atomic reads/writes of single
attributes holding immutable objects or ints:

* ``_transport`` – a frozen :class:`_Transport` snapshot (source, crossfade ramps,
  loop, play-range end, pending seek).  Only the control thread writes it, always by
  building a new snapshot and assigning the reference in one statement; the callback
  reads it exactly once per block, so it always sees a consistent combination
  (e.g. "new source + seek to 0" lands in the same block).
* A seek while playing is posted as a fresh :class:`_Seek` object inside the snapshot;
  the callback applies it when ``snapshot.seek is not self._seek_done`` (identity
  comparison, so no request is lost and none is applied twice).
* ``_position`` / ``_cb_*`` state is written by the callback while the stream runs and
  by the control thread only while the stream is stopped (``backend.stop()`` returns
  only after the last callback finished).
* ``_playing`` is the run flag.  The control thread sets it True before starting the
  stream and False before stopping it; the callback sets it False (as its last shared
  write) when playback finishes or fails, then asks the backend to stop by returning
  ``False``.
* ``_target_gain`` is written by the control thread; the callback slews ``_cb_gain``
  toward it (linear ramp, at most full scale per ``volume_ramp_ms``) to avoid zipper
  noise.

Callback rules (enforced by construction)
-----------------------------------------
No FFT, no file I/O, no blocking locks, no logging, no GUI calls and no large
allocations.  The callback only slices, ``np.copyto``/``np.multiply(..., out=)`` into
the PortAudio buffer and preallocated scratch arrays (fade scratch, gain ramp), whose
size is fixed when the stream is opened (outside the callback).  If PortAudio delivers a
block larger than the scratch, it is processed in scratch-sized chunks.  Crossfade ramps
are precomputed by :meth:`PlaybackEngine.set_source` on the control thread.  Only the
error path builds a (small) message string.  Every exception is caught inside the
callback: the block is zeroed, ``state().error`` is set and ``on_error`` is called.

Callbacks to the application
----------------------------
``on_finished()`` and ``on_error(message)`` may be invoked **from the audio thread**
(end of source, stream failure, underflow) or from the calling thread (synchronous
failures in ``play()`` and friends).  The engine never calls GUI code itself; the UI
must marshal to the GUI thread (e.g. emit a Qt signal with a queued connection) and
must not block inside these callbacks.

Position semantics
------------------
``position`` is the next frame the engine will *render*.  It leads what is audible by
the output latency (``state().latency_seconds``); a playhead can subtract that.
"""

from __future__ import annotations

import dataclasses
import math
import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol, runtime_checkable

import numpy as np

from .sources import PlaybackSource

__all__ = [
    "AudioCallback",
    "PlaybackBackend",
    "SoundDeviceBackend",
    "FakeBackend",
    "PlaybackState",
    "PlaybackEngine",
    "DEFAULT_SCRATCH_FRAMES",
    "MAX_VOLUME",
]

#: ``callback(outdata, frames) -> keep_running``.  ``outdata`` is a writable
#: ``(frames, channels)`` float32 array that must be filled completely.
AudioCallback = Callable[[np.ndarray, int], bool]

#: Scratch size (frames) allocated when a stream opens (or ``blocksize`` if larger).
DEFAULT_SCRATCH_FRAMES = 2048
#: Upper clamp for the monitor volume (+12 dB).
MAX_VOLUME = 4.0

_monotonic = time.monotonic


# --------------------------------------------------------------------------- backends


@runtime_checkable
class PlaybackBackend(Protocol):
    """Output-stream abstraction (PortAudio in the app, a fake in tests).

    ``open`` creates a *stopped* stream for float32 ``(frames, channels)`` output that
    calls ``callback(outdata, frames)`` from the audio thread; when the callback returns
    ``False`` the backend stops calling it after that block has been played.
    ``underflow_callback()`` is called (audio thread) for each block flagged with an
    output underflow; ``finished_callback()`` whenever the stream becomes inactive
    (after ``stop()``, after the callback returned ``False``, or when the device
    vanished).  Optional attributes read with ``getattr``: ``device_name`` (str) and
    ``warning`` (str | None, e.g. "sample-rate conversion enabled").
    """

    def open(
        self,
        sample_rate: int,
        channels: int,
        callback: AudioCallback,
        device: Any = None,
        blocksize: int = 0,
        latency: float | str = "low",
        *,
        underflow_callback: Callable[[], None] | None = None,
        finished_callback: Callable[[], None] | None = None,
    ) -> None: ...

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def close(self) -> None: ...

    @property
    def active(self) -> bool: ...

    @property
    def device_sample_rate(self) -> float | None: ...

    @property
    def latency_seconds(self) -> float | None: ...


class SoundDeviceBackend:
    """:class:`PlaybackBackend` on ``sounddevice.OutputStream`` (PortAudio).

    ``sounddevice`` is imported lazily inside :meth:`open` so importing this module
    never needs PortAudio.  The PortAudio callback signature
    ``(outdata, frames, time, status)`` is mapped to ``callback(outdata, frames)``;
    ``status.output_underflow`` is forwarded to ``underflow_callback`` and a ``False``
    return raises ``sounddevice.CallbackStop``.

    Sample rate: on Core Audio the stream is opened at the project rate with
    ``CoreAudioSettings(conversion_quality=...)`` so that, if the device runs at another
    nominal rate, Core Audio converts (the device's rate is never changed – that would
    affect every other app).  On other host APIs a plain open is tried first; if that
    fails, a converting open is attempted (WASAPI ``auto_convert``) and the first error
    is kept in :attr:`warning`.  ``device_sample_rate`` reports the device's nominal rate.
    """

    def __init__(self, conversion_quality: str = "max") -> None:
        self._stream = None
        self._conversion_quality = conversion_quality
        self._device_sample_rate: float | None = None
        self._device_name = ""
        self._warning: str | None = None

    def open(
        self,
        sample_rate: int,
        channels: int,
        callback: AudioCallback,
        device: Any = None,
        blocksize: int = 0,
        latency: float | str = "low",
        *,
        underflow_callback: Callable[[], None] | None = None,
        finished_callback: Callable[[], None] | None = None,
    ) -> None:
        import sounddevice as sd  # lazy: tests and headless imports need no PortAudio

        self.close()
        self._warning = None
        stop_exc = sd.CallbackStop

        def _sd_callback(outdata, frames, time_info, status):  # audio thread
            if status and underflow_callback is not None and status.output_underflow:
                underflow_callback()
            if not callback(outdata, frames):
                raise stop_exc

        info = sd.query_devices(device, "output")
        hostapi = sd.query_hostapis(info["hostapi"])["name"]
        kwargs = dict(
            samplerate=float(sample_rate),
            blocksize=int(blocksize),
            device=info["index"] if device is None else device,
            channels=int(channels),
            dtype="float32",
            latency=latency,
            callback=_sd_callback,
            finished_callback=finished_callback,
            dither_off=True,
        )
        primary = None
        if hostapi == "Core Audio":
            # Let Core Audio convert if the device runs at another rate; never change
            # the device's nominal rate (fail_if_conversion_required stays False).
            primary = sd.CoreAudioSettings(conversion_quality=self._conversion_quality)
        try:
            stream = sd.OutputStream(extra_settings=primary, **kwargs)
        except sd.PortAudioError as first:
            fallback = None
            if "WASAPI" in hostapi:
                fallback = sd.WasapiSettings(auto_convert=True)
            if fallback is None:
                raise
            stream = sd.OutputStream(extra_settings=fallback, **kwargs)
            self._warning = (
                f"Output device could not open at {sample_rate} Hz ({first}); "
                "using host sample-rate conversion."
            )
        self._stream = stream
        self._device_sample_rate = float(info["default_samplerate"])
        self._device_name = str(info["name"])

    def start(self) -> None:
        s = self._stream
        if s is None:
            raise RuntimeError("audio stream is not open")
        if not s.stopped:  # a stream that ended via CallbackStop must be stopped first
            s.stop()
        s.start()

    def stop(self) -> None:
        s = self._stream
        if s is not None and not s.stopped:
            s.stop()

    def close(self) -> None:
        s = self._stream
        self._stream = None
        if s is not None:
            try:
                s.abort(ignore_errors=True)
            finally:
                s.close(ignore_errors=True)

    @property
    def active(self) -> bool:
        s = self._stream
        return bool(s is not None and s.active)

    @property
    def device_sample_rate(self) -> float | None:
        return self._device_sample_rate if self._stream is not None else None

    @property
    def latency_seconds(self) -> float | None:
        s = self._stream
        return float(s.latency) if s is not None else None

    @property
    def device_name(self) -> str:
        return self._device_name if self._stream is not None else ""

    @property
    def warning(self) -> str | None:
        return self._warning


class FakeBackend:
    """Deterministic in-process backend for tests (no audio hardware).

    :meth:`pull` drives the callback synchronously, like one PortAudio block, and returns
    the produced ``(n, channels)`` block.  Unwritten samples would show up as NaN.
    ``open_error`` / ``start_error`` (exception instances) make ``open`` / ``start``
    raise; :meth:`disconnect` simulates the device vanishing mid-stream.
    ``realtime=False`` (default) exempts it from the engine's stall watchdog, since
    tests pull blocks whenever they like.
    """

    def __init__(
        self,
        *,
        device_sample_rate: float | None = None,
        latency_seconds: float = 0.0,
        device_name: str = "Fake output",
        open_error: BaseException | None = None,
        start_error: BaseException | None = None,
        realtime: bool = False,
    ) -> None:
        self.open_error = open_error
        self.start_error = start_error
        self.realtime = realtime
        self.warning: str | None = None
        self.callback: AudioCallback | None = None
        self.sample_rate: int | None = None
        self.channels: int | None = None
        self.device: Any = None
        self.blocksize = 0
        self.latency: float | str | None = None
        self.open_count = 0
        self.start_count = 0
        self._device_sample_rate = device_sample_rate
        self._latency_seconds = float(latency_seconds)
        self._device_name = device_name
        self._open = False
        self._active = False
        self._dead = False
        self._underflow_cb: Callable[[], None] | None = None
        self._finished_cb: Callable[[], None] | None = None

    def open(
        self,
        sample_rate: int,
        channels: int,
        callback: AudioCallback,
        device: Any = None,
        blocksize: int = 0,
        latency: float | str = "low",
        *,
        underflow_callback: Callable[[], None] | None = None,
        finished_callback: Callable[[], None] | None = None,
    ) -> None:
        if self.open_error is not None:
            raise self.open_error
        self.close()
        self.sample_rate = int(sample_rate)
        self.channels = int(channels)
        self.callback = callback
        self.device = device
        self.blocksize = int(blocksize)
        self.latency = latency
        self._underflow_cb = underflow_callback
        self._finished_cb = finished_callback
        self._open = True
        self._dead = False
        self.open_count += 1

    def start(self) -> None:
        if not self._open:
            raise RuntimeError("FakeBackend: start() before open()")
        if self._dead:
            raise RuntimeError("FakeBackend: device unavailable")
        if self.start_error is not None:
            raise self.start_error
        self._active = True
        self.start_count += 1

    def _deactivate(self) -> None:
        if self._active:
            self._active = False
            if self._finished_cb is not None:
                self._finished_cb()

    def stop(self) -> None:
        self._deactivate()

    def close(self) -> None:
        self._deactivate()
        self._open = False
        self.callback = None

    def disconnect(self) -> None:
        """Simulate the output device disappearing while the stream runs."""
        self._dead = True
        self._deactivate()

    def pull(self, n: int, *, underflow: bool = False, out: np.ndarray | None = None) -> np.ndarray:
        """Run one callback of ``n`` frames; return the block (silence if inactive)."""
        ch = self.channels or 1
        if out is None:
            out = np.full((int(n), ch), np.nan, dtype=np.float32)
        if not self._active or self.callback is None:
            out.fill(0.0)
            return out
        if underflow and self._underflow_cb is not None:
            self._underflow_cb()
        if not self.callback(out, int(n)):
            self._deactivate()
        return out

    @property
    def is_open(self) -> bool:
        return self._open

    @property
    def active(self) -> bool:
        return self._active

    @property
    def device_sample_rate(self) -> float | None:
        if not self._open:
            return None
        if self._device_sample_rate is not None:
            return float(self._device_sample_rate)
        return float(self.sample_rate) if self.sample_rate else None

    @property
    def latency_seconds(self) -> float | None:
        return self._latency_seconds if self._open else None

    @property
    def device_name(self) -> str:
        return self._device_name if self._open else ""


# --------------------------------------------------------------------------- engine


class _Seek:
    """A seek request; compared by identity so each request is applied exactly once."""

    __slots__ = ("frame",)

    def __init__(self, frame: int) -> None:
        self.frame = frame


@dataclass(frozen=True, eq=False)
class _Transport:
    """Immutable control snapshot published by the control thread, read once per block."""

    source: PlaybackSource | None = None
    ramp_in: np.ndarray | None = None  # (L, 1) float32, weight of the incoming source
    ramp_out: np.ndarray | None = None  # (L, 1) float32, 1 - ramp_in
    loop: tuple[int, int] | None = None
    end: int | None = None  # play-range stop frame (ignored while looping)
    seek: _Seek | None = None


@dataclass
class PlaybackState:
    """Snapshot of the transport for the UI (cheap; poll it from a GUI timer)."""

    playing: bool
    position: int
    source_label: str
    loop: tuple[int, int] | None
    device_name: str
    error: str | None
    n_frames: int = 0
    sample_rate: int | None = None
    device_sample_rate: float | None = None
    latency_seconds: float | None = None
    volume: float = 1.0
    play_end: int | None = None
    warning: str | None = None
    underflows: int = 0


def _make_ramps(crossfade_ms: float, sample_rate: int) -> tuple[np.ndarray | None, np.ndarray | None]:
    """Linear equal-gain (w_in + w_out == 1) crossfade ramps, shape (L, 1) float32."""
    ms = float(crossfade_ms)
    if not math.isfinite(ms) or ms <= 0.0:
        return None, None
    length = int(round(ms * sample_rate / 1000.0))
    if length <= 0:
        return None, None
    w = np.arange(1, length + 1, dtype=np.float64) / (length + 1)
    ramp_in = w.astype(np.float32).reshape(length, 1)
    ramp_out = (1.0 - w).astype(np.float32).reshape(length, 1)
    ramp_in.flags.writeable = False
    ramp_out.flags.writeable = False
    return ramp_in, ramp_out


class PlaybackEngine:
    """Transport + audition engine.  All public methods are for the control thread.

    Parameters
    ----------
    backend:
        Output backend; defaults to :class:`SoundDeviceBackend`.
    blocksize:
        PortAudio frames per buffer (0 = let the host choose, recommended).
    latency:
        PortAudio latency ('low', 'high' or seconds).  Raise it if dropouts occur on a
        heavily loaded machine (the callback runs Python code and needs the GIL).
    max_consecutive_underflows:
        Isolated output underflows are counted in ``state().underflows`` but tolerated;
        more than this many in consecutive callbacks is treated as a stream failure
        (error + stop).  ``0`` makes any underflow fatal; ``None`` never stops.
    stall_timeout:
        If playing and no callback arrived for this many seconds (at least 4x the
        output latency), :meth:`state` declares the device dead (disconnect detection
        for hosts that just stop calling back).  ``None`` disables the watchdog; it is
        also skipped for backends with ``realtime = False`` (e.g. :class:`FakeBackend`).
    volume_ramp_ms:
        Time for a full-scale (0 -> 1) monitor-volume change.
    """

    def __init__(
        self,
        backend: PlaybackBackend | None = None,
        *,
        blocksize: int = 0,
        latency: float | str = "low",
        max_consecutive_underflows: int | None = 8,
        stall_timeout: float | None = 2.0,
        volume_ramp_ms: float = 20.0,
    ) -> None:
        self._backend: PlaybackBackend = backend if backend is not None else SoundDeviceBackend()
        self._blocksize = max(0, int(blocksize))
        self._latency = latency
        self._max_uf = max_consecutive_underflows
        self._stall_timeout = stall_timeout
        self._volume_ramp_s = max(0.0, float(volume_ramp_ms)) / 1000.0

        #: Called with no arguments when playback reaches its end (audio thread).
        self.on_finished: Callable[[], None] | None = None
        #: Called with a message on any playback failure (audio or calling thread).
        self.on_error: Callable[[str], None] | None = None

        # Control-thread state.
        self._transport = _Transport()
        self._device: Any = None
        self._open_key: tuple | None = None
        self._target_gain = 1.0
        self._gain_step = 1.0
        self._error: str | None = None
        self._warning: str | None = None
        self._last_start_error = ""

        # Shared flags (GIL-atomic attribute writes).
        self._playing = False
        self._position = 0
        self._finished = False

        # Audio-thread state (control thread writes it only while the stream is stopped).
        self._seek_done: _Seek | None = None
        self._cb_src: PlaybackSource | None = None
        self._cb_fade_src: PlaybackSource | None = None
        self._cb_fade_pos = 0
        self._cb_ramp_in: np.ndarray | None = None
        self._cb_ramp_out: np.ndarray | None = None
        self._cb_gain = 1.0
        self._cb_count = 0
        self._uf_last = -2
        self._uf_run = 0
        self._underflows = 0
        self._last_cb_time = 0.0
        self._bufs: tuple[np.ndarray, np.ndarray, np.ndarray] = self._alloc_scratch(
            1, DEFAULT_SCRATCH_FRAMES
        )

    # ------------------------------------------------------------------ properties

    @property
    def backend(self) -> PlaybackBackend:
        return self._backend

    @property
    def source(self) -> PlaybackSource | None:
        return self._transport.source

    @property
    def position(self) -> int:
        """Next frame to render (a seek not yet consumed by the callback is reported)."""
        s = self._transport.seek
        if s is not None and s is not self._seek_done:
            return s.frame
        return self._position

    @property
    def is_playing(self) -> bool:
        return self._playing

    @property
    def loop(self) -> tuple[int, int] | None:
        return self._transport.loop

    @property
    def loop_enabled(self) -> bool:
        return self._transport.loop is not None

    @property
    def volume(self) -> float:
        return self._target_gain

    @property
    def output_device(self) -> Any:
        return self._device

    # ------------------------------------------------------------------ sources

    def set_source(
        self,
        source: PlaybackSource | None,
        keep_position: bool = True,
        crossfade_ms: float = 8.0,
    ) -> None:
        """Swap the playing source atomically.

        While playing, a source with the same sample rate and channel count is
        crossfaded (linear, equal gain, ``crossfade_ms``) at the *same* timeline
        position, so A/B toggles never jump.  A format change stops, reopens the stream
        and resumes at the same position.  ``keep_position=False`` restarts at frame 0
        (hard switch, no crossfade).  ``None`` stops playback and clears the source.
        """
        if source is not None and not isinstance(source, PlaybackSource):
            raise TypeError("source must be a PlaybackSource or None")
        t = self._transport
        old = t.source
        if source is old:
            if not keep_position:
                self.seek(0)
            return
        if source is None:
            self.stop()
            self._transport = dataclasses.replace(
                self._transport, source=None, ramp_in=None, ramp_out=None
            )
            self._position = 0
            return
        same_format = (
            old is not None
            and old.sample_rate == source.sample_rate
            and old.channels == source.channels
        )
        if self._playing and same_format:
            if keep_position:
                ramp_in, ramp_out = _make_ramps(crossfade_ms, source.sample_rate)
                self._transport = dataclasses.replace(
                    t, source=source, ramp_in=ramp_in, ramp_out=ramp_out
                )
            else:
                self._transport = dataclasses.replace(
                    t, source=source, ramp_in=None, ramp_out=None, seek=_Seek(0)
                )
            return
        was_playing = self._playing
        if was_playing:
            self._playing = False
            self._halt_stream()
        self._absorb_pending_seek()
        pos = self._position if keep_position else 0
        self._transport = dataclasses.replace(
            self._transport, source=source, ramp_in=None, ramp_out=None
        )
        self._position = max(0, min(pos, source.n_frames))
        if was_playing:
            self.play()  # reopens the stream for the new format, resumes at position

    # ------------------------------------------------------------------ transport

    def play(self, start: int | None = None, end: int | None = None) -> bool:
        """Start (or retarget) playback.  Returns True if audio is now playing.

        ``start`` – frame to start at (default: current position).  ``end`` – stop frame
        for a play range (ignored while a loop is enabled).  Resuming after
        :meth:`pause` with no arguments continues the previous play range.  With no
        ``start``: at the end of the source playback restarts from 0, and with a loop
        enabled a position outside the loop jumps to the loop start.  Never raises for
        device problems: failures set ``state().error``, call ``on_error`` and return
        False.
        """
        t = self._transport
        src = t.source
        if src is None:
            return False
        if self._playing:
            changes: dict[str, Any] = {}
            if start is not None:
                changes["seek"] = _Seek(self._clamp(start, src))
                changes["end"] = None
            if end is not None:
                changes["end"] = int(end)
            if changes:
                self._transport = dataclasses.replace(t, **changes)
            return True

        self._absorb_pending_seek()
        resume = start is None
        pos = self._clamp(start, src) if start is not None else self._position
        if end is not None:
            play_end = int(end)
        elif resume and not self._finished:
            play_end = t.end
        else:
            play_end = None
        n = src.n_frames
        loop = self._effective_loop(t.loop, n)
        if resume:
            if loop is not None:
                if not (loop[0] <= pos < loop[1]):
                    pos = loop[0]
            elif play_end is None and pos >= n:
                pos = 0
        if loop is None:
            stop_frame = n if play_end is None else min(play_end, n)
            if pos >= stop_frame:
                return False

        if not self._ensure_stream(src):
            return False
        try:
            # A stream that ended by itself (end of source / error) must be fully stopped,
            # and its finished callback must have fired, before the run flag goes up.
            self._backend.stop()
        except Exception:
            self._close_stream()  # broken stream: reopen
            if not self._ensure_stream(src):
                return False

        self._transport = dataclasses.replace(self._transport, end=play_end)
        self._seek_done = self._transport.seek
        self._position = pos
        self._finished = False
        self._error = None
        if self._start_stream(src):
            return True
        # The stream may be stale (device re-plugged, stream invalidated): reopen once.
        self._close_stream()
        if not self._ensure_stream(src):
            return False
        if self._start_stream(src):
            return True
        self._close_stream()
        self._fail(f"Could not start audio output: {self._last_start_error}")
        return False

    def _start_stream(self, src: PlaybackSource) -> bool:
        self._reset_callback_state(src)
        self._playing = True
        try:
            self._backend.start()
            return True
        except Exception as exc:
            self._playing = False
            self._last_start_error = f"{type(exc).__name__}: {exc}"
            return False

    def pause(self) -> None:
        """Stop the stream, keep the position (and play range) for :meth:`play`."""
        if self._playing:
            self._playing = False
            self._halt_stream()
        self._absorb_pending_seek()

    def stop(self, return_to: int | None = None) -> None:
        """Stop playback.  Position stays unless ``return_to`` is given.  Clears the play range."""
        if self._playing:
            self._playing = False
            self._halt_stream()
        self._absorb_pending_seek()
        self._finished = False
        self._transport = dataclasses.replace(self._transport, end=None)
        if return_to is not None:
            self._position = self._clamp(return_to, self._transport.source)

    def seek(self, frame: int) -> None:
        """Jump to ``frame`` (clamped to ``[0, n_frames]``); works while playing (no crossfade)."""
        t = self._transport
        f = self._clamp(frame, t.source)
        if self._playing:
            self._transport = dataclasses.replace(t, seek=_Seek(f))
        else:
            self._absorb_pending_seek()
            self._position = f

    def set_loop(self, start: int, end: int) -> None:
        """Loop ``[start, end)``.  The wrap happens inside the callback, sample-accurately.

        While playing, a playhead before ``end`` runs into the loop and wraps; a playhead
        already at/after ``end`` plays on.  The loop is clipped to the source length.
        """
        s, e = int(start), int(end)
        if s < 0 or e <= s:
            raise ValueError(f"invalid loop [{start}, {end})")
        self._transport = dataclasses.replace(self._transport, loop=(s, e))

    def clear_loop(self) -> None:
        self._transport = dataclasses.replace(self._transport, loop=None)

    def set_volume(self, gain_linear: float) -> None:
        """Monitor volume (linear, clamped to [0, MAX_VOLUME]); ramped in the callback.

        Applied only to the output buffer – never to sources, renders or exports.
        """
        g = float(gain_linear)
        if not math.isfinite(g):
            raise ValueError("volume must be finite")
        g = min(max(g, 0.0), MAX_VOLUME)
        self._target_gain = g
        if not self._playing:
            self._cb_gain = g

    def clear_error(self) -> None:
        self._error = None

    # ------------------------------------------------------------------ devices

    @staticmethod
    def list_output_devices() -> list[dict]:
        """Output devices as dicts ``id, name, max_output_channels, default_samplerate,
        is_default, hostapi``.  Returns ``[]`` if PortAudio is unavailable.

        Note: PortAudio enumerates devices once per process, so hot-plugged devices
        appear only after a restart of the app.
        """
        try:
            import sounddevice as sd

            devices = sd.query_devices()
        except Exception:
            return []
        try:
            default_out = int(sd.query_devices(kind="output")["index"])
        except Exception:
            default_out = -1
        try:
            hostapis = [h["name"] for h in sd.query_hostapis()]
        except Exception:
            hostapis = []
        result = []
        for d in devices:
            if int(d.get("max_output_channels", 0)) <= 0:
                continue
            idx = int(d["index"])
            ha = int(d.get("hostapi", -1))
            result.append(
                {
                    "id": idx,
                    "name": str(d["name"]),
                    "max_output_channels": int(d["max_output_channels"]),
                    "default_samplerate": float(d["default_samplerate"]),
                    "is_default": idx == default_out,
                    "hostapi": hostapis[ha] if 0 <= ha < len(hostapis) else "",
                }
            )
        return result

    def set_output_device(self, device_id: Any = None) -> None:
        """Select the output device (``None`` = system default).  Resumes if playing."""
        if device_id == self._device and self._open_key is not None:
            return
        was_playing = self._playing
        if was_playing:
            self._playing = False
            self._halt_stream()
            self._absorb_pending_seek()
        self._close_stream()
        self._device = device_id
        if was_playing:
            self.play()

    # ------------------------------------------------------------------ state

    def state(self) -> PlaybackState:
        """Transport snapshot.  Also runs the stall watchdog (disconnect detection)."""
        self._check_stall()
        t = self._transport
        src = t.source
        b = self._backend
        is_open = self._open_key is not None
        if is_open:
            device_name = str(getattr(b, "device_name", "") or "")
        else:
            device_name = ""
        if not device_name:
            device_name = "Default output" if self._device is None else str(self._device)
        return PlaybackState(
            playing=self._playing,
            position=self.position,
            source_label=src.label if src is not None else "",
            loop=t.loop,
            device_name=device_name,
            error=self._error,
            n_frames=src.n_frames if src is not None else 0,
            sample_rate=src.sample_rate if src is not None else None,
            device_sample_rate=b.device_sample_rate if is_open else None,
            latency_seconds=b.latency_seconds if is_open else None,
            volume=self._target_gain,
            play_end=t.end,
            warning=self._warning,
            underflows=self._underflows,
        )

    def close(self) -> None:
        """Stop and release the stream.  The engine can be used again (play reopens)."""
        self.stop()
        self._close_stream()

    def __enter__(self) -> PlaybackEngine:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------------ internals (control thread)

    @staticmethod
    def _clamp(frame: int, src: PlaybackSource | None) -> int:
        f = int(frame)
        n = src.n_frames if src is not None else 0
        return 0 if f < 0 else (n if f > n else f)

    @staticmethod
    def _effective_loop(loop: tuple[int, int] | None, n: int) -> tuple[int, int] | None:
        if loop is None:
            return None
        ls, le = loop
        if le > n:
            le = n
        return (ls, le) if le > ls else None

    @staticmethod
    def _alloc_scratch(channels: int, frames: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        fade = np.zeros((frames, channels), dtype=np.float32)
        gain = np.zeros((frames, 1), dtype=np.float32)
        ramp_idx = np.arange(1, frames + 1, dtype=np.float32).reshape(frames, 1)
        return fade, gain, ramp_idx

    def _absorb_pending_seek(self) -> None:
        """Apply a seek the callback never consumed (only while not playing)."""
        s = self._transport.seek
        if s is not None and s is not self._seek_done:
            self._seek_done = s
            self._position = s.frame

    def _reset_callback_state(self, src: PlaybackSource) -> None:
        self._cb_src = src
        self._cb_fade_src = None
        self._cb_fade_pos = 0
        self._cb_ramp_in = None
        self._cb_ramp_out = None
        self._cb_gain = self._target_gain
        self._uf_run = 0
        self._uf_last = -2
        self._last_cb_time = _monotonic()

    def _ensure_stream(self, src: PlaybackSource) -> bool:
        key = (src.sample_rate, src.channels, self._device, self._blocksize, self._latency)
        if self._open_key == key:
            return True
        self._close_stream()
        try:
            self._backend.open(
                src.sample_rate,
                src.channels,
                self._audio_callback,
                device=self._device,
                blocksize=self._blocksize,
                latency=self._latency,
                underflow_callback=self._on_underflow,
                finished_callback=self._on_stream_finished,
            )
        except Exception as exc:
            self._fail(
                f"Could not open audio output ({src.sample_rate} Hz, {src.channels} ch): {exc}"
            )
            return False
        self._open_key = key
        self._bufs = self._alloc_scratch(src.channels, max(DEFAULT_SCRATCH_FRAMES, self._blocksize))
        ramp_frames = self._volume_ramp_s * src.sample_rate
        self._gain_step = 1.0 / ramp_frames if ramp_frames >= 1.0 else 1.0
        self._warning = getattr(self._backend, "warning", None)
        return True

    def _close_stream(self) -> None:
        self._open_key = None
        try:
            self._backend.close()
        except Exception:
            pass

    def _halt_stream(self) -> None:
        """Stop the stream after ``_playing`` was cleared; a failure closes it."""
        try:
            self._backend.stop()
        except Exception as exc:
            self._close_stream()
            self._fail(f"Audio output error while stopping: {exc}")

    def _check_stall(self) -> None:
        timeout = self._stall_timeout
        if timeout is None or not self._playing or not getattr(self._backend, "realtime", True):
            return
        lat = self._backend.latency_seconds if self._open_key is not None else None
        if lat:
            timeout = max(timeout, 4.0 * lat)
        if _monotonic() - self._last_cb_time > timeout:
            self._playing = False
            self._close_stream()  # aborts; returns once no callback is running
            self._absorb_pending_seek()
            self._fail("Audio device stopped responding (disconnected or unavailable).")

    # ------------------------------------------------------------------ internals (any thread)

    def _fail(self, message: str) -> None:
        self._error = message
        self._playing = False
        cb = self.on_error
        if cb is not None:
            try:
                cb(message)
            except Exception:
                pass

    def _on_underflow(self) -> None:  # audio thread, before the flagged block renders
        self._underflows += 1
        n = self._cb_count
        self._uf_run = self._uf_run + 1 if n == self._uf_last + 1 else 1
        self._uf_last = n
        limit = self._max_uf
        if limit is not None and self._uf_run > limit and self._playing:
            self._fail(
                f"Audio output underflow ({self._uf_run} consecutive blocks); playback stopped."
            )

    def _on_stream_finished(self) -> None:  # PortAudio thread
        if self._playing:  # the engine did not ask for this
            self._fail("Audio stream stopped unexpectedly (output device disconnected or unavailable).")

    # ------------------------------------------------------------------ the audio callback

    def _audio_callback(self, outdata: np.ndarray, frames: int) -> bool:
        """PortAudio callback body.  Returns False to stop the stream after this block."""
        try:
            if not self._playing:
                outdata.fill(0.0)
                return False
            return self._render(outdata, frames)
        except BaseException as exc:  # never let anything escape into PortAudio
            try:
                outdata.fill(0.0)
            except BaseException:
                pass
            self._fail(f"Playback callback failed: {type(exc).__name__}: {exc}")
            return False

    def _render(self, outdata: np.ndarray, frames: int) -> bool:
        self._last_cb_time = _monotonic()
        self._cb_count += 1
        t = self._transport  # the one read of the control snapshot for this block
        src = t.source
        if src is None:
            outdata.fill(0.0)
            self._playing = False
            return False

        seek = t.seek
        if seek is not self._seek_done:
            self._seek_done = seek
            pos = seek.frame if seek is not None else self._position
        else:
            pos = self._position
        if src is not self._cb_src:
            self._begin_fade(src, t)

        n_total = src.n_frames
        looping = False
        ls = le = 0
        loop = t.loop
        if loop is not None:
            ls, le = loop
            if le > n_total:
                le = n_total
            looping = le > ls
        stop = n_total
        if not looping:
            end = t.end
            if end is not None and end < stop:
                stop = end

        fade_buf, gain_buf, ramp_idx = self._bufs
        max_block = fade_buf.shape[0]
        written = 0
        finished = False
        while written < frames:
            if looping and pos < le:
                limit = le
                wrap = True
            else:
                limit = stop
                wrap = False
            if pos >= limit:
                finished = True
                break
            m = frames - written
            if m > limit - pos:
                m = limit - pos
            if m > max_block:
                m = max_block
            seg = outdata[written : written + m]
            src.read_into(pos, seg)
            if self._cb_fade_src is not None:
                self._mix_fade(pos, seg, m, fade_buf)
            pos += m
            written += m
            if wrap and pos >= le:
                pos = ls
        if not finished and not (looping and pos < le) and pos >= stop:
            finished = True
        if written < frames:
            outdata[written:frames].fill(0.0)
        if written:
            self._apply_gain(outdata, written, gain_buf, ramp_idx)

        if finished:
            if pos > stop:
                pos = stop
            self._position = pos
            self._finished = True
            self._playing = False  # last shared write: control thread may now take over
            cb = self.on_finished
            if cb is not None:
                try:
                    cb()
                except Exception:
                    pass
            return False
        self._position = pos
        return True

    def _begin_fade(self, src: PlaybackSource, t: _Transport) -> None:
        old = self._cb_src
        self._cb_src = src
        ramp_in = t.ramp_in
        if old is None or ramp_in is None or old.channels != src.channels:
            self._cb_fade_src = None
            return
        length = ramp_in.shape[0]
        if self._cb_fade_src is src and self._cb_ramp_in is not None:
            # Toggled back mid-fade: continue from the current weights (no jump).
            prev_len = self._cb_ramp_in.shape[0]
            w_src = (prev_len - self._cb_fade_pos) / (prev_len + 1)  # src's current weight
            k = int(round(w_src * (length + 1))) - 1
            self._cb_fade_pos = 0 if k < 0 else (length - 1 if k >= length else k)
        else:
            self._cb_fade_pos = 0
        self._cb_fade_src = old
        self._cb_ramp_in = ramp_in
        self._cb_ramp_out = t.ramp_out

    def _mix_fade(self, pos: int, seg: np.ndarray, m: int, fade_buf: np.ndarray) -> None:
        k = self._cb_fade_pos
        rin = self._cb_ramp_in
        rout = self._cb_ramp_out
        length = rin.shape[0]
        f = length - k
        if f > m:
            f = m
        tmp = fade_buf[:f]
        self._cb_fade_src.read_into(pos, tmp)
        head = seg[:f]
        np.multiply(head, rin[k : k + f], out=head)
        np.multiply(tmp, rout[k : k + f], out=tmp)
        np.add(head, tmp, out=head)
        k += f
        if k >= length:
            self._cb_fade_src = None
        self._cb_fade_pos = k

    def _apply_gain(self, outdata: np.ndarray, n: int, gain_buf: np.ndarray, ramp_idx: np.ndarray) -> None:
        cur = self._cb_gain
        tgt = self._target_gain
        if cur == tgt:
            if cur != 1.0:
                blk = outdata[:n]
                np.multiply(blk, cur, out=blk)
            return
        # Slew at a constant rate (``_gain_step`` per frame) until the target is reached,
        # independent of the block size; then hold the target exactly.
        step = self._gain_step
        max_block = gain_buf.shape[0]
        o = 0
        while o < n:
            m = n - o
            if m > max_block:
                m = max_block
            blk = outdata[o : o + m]
            d = tgt - cur
            if d == 0.0:
                if cur != 1.0:
                    np.multiply(blk, cur, out=blk)
                o += m
                continue
            need = math.ceil(abs(d) / step)
            r = need if need < m else m
            nxt = tgt if need <= m else (cur + step * m if d > 0.0 else cur - step * m)
            g = gain_buf[:r]
            np.multiply(ramp_idx[:r], (nxt - cur) / r, out=g)
            np.add(g, cur, out=g)
            head = blk[:r]
            np.multiply(head, g, out=head)
            if r < m:
                tail = blk[r:]
                if nxt != 1.0:
                    np.multiply(tail, nxt, out=tail)
            cur = nxt
            o += m
        self._cb_gain = cur
