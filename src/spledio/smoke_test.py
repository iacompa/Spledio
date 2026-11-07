"""``spledio --smoke-test FILE``: check that an installed / packaged app really works.

Runs inside the real application (the same code path as a user, the real window and
the bundled libraries) and prints a report:

* versions of the bundled libraries, and whether the app is frozen (PyInstaller);
* every dock panel loaded (none fell back to the "panel failed to load" text);
* the app icon loaded;
* PortAudio loads and lists output devices;
* the app's real output stream runs for a moment, playing **silence** (this exercises the
  PortAudio → cffi callback, the part most likely to break under the hardened runtime);
* FILE opens, the spectrogram renders;
* an adjusted area at −6 dB changes the audio there and only there;
* WAV export equals the playback mix; save + reopen gives the identical mix.

Exit code 0 when everything passed, 1 otherwise. Temporary files are removed.
Used by ``scripts/release_macos.sh`` after freezing and again after signing.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time

import numpy as np


def _say(msg: str) -> None:
    print(msg, flush=True)


def run(app, win, path: str, timeout: float = 60.0, on_done=None, play_silence: bool = True) -> None:
    """Schedule the checks on the running event loop; at the end calls ``on_done(code, results)``
    (default: ``app.exit(code)``). ``play_silence=False`` skips opening the real output device
    (for the headless test suite)."""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox, QPlainTextEdit

    results: list[tuple[str, bool, str]] = []
    tmp = tempfile.mkdtemp(prefix="spledio-smoke-")
    ctl = win.ctl
    state: dict = {}

    def check(name: str, ok: bool, detail: str = "") -> bool:
        results.append((name, bool(ok), detail))
        _say(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" – {detail}" if detail else ""))
        return bool(ok)

    def finish() -> None:
        be = state.pop("backend", None)
        if be is not None:
            be.close()
        failed = [r for r in results if not r[1]]
        _say("SMOKE TEST " + ("PASSED" if not failed else f"FAILED ({len(failed)} of {len(results)} checks)"))
        shutil.rmtree(tmp, ignore_errors=True)
        code = 0 if not failed else 1
        if on_done is not None:
            on_done(code, results)
            return
        try:
            ctl.shutdown()
        finally:
            app.exit(code)

    def wait(cond, then, what: str, limit: float = timeout) -> None:
        t0 = time.monotonic()

        def poll() -> None:
            try:
                ok = cond()
            except Exception as e:  # report, don't hang
                check(what, False, f"{type(e).__name__}: {e}")
                finish()
                return
            if ok:
                then()
            elif time.monotonic() - t0 > limit:
                check(what, False, f"timed out after {limit:.0f} s")
                finish()
            else:
                QTimer.singleShot(50, poll)

        QTimer.singleShot(50, poll)

    def step(fn):
        def wrapped() -> None:
            try:
                fn()
            except Exception as e:
                import traceback

                check(fn.__name__, False, f"{type(e).__name__}: {e}\n{traceback.format_exc()}")
                finish()

        return wrapped

    @step
    def s1_static() -> None:
        import PySide6
        import scipy
        import sounddevice
        import soundfile

        from spledio import __version__

        _say(f"Spledio {__version__} · frozen={bool(getattr(sys, 'frozen', False))} · Python {sys.version.split()[0]}")
        _say(f"Qt/PySide6 {PySide6.__version__} · NumPy {np.__version__} · SciPy {scipy.__version__} · "
             f"libsndfile {soundfile.__libsndfile_version__} · {sounddevice.get_portaudio_version()[1]}")
        broken = []
        for key, dock in win.docks.items():
            for ed in dock.findChildren(QPlainTextEdit):
                if "failed to load" in ed.toPlainText():
                    broken.append(f"{key}: {ed.toPlainText().splitlines()[-1]}")
        check("all dock panels loaded", not broken and len(win.docks) >= 7,
              "; ".join(broken) or f"{len(win.docks)} panels")
        from spledio.ui.icons import app_icon

        ic = app_icon()
        check("app icon loaded", ic is not None and not ic.isNull() and bool(ic.availableSizes()),
              f"{len(ic.availableSizes()) if ic is not None else 0} sizes")
        try:
            devs = sounddevice.query_devices()
            outs = [d for d in devs if d["max_output_channels"] > 0]
            default = sounddevice.query_devices(kind="output")["name"] if outs else "none"
            check("PortAudio loads and lists output devices", True, f"{len(outs)} output(s), default: {default}")
        except Exception as e:
            check("PortAudio loads and lists output devices", False, f"{type(e).__name__}: {e}")
            outs = []
        if play_silence and outs:
            from spledio.playback.engine import SoundDeviceBackend

            calls = state["calls"] = [0]

            def silent(outdata, frames) -> bool:  # audio thread
                outdata.fill(0.0)
                calls[0] += 1
                return True

            be = state["backend"] = SoundDeviceBackend()
            be.open(48000, 2, silent)
            be.start()
            wait(lambda: calls[0] >= 5, s1b_stream, "output stream callback runs (silence)", limit=10.0)
        else:
            s1c_open()

    @step
    def s1b_stream() -> None:
        be = state.pop("backend")
        name, lat = be.device_name, be.latency_seconds or 0.0
        be.close()
        check("output stream callback runs (silence)", True,
              f"{state['calls'][0]} callbacks on {name}, latency {lat * 1000:.1f} ms")
        s1c_open()

    def refusal():
        """The error box the app shows when it cannot open the file, if one is up."""
        box = QApplication.activeModalWidget()
        return box if isinstance(box, QMessageBox) else None

    @step
    def s1c_open() -> None:
        win.open_path(os.path.abspath(path))
        # a file the app refuses never opens: report its message now, not a timeout a minute later
        wait(lambda: refusal() is not None or (ctl.session is not None and not ctl.busy), s1d_opened_or_refused,
             "open audio file")

    @step
    def s1d_opened_or_refused() -> None:
        box = refusal()
        if box is None:
            s2_opened()
            return
        check("open audio file", False, f"refused: {box.text()}")  # macOS message boxes have no title
        box.reject()
        finish()

    @step
    def s2_opened() -> None:
        s = ctl.session
        p = s.project
        check("open audio file", p.n_frames > 0, f"{p.sample_rate} Hz, {p.channels} ch, {p.duration:.2f} s")
        state["orig"] = s.render_full_mix()  # a fresh float32 array
        wait(lambda: win.editor.canvas.is_settled() and win.editor.canvas.render_count > 0, s3_rendered,
             "spectrogram renders")

    @step
    def s3_rendered() -> None:
        check("spectrogram renders", True, f"{win.editor.canvas.render_count} render(s)")
        from spledio.selections.geometry import RectShape

        s = ctl.session
        d = s.project.duration
        state["span"] = (0.25 * d, 0.75 * d)
        ctl.commit_selection_shape(RectShape(state["span"][0], state["span"][1], 1000.0, 3000.0))
        ctl.adjust_area(target_db=-6.0)
        wait(lambda: not ctl.busy and len(s.areas) == 1, s4_area, "adjusted area at -6 dB")

    @step
    def s4_area() -> None:
        s = ctl.session
        mix = s.render_full_mix()
        a = s.areas[0]
        sr = s.sample_rate
        lead = max(0, a.start - 1)  # samples before the area's span must be untouched
        changed = not np.allclose(mix, state["orig"])
        untouched = np.array_equal(mix[:, :lead], state["orig"][:, :lead])
        check("adjusted area at -6 dB", a.gain_db == -6.0 and changed and untouched,
              f"{a.name} {a.gain_db:+.0f} dB, span {a.start / sr:.2f}–{a.end / sr:.2f} s, audio before it unchanged: {untouched}")
        from spledio.audio.export import ExportSettings

        out = os.path.join(tmp, "export.wav")
        s.export(out, ExportSettings(format="WAV", subtype="FLOAT"))
        import soundfile as sf

        data, rate = sf.read(out, dtype="float32", always_2d=True)  # a FLOAT export: exact in float32
        err = float(np.max(np.abs(data.T - mix))) if data.T.shape == mix.shape else float("inf")
        check("WAV export equals the playback mix", rate == sr and err < 1e-6, f"max difference {err:.1e}")
        proj = os.path.join(tmp, "smoke.spledio")
        s.view_state = ctl.view.to_dict()
        s.save(proj)
        from spledio.engine.session import EditorSession

        r = EditorSession.open_project(proj)
        try:
            same = np.array_equal(r.render_full_mix(), mix) and len(r.areas) == 1
        finally:
            r.close()
        check("save + reopen project gives the identical mix", same, f"{os.path.getsize(proj)} bytes")
        finish()

    QTimer.singleShot(200, s1_static)


__all__ = ["run"]
