"""EditorController: binds an EditorSession to Qt (signals, worker jobs,
playback/audition, display analysis). Widgets talk to this object only.

Threading: long renders run in a ThreadPoolExecutor via :meth:`run_job`;
results come back to the GUI thread through queued Qt signals. The session
serialises mutations with its own lock. The PortAudio callback only ever sees
pre-rendered immutable PlaybackSource objects.
"""

from __future__ import annotations

import math
import os
import threading
import time
import traceback
import warnings
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal, Slot

from spledio.commands.history import Change
from spledio.dsp.spectral import Cancelled
from spledio.engine.session import EditError, EditorSession, SessionEvent
from spledio.playback.sources import ArraySource, OverlaySource, PlaybackSource
from spledio.selections.geometry import BrushShape, INF_F
from spledio.selections.selection import Selection
from spledio.ui.state import ToolState, ViewState


@dataclass
class AuditionState:
    mode: str = "mix"  # mix | selected | all_freqs | remainder | difference | ab_a | ab_b | preview | bypass
    label: str = "Mix"
    ab_start: int = 0


class _Bridge(QObject):
    """Carries callbacks from worker/audio threads to the GUI thread."""

    call = Signal(object)


class EditorController(QObject):
    sessionReplaced = Signal()
    audioChanged = Signal(object)  # Change
    structureChanged = Signal()
    propsChanged = Signal()
    selectionChanged = Signal()
    historyChanged = Signal()
    previewChanged = Signal()
    dirtyChanged = Signal(bool)
    playheadChanged = Signal(int)
    playbackChanged = Signal()  # transport/audition state changed
    statusMessage = Signal(str, int)  # text, timeout ms (0 = sticky)
    busyChanged = Signal(bool, str)  # a job that blocks other actions started / ended
    busyIndicator = Signal(bool, str)  # status-bar spinner (also for quick jobs that run long)
    jobEnded = Signal()  # after every job, quiet or not
    warning = Signal(str)
    error = Signal(str, str)  # title, text
    tilesReady = Signal()
    areasChanged = Signal()  # adjusted areas added / changed / removed / picked
    requestExportLayers = Signal(list)  # layer ids -> main window opens the export dialog

    def __init__(self, parent=None, playback_backend=None):
        super().__init__(parent)
        self.session: EditorSession | None = None
        self.view = ViewState(self)
        self.tools = ToolState(self)
        self._bridge = _Bridge()
        self._bridge.call.connect(lambda fn: fn())
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="spledio-job")
        self._busy = False
        self.job_label = ""  # label of the running job ("" when idle)
        # quick jobs (area level changes) only show as busy if they take a noticeable time,
        # so the toolbar / panels don't blink grey on every key press
        self._busy_shown = False
        self._job_quiet = False
        self._held_events: list = []  # session events of a quiet job, delivered when it ends
        self._busy_timer = QTimer(self)
        self._busy_timer.setSingleShot(True)
        self._busy_timer.setInterval(400)
        self._busy_timer.timeout.connect(self._show_busy)
        self._job_cancel: threading.Event | None = None
        self.audition = AuditionState()
        self.loop_enabled = False
        self.preroll_s = 0.5
        self.postroll_s = 0.5
        self.monitor_volume = 1.0
        # (area id or None, "step"|"target", value, big, time.monotonic() of the press)
        self._area_queue: list[tuple[str | None, str, float, bool, float]] = []
        self._mix_source: ArraySource | None = None

        from spledio.playback.engine import PlaybackEngine
        from spledio.rendering.analysis import SpectrogramAnalyzer

        self.playback = PlaybackEngine(backend=playback_backend)
        self.playback.on_error = lambda msg: self._bridge.call.emit(lambda: self._on_playback_error(msg))
        self.playback.on_finished = lambda: self._bridge.call.emit(self._on_playback_finished)
        self.analyzer = SpectrogramAnalyzer(on_tile_ready=lambda: self._bridge.call.emit(self.tilesReady.emit))

        self._ph_timer = QTimer(self)
        self._ph_timer.setInterval(33)
        self._ph_timer.timeout.connect(self._poll_playhead)
        self._ph_timer.start()
        self._last_pos = -1
        self.view.changed.connect(self._on_view_changed)

    # ------------------------------------------------------------- session
    def has_session(self) -> bool:
        return self.session is not None

    def set_session(self, session: EditorSession) -> None:
        old = self.session
        self.stop()
        if old is not None:
            old.unsubscribe(self._on_session_event)
            old.close()
        self.session = session
        session.subscribe(self._on_session_event)
        self.view.bind_project(session.project.duration, session.project.nyquist)
        if session.view_state:
            self.view.load_dict(session.view_state)
        self.tools.transfer_target = None
        self._rebuild_mix_source()
        self._rebind_analyzer()
        self.playback.seek(0)
        self.sessionReplaced.emit()
        self.dirtyChanged.emit(session.dirty)

    def _on_session_event(self, ev: SessionEvent) -> None:
        # may run on a worker thread (edits run in jobs): marshal to GUI thread
        self._bridge.call.emit(lambda: self._handle_event(ev))

    def _handle_event(self, ev: SessionEvent) -> None:
        if self._busy and self._job_quiet:
            # a quick job: let the UI see its changes once it has finished, so nothing refreshes
            # into a "busy" (greyed-out) state in between – that is what made the window blink
            self._held_events.append(ev)
            return
        if ev.kind == "audio":
            self._update_mix_regions(ev.change)
            self._invalidate_display(ev.change)
            self.audioChanged.emit(ev.change)
        elif ev.kind == "structure":
            if self.session is not None and abs(self.view.duration - self.session.project.duration) > 1e-9:
                self.view.duration = self.session.project.duration
                self.view.set_time_range(self.view.t0, self.view.t1)
            self._rebuild_mix_source()
            self._rebind_analyzer()
            self.structureChanged.emit()
        elif ev.kind == "props":
            self._rebuild_mix_source()
            if self.view.layer_display == "composite":
                self._rebind_analyzer()
            self.propsChanged.emit()
        elif ev.kind == "selection":
            self.selectionChanged.emit()
        elif ev.kind == "history":
            self.historyChanged.emit()
        elif ev.kind == "preview":
            self.previewChanged.emit()
        elif ev.kind == "areas":
            self.areasChanged.emit()
        elif ev.kind == "dirty":
            self.dirtyChanged.emit(self.session.dirty if self.session else False)

    # ------------------------------------------------------ display analysis
    def _display_provider(self):
        s = self.session
        if self.view.layer_display == "active" and s.active_layer is not None:
            layer = s.active_layer
            return lambda a, b: layer.audio[:, max(0, a) : max(0, b)].astype(np.float64), layer.version
        return (lambda a, b: s.project.render_mix(max(0, a), max(0, b))), s.project.mix_version

    def _rebind_analyzer(self) -> None:
        s = self.session
        if s is None:
            return
        from spledio.rendering.analysis import DisplayAnalysisSettings

        self.analyzer.set_settings(DisplayAnalysisSettings(config=self.view.display_config, channel_mode=self.view.channel_mode))
        provider, version = self._display_provider()
        self.analyzer.set_audio(_padded(provider, s.project.n_frames, s.project.channels), s.project.n_frames,
                                s.project.channels, s.project.sample_rate, version)
        self.tilesReady.emit()

    def _invalidate_display(self, change: Change) -> None:
        s = self.session
        if s is None or not change.audio:
            return
        provider, version = self._display_provider()
        relevant = change.audio
        if self.view.layer_display == "active":
            relevant = [c for c in change.audio if c[0] == s.active_layer_id]
        if not relevant:
            return
        a = min(c[1] for c in relevant)
        b = max(c[2] for c in relevant)
        # provider closure may have changed (new layer objects); re-set without dropping tiles
        self.analyzer.invalidate(a, b, version)
        self.tilesReady.emit()

    @Slot(str)
    def _on_view_changed(self, what: str) -> None:
        if what == "analysis" and self.session is not None:
            self._rebind_analyzer()
        if self.session is not None:
            self.session.view_state = self.view.to_dict()

    # --------------------------------------------------------------- jobs
    @property
    def busy(self) -> bool:
        return self._busy

    def run_job(self, label: str, fn: Callable[[threading.Event], object], on_done: Callable[[object], None] | None = None,
                on_error: Callable[[BaseException], None] | None = None, quiet: bool = False) -> bool:
        """Run ``fn(cancel_event)`` on a worker; ``on_done(result)`` on the GUI thread.

        ``quiet`` (quick jobs such as area level changes): never announce ``busyChanged`` – the
        toolbar and panels must not grey out and back on every key press – and only show the
        status-bar spinner if the job runs longer than a moment. Its session events reach the UI
        when it ends. ``busy`` itself is True for the whole job either way."""
        if self._busy:
            self.statusMessage.emit("Busy: wait for the current operation or press Esc to cancel it", 4000)
            return False
        self._busy = True
        self.job_label = label
        self._job_cancel = threading.Event()
        cancel = self._job_cancel
        self._job_quiet = bool(quiet)
        if quiet:
            self._busy_timer.start()
        else:
            self._show_busy()

        def work():
            try:
                res = fn(cancel)
                self._bridge.call.emit(lambda res=res: self._job_finished(label, res, None, on_done, on_error))
            except BaseException as e:  # noqa: BLE001 - reported to the user
                tb = traceback.format_exc()
                # bind e/tb now: Python deletes the ``as e`` name when the except block ends
                self._bridge.call.emit(lambda e=e, tb=tb: self._job_finished(label, None, (e, tb), on_done, on_error))

        self.executor.submit(work)
        return True

    def cancel_job(self) -> None:
        if self._job_cancel is not None:
            self._job_cancel.set()

    def _show_busy(self) -> None:
        if self._busy and not self._busy_shown:
            self._busy_shown = True
            if not self._job_quiet:
                self.busyChanged.emit(True, self.job_label)
            self.busyIndicator.emit(True, self.job_label)

    def _flush_held_events(self) -> None:
        held, self._held_events = self._held_events, []
        for ev in held:
            self._handle_event(ev)

    def _job_finished(self, label, res, err, on_done, on_error):
        self._busy = False
        self.job_label = ""
        self._job_cancel = None
        self._busy_timer.stop()
        quiet, self._job_quiet = self._job_quiet, False
        if self._busy_shown:
            self._busy_shown = False
            if not quiet:
                self.busyChanged.emit(False, "")
            self.busyIndicator.emit(False, "")
        self._flush_held_events()
        self.jobEnded.emit()
        if err is None:
            if on_done:
                on_done(res)
            return
        e, tb = err
        if isinstance(e, Cancelled):
            self.statusMessage.emit(f"{label}: cancelled – nothing was changed", 5000)
        elif isinstance(e, EditError):
            self.statusMessage.emit(str(e), 6000)
            self.warning.emit(str(e))
        elif on_error is not None:
            on_error(e)
        else:
            self.error.emit(label, f"{e}\n\n{tb[-1500:]}")

    # --------------------------------------------------------------- edits
    def _edit(self, label: str, fn: Callable[[EditorSession, threading.Event], object]) -> None:
        s = self.session
        if s is None:
            return
        self.stop_audition_if_needed()

        def done(res):
            if res is not None and getattr(res, "warnings", None):
                for w in res.warnings:
                    self.warning.emit(w)
            if res is not None and hasattr(res, "command"):
                self.statusMessage.emit(res.command.label, 5000)

        self.run_job(label, lambda cancel: fn(s, cancel), done)

    def _ensure_scope(self, s: EditorSession):
        if s.selection is not None and s.selection.channels != self.tools.channel_scope:
            s._set_selection_raw(s.selection.with_channels(self.tools.channel_scope))

    def apply_gain(self, gain_db: float) -> None:
        def fn(s, cancel):
            self._ensure_scope(s)
            s.component(cancel=lambda: cancel.is_set())
            return s.apply_gain(gain_db)

        self._edit("Apply gain", fn)

    def mute_selection(self) -> None:
        self._edit("Mute", lambda s, c: (self._ensure_scope(s), s.component(cancel=c.is_set), s.mute_selection())[-1])

    def delete_spectral(self) -> None:
        self._edit("Delete spectral content",
                   lambda s, c: (self._ensure_scope(s), s.component(cancel=c.is_set), s.delete_spectral())[-1])

    def extract(self, copy: bool = False) -> None:
        self._edit("Copy to new layer" if copy else "Extract to new layer",
                   lambda s, c: (self._ensure_scope(s), s.component(cancel=c.is_set), s.extract_to_new_layer(copy=copy))[-1])

    def brush_stroke(self, brush: BrushShape, kind: str) -> None:
        tools = self.tools

        def fn(s, cancel):
            if kind == "reduce":
                return s.apply_brush(brush, tools.reduce_db)
            if kind == "amplify":
                return s.apply_brush(brush, tools.amplify_db)
            if kind == "transfer":
                if not tools.transfer_target:
                    raise EditError("Choose a target layer for the transfer brush (Layers panel)")
                return s.transfer_brush(brush, tools.transfer_target, tools.transfer_amount)
            raise ValueError(kind)

        self._edit("Brush stroke", fn)

    def preview_gain(self, gain_db: float, then_play: bool = True) -> None:
        def fn(s, cancel):
            self._ensure_scope(s)
            s.component(cancel=cancel.is_set)
            return s.preview_gain(gain_db)

        def done(pv):
            self.statusMessage.emit(f"Preview ready: {pv.label}. Space plays it, B toggles bypass, Enter applies.", 6000)
            if then_play:
                self.play_preview(bypass=False)

        if self.session is not None:
            self.run_job("Render preview", lambda cancel: fn(self.session, cancel), done)

    def commit_preview(self) -> None:
        self._edit("Apply", lambda s, c: s.commit_preview())

    def cancel_preview(self) -> None:
        if self.session is not None:
            self.session.cancel_preview()
            if self.audition.mode in ("preview", "bypass"):
                self.set_playback_source(self._mix_source, AuditionState())

    def undo(self) -> None:
        if self.session is None or self._busy:
            return
        self.stop_audition_if_needed()
        cmd = self.session.undo()
        if cmd is not None:
            self.statusMessage.emit(f"Undo: {cmd.label}", 4000)

    def redo(self) -> None:
        if self.session is None or self._busy:
            return
        self.stop_audition_if_needed()
        cmd = self.session.redo()
        if cmd is not None:
            self.statusMessage.emit(f"Redo: {cmd.label}", 4000)

    def safe(self, fn: Callable, *a, **kw):
        """Run a quick session call on the GUI thread, reporting EditError politely."""
        try:
            res = fn(*a, **kw)
            if res is not None and getattr(res, "warnings", None):
                for w in res.warnings:
                    self.warning.emit(w)
            return res
        except EditError as e:
            self.statusMessage.emit(str(e), 6000)
            self.warning.emit(str(e))
            return None

    # ------------------------------------------------------ adjusted areas
    AREA_JOB = "Adjust area level"

    def adjust_area(self, direction: int = 0, big: bool = False, target_db: float | None = None,
                    area_id: str | None = None) -> None:
        """↑/↓ (+/−) on a selection or picked area, or a level from the level bar / Areas panel.

        Changes made while a previous adjustment is still rendering are queued and applied
        together afterwards (steps add up, the latest typed/dragged level wins; nothing is dropped)."""
        s = self.session
        if s is None or (target_db is None and not direction):
            return
        now = time.monotonic()  # when the user asked (rendering may take a while on long files)
        if self._busy:
            if self.job_label == self.AREA_JOB:
                if target_db is not None:
                    self._area_queue.append((area_id, "target", float(target_db), False, now))
                else:
                    self._area_queue.append((area_id, "step", int(direction), bool(big), now))
            else:
                self.statusMessage.emit("Busy: wait for the current operation or press Esc to cancel it", 3000)
            return
        self._run_area_job(lambda sess: sess.adjust_area(direction, big, target_db, area_id, requested_at=now))

    def _run_area_job(self, fn) -> None:
        s = self.session
        if self.audition.mode != "mix" and self.playback.is_playing and self._mix_source is not None:
            # keep playing, but switch to the full mix so the adjustment is heard live
            self.set_playback_source(self._mix_source, AuditionState())

        def job(cancel):
            if s.current_area() is None:
                self._ensure_scope(s)
            return fn(s)

        def done(res):
            from spledio.engine.areas import gain_text

            a = s.current_area()
            if res is not None:
                for w in res.warnings:  # status bar only: no pop-up on every key press
                    self.statusMessage.emit("⚠ " + w, 8000)
                if a is not None and not res.warnings:
                    self.statusMessage.emit(f"{a.name}: {gain_text(a.gain_db)}   (↑/↓ 1 dB · Shift = 6 dB · "
                                            "0 = silence · Delete = remove adjustment)", 6000)
            elif a is not None and not self._area_queue:
                self.statusMessage.emit(f"{a.name} is already at {gain_text(a.gain_db)}", 3000)
            self._drain_area_queue(s)

        def failed(e):
            if isinstance(e, EditError):
                self.statusMessage.emit(str(e), 6000)
                self._drain_area_queue(s)  # e.g. a level dragged on past 0 dB still applies
            else:
                self._area_queue.clear()
                self.error.emit("Adjust area", f"{type(e).__name__}: {e}")

        self.run_job(self.AREA_JOB, job, done, failed, quiet=True)

    def _drain_area_queue(self, s) -> None:
        """Apply changes queued while the last adjustment was rendering, as one more job."""
        from spledio.engine.areas import step_gain

        q, self._area_queue = self._area_queue, []
        if not q or s is not self.session:
            return
        a = s.current_area()
        g0 = a.gain_db if a is not None else 0.0  # no area yet: the selection is at 0 dB
        g = g0
        others: dict[str, tuple[float, float]] = {}
        t = None  # earliest press folded into this job: it was made while the last change rendered
        for aid, kind, val, big, pressed in q:
            if aid is None or (a is not None and aid == a.id):
                g = step_gain(g, val, big) if kind == "step" else val
                t = pressed if t is None else min(t, pressed)
            elif kind == "target" and s.area(aid) is not None:
                others[aid] = (val, min(pressed, others[aid][1]) if aid in others else pressed)
        cur = a.id if a is not None else None  # None: make a new area from the selection
        jobs = ([(cur, (g, t))] if g != g0 else []) + list(others.items())
        if not jobs:
            return
        (aid, (g, t)), rest = jobs[0], jobs[1:]
        self._area_queue = [(k, "target", v, False, p) for k, (v, p) in rest]
        self._run_area_job(lambda sess, aid=aid, g=g, t=t: sess.adjust_area(target_db=g, area_id=aid, requested_at=t))

    def set_area_gain(self, area_id: str, gain_db: float) -> None:
        self.adjust_area(target_db=gain_db, area_id=area_id)

    def select_area(self, area_id: str | None) -> None:
        if self.session is not None:
            self.safe(self.session.select_area, area_id)

    def remove_area(self, area_id: str | None = None) -> None:
        s = self.session
        if s is None:
            return
        self._edit("Remove adjustment", lambda sess, c: sess.remove_area(area_id))

    def remove_all_areas(self) -> None:
        if self.session is not None:
            self._edit("Remove all adjustments", lambda sess, c: sess.remove_all_areas())

    def delete_or_remove(self) -> None:
        """Delete key: on a picked area, remove its adjustment (restores the audio there);
        otherwise silence the selection as a new adjusted area (it can be brought back later)."""
        s = self.session
        if s is None:
            return
        if s.current_area() is not None:
            self.remove_area()
        else:
            self.adjust_area(target_db=float("-inf"))

    # ----------------------------------------------------------- selection
    def commit_selection_shape(self, shape, mode: str | None = None) -> None:
        s = self.session
        if s is None:
            return
        mode = mode or self.tools.selection_mode
        base = s.selection or Selection(feather_t=self.tools.feather_t, feather_f=self.tools.feather_f)
        sel = base.combine(mode, shape) if s.selection is not None else Selection.of(
            shape, feather_t=self.tools.feather_t, feather_f=self.tools.feather_f)
        sel = sel.with_channels(self.tools.channel_scope)
        if mode == "subtract" and s.selection is None:
            return
        s.set_selection(sel)

    def set_channel_scope(self, scope: tuple[int, ...] | None) -> None:
        self.tools.set(channel_scope=scope)
        s = self.session
        if s is not None and s.selection is not None:
            s.set_selection(s.selection.with_channels(scope))

    # ------------------------------------------------------------ playback
    def _rebuild_mix_source(self) -> None:
        s = self.session
        if s is None:
            self._mix_source = None
            return
        mix = monitor_layout(s.render_full_mix())
        self._mix_source = ArraySource(mix, s.sample_rate, label="Mix", total_frames=s.project.n_frames)
        if self.audition.mode == "mix":
            self.playback.set_source(self._mix_source, keep_position=True)

    def _update_mix_regions(self, change: Change) -> None:
        s = self.session
        if s is None or self._mix_source is None or not change.audio:
            self._rebuild_mix_source()
            return
        buf = np.array(self._mix_source.data)  # (frames, ch) copy
        if buf.shape[0] != s.project.n_frames:
            self._rebuild_mix_source()
            return
        for _lid, a, b in change.audio:
            buf[a:b] = monitor_layout(s.project.render_mix(a, b, dtype=np.float32)).T
        self._mix_source = ArraySource(buf.T, s.sample_rate, label="Mix", total_frames=s.project.n_frames)
        if self.audition.mode == "mix":
            self.playback.set_source(self._mix_source, keep_position=True)

    def mix_source(self) -> PlaybackSource | None:
        return self._mix_source

    def set_playback_source(self, src: PlaybackSource, state: AuditionState) -> None:
        self.audition = state
        self.playback.set_source(src, keep_position=True)
        self.playbackChanged.emit()

    def play_pause(self) -> None:
        if self.session is None:
            return
        if self.playback.is_playing:
            self.playback.pause()
        else:
            if self.audition.mode not in ("mix", "preview", "bypass", "ab_a", "ab_b") or self.playback.source is None:
                self.set_playback_source(self._mix_source, AuditionState())
            self._apply_loop()
            self.playback.play()
        self.playbackChanged.emit()

    def stop(self) -> None:
        self.playback.stop()
        self.playbackChanged.emit()

    def return_to_start(self) -> None:
        self.playback.seek(0)
        self.playheadChanged.emit(0)

    def seek_seconds(self, t: float) -> None:
        if self.session is None:
            return
        f = int(np.clip(t, 0, self.session.project.duration) * self.session.sample_rate)
        self.playback.seek(f)
        self.playheadChanged.emit(f)

    def set_loop_enabled(self, on: bool) -> None:
        self.loop_enabled = bool(on)
        self._apply_loop()
        self.playbackChanged.emit()

    def loop_range(self) -> tuple[int, int] | None:
        s = self.session
        if s is None:
            return None
        if s.selection is not None:
            b = s.selection.bounds(s.project.duration, s.project.nyquist)
            if b is not None and b[1] - b[0] > 0.01:
                return int(b[0] * s.sample_rate), int(b[1] * s.sample_rate)
        return 0, s.project.n_frames

    def _apply_loop(self) -> None:
        r = self.loop_range() if self.loop_enabled else None
        if r is None:
            self.playback.clear_loop()
        else:
            self.playback.set_loop(*r)

    def set_monitor_volume(self, v: float) -> None:
        self.monitor_volume = float(v)
        self.playback.set_volume(self.monitor_volume)

    def stop_audition_if_needed(self) -> None:
        if self.audition.mode != "mix":
            self.playback.stop()
            self.set_playback_source(self._mix_source, AuditionState()) if self._mix_source else None

    def _play_region(self, src: PlaybackSource, state: AuditionState, a: int, b: int) -> None:
        s = self.session
        sr = s.sample_rate
        start = max(0, a - int(self.preroll_s * sr))
        end = min(s.project.n_frames, b + int(self.postroll_s * sr))
        self.audition = state
        self.playback.stop()
        self.playback.set_source(src, keep_position=False)
        if self.loop_enabled:
            self.playback.set_loop(start, end)
            self.playback.play(start)
        else:
            self.playback.clear_loop()
            self.playback.play(start, end)
        self.playbackChanged.emit()
        self.statusMessage.emit(f"Hearing: {state.label}", 4000)

    def play_selection_in_mix(self) -> None:
        """Play the full mix over the selection's time span (with pre/post-roll), or stop.

        This is how an adjusted area actually sounds in context; it keeps playing while the
        level is changed, so every change is heard live."""
        s = self.session
        if s is None or self._mix_source is None:
            return
        if self.playback.is_playing and self.audition.mode == "mix":
            self.stop()
            return
        r = self.loop_range()
        if r is None:
            return
        self._play_region(self._mix_source, AuditionState(), r[0], r[1])

    def audition_mode(self, mode: str) -> None:
        """mode: 'selected' | 'all_freqs' | 'remainder' | 'difference'."""
        s = self.session
        if s is None:
            return

        def fn(cancel):
            self._ensure_scope(s)
            if mode in ("selected", "remainder"):
                s.component(cancel=cancel.is_set)
            return {
                "selected": s.audition_selected,
                "all_freqs": s.audition_all_frequencies,
                "remainder": s.audition_remainder,
                "difference": s.difference_buffer,
            }[mode]()

        def done(buf):
            src = ArraySource(monitor_layout(buf.data), s.sample_rate, label=buf.label, offset=buf.start,
                              total_frames=s.project.n_frames)
            self._play_region(src, AuditionState(mode, buf.label), buf.start, buf.end)

        self.run_job("Render audition", fn, done)

    def play_preview(self, bypass: bool = False) -> None:
        s = self.session
        if s is None or s.preview is None:
            self.statusMessage.emit("No preview: set a gain and press Preview first", 4000)
            return
        start, a_reg, b_reg, label = s.ab_regions()
        region = monitor_layout(a_reg if bypass else b_reg)
        src = OverlaySource(self._mix_source, start, region, label="Bypass (original)" if bypass else "Preview")
        if self.playback.is_playing and self.audition.mode in ("preview", "bypass"):
            self.set_playback_source(src, AuditionState("bypass" if bypass else "preview",
                                                         "Bypass – original" if bypass else f"Preview – {label}", start))
            return
        self._play_region(src, AuditionState("bypass" if bypass else "preview",
                                             "Bypass – original" if bypass else f"Preview – {label}", start),
                          start, start + region.shape[1])

    def toggle_bypass(self) -> None:
        if self.audition.mode == "preview":
            self.play_preview(bypass=True)
        elif self.audition.mode == "bypass":
            self.play_preview(bypass=False)
        else:
            self.play_preview(bypass=True)

    def ab_toggle(self) -> None:
        """A/B the pending preview, else the last edit. Keeps the playback position."""
        s = self.session
        if s is None:
            return
        try:
            start, a_reg, b_reg, label = s.ab_regions()
        except EditError as e:
            self.statusMessage.emit(str(e), 4000)
            return
        going_a = self.audition.mode != "ab_a"
        region = monitor_layout(a_reg if going_a else b_reg)
        src = OverlaySource(self._mix_source, start, region, label="A original" if going_a else "B processed")
        st = AuditionState("ab_a" if going_a else "ab_b", ("A – original (before: " if going_a else "B – processed (") + label + ")", start)
        if self.playback.is_playing:
            self.set_playback_source(src, st)
            self.statusMessage.emit(f"Hearing: {st.label}", 4000)
        else:
            self._play_region(src, st, start, start + region.shape[1])

    def _poll_playhead(self) -> None:
        pos = self.playback.position
        if self.playback.is_playing and self.session is not None:
            lat = self.playback.state().latency_seconds or 0.0
            pos = max(0, pos - int(lat * self.session.sample_rate))  # position leads what is audible
        if pos != self._last_pos:
            self._last_pos = pos
            self.playheadChanged.emit(pos)

    def _on_playback_error(self, msg: str) -> None:
        self.statusMessage.emit(f"Playback stopped: {msg}", 0)
        self.warning.emit(f"Audio device problem: {msg}. Check the output device and press Play again.")
        self.playbackChanged.emit()

    def _on_playback_finished(self) -> None:
        self.playbackChanged.emit()

    # ------------------------------------------------------------ files
    def open_audio(self, path: str, on_done: Callable[[], None] | None = None) -> None:
        def fn(cancel):
            return EditorSession.from_audio_file(path)

        def done(sess):
            self.set_session(sess)
            p = sess.project
            self.statusMessage.emit(
                f"Opened {os.path.basename(path)} – {p.sample_rate} Hz, {p.channels} ch, {p.duration:.2f} s "
                "(source file is never modified)", 8000)
            if on_done:
                on_done()

        self.run_job(f"Open {os.path.basename(path)}", fn, done,
                     on_error=lambda e: self.error.emit("Could not open audio", str(e)))

    def shutdown(self) -> None:
        """Stop audio, workers and timers and break reference cycles, so the
        controller is freed on the GUI thread (Qt timers must not be destroyed
        from a worker thread by a late garbage collection)."""
        if getattr(self, "_shut_down", False):
            return
        self._shut_down = True
        self._ph_timer.stop()
        self._busy_timer.stop()
        try:
            self.playback.on_error = None
            self.playback.on_finished = None
            self.playback.close()
        except Exception:
            pass
        try:
            self.analyzer.on_tile_ready = None
            self.analyzer.shutdown()
        except Exception:
            pass
        if self._job_cancel is not None:
            self._job_cancel.set()
        self.executor.shutdown(wait=True, cancel_futures=True)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)  # PySide warns if already disconnected
            for sig, slot in ((self._bridge.call, None), (self.view.changed, self._on_view_changed)):
                try:
                    sig.disconnect() if slot is None else sig.disconnect(slot)
                except (RuntimeError, TypeError):
                    pass
        if self.session is not None:
            self.session.unsubscribe(self._on_session_event)
            self.session.close()
            self.session = None


def monitor_layout(audio: np.ndarray) -> np.ndarray:
    """Monitoring only: duplicate mono to two channels so it plays on both speakers.
    Project audio, edits and exports are never changed by this."""
    if audio.shape[0] == 1:
        return np.repeat(audio, 2, axis=0)
    return audio


def _padded(provider, n_frames: int, channels: int):
    """Wrap a provider so requests outside [0, n) return zeros (analysis zero-padding)."""

    def get(a: int, b: int) -> np.ndarray:
        out = np.zeros((channels, max(0, b - a)))
        lo, hi = max(a, 0), min(b, n_frames)
        if hi > lo:
            out[:, lo - a : hi - a] = provider(lo, hi)
        return out

    return get
