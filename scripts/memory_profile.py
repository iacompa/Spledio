"""Memory profile of Spledio in the running app (offscreen Qt, the real main window). macOS only.

usage (from the repository root):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=src .venv/bin/python scripts/memory_profile.py MINUTES AREA_MINUTES OUT.json
        [--tracemalloc]

Generates a 44.1 kHz stereo 16-bit WAV of MINUTES (two sines plus noise; kept in
$TMPDIR/spledio-memprof/ for later runs), opens it, waits for the spectrogram to
settle, makes a -6 dB adjusted area over AREA_MINUTES (200-4000 Hz, from 30 s),
exports the mix as 32-bit float WAV through the window's export job, opens the
export dialog, and undoes the area. A thread reads the process's resident size
and phys_footprint (what Activity Monitor shows as Memory) every 5 ms; each
phase records its peak and the value 3 s after it (after gc). Every run also
checks that the export equals the playback mix and that undo is bit-exact.

To measure another version, point PYTHONPATH at its ``src`` (for example a
``git archive <commit> src`` copy). MEMPROF_RAM_GB=8 sizes the RAM-based budgets of versions that have
``spledio/ram.py`` (tile cache, undo RAM) as on a machine with that much RAM. --tracemalloc lists
the live allocations of 20 MB or more after opening and after the area.
"""

from __future__ import annotations

import ctypes
import gc
import json
import os
import sys
import tempfile
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import soundfile as sf

HERE = os.path.join(tempfile.gettempdir(), "spledio-memprof")
os.makedirs(HERE, exist_ok=True)
SR = 44100

# ---------------------------------------------------------------- mach memory
_libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
_task = ctypes.c_uint.in_dll(_libc, "mach_task_self_")
TASK_VM_INFO = 22


def mem() -> tuple[int, int]:
    """(resident_size, phys_footprint) in bytes."""
    buf = (ctypes.c_uint64 * 128)()
    count = ctypes.c_uint(ctypes.sizeof(buf) // 4)
    r = _libc.task_info(_task, TASK_VM_INFO, ctypes.byref(buf), ctypes.byref(count))
    if r != 0:
        raise OSError(r)
    raw = bytes(buf)
    resident = int.from_bytes(raw[16:24], "little")
    footprint = int.from_bytes(raw[144:152], "little")
    return resident, footprint


class Sampler(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.peak_rss = self.peak_fp = 0
        self.lock = threading.Lock()
        self.stop = False

    def reset(self):
        with self.lock:
            self.peak_rss, self.peak_fp = mem()

    def run(self):
        while not self.stop:
            r, f = mem()
            with self.lock:
                self.peak_rss = max(self.peak_rss, r)
                self.peak_fp = max(self.peak_fp, f)
            time.sleep(0.005)


def make_file(minutes: float) -> str:
    path = os.path.join(HERE, f"test_{minutes:g}min.wav")
    if os.path.exists(path):
        return path
    n = int(minutes * 60 * SR)
    rng = np.random.default_rng(1)
    blk = SR * 10
    with sf.SoundFile(path, "w", SR, 2, format="WAV", subtype="PCM_16") as f:
        for a in range(0, n, blk):
            b = min(n, a + blk)
            t = np.arange(a, b) / SR
            x = 0.2 * np.sin(2 * np.pi * 440 * t) + 0.1 * np.sin(2 * np.pi * 2500 * t + 0.3)
            y = np.stack([x, 0.8 * x]) + 0.05 * rng.standard_normal((2, b - a))
            f.write(y.T.astype(np.float32))
    return path


GB = 1e9


def main() -> int:
    minutes, area_min, out_path = float(sys.argv[1]), float(sys.argv[2]), sys.argv[3]
    trace = "--tracemalloc" in sys.argv
    path = make_file(minutes)
    if trace:
        import tracemalloc

        tracemalloc.start(8)

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv[:1])
    ram_gb = os.environ.get("MEMPROF_RAM_GB")
    if ram_gb:  # size the RAM-based budgets as on a machine with this much RAM (new code only)
        import spledio.ram

        spledio.ram.physical_memory = lambda: int(float(ram_gb) * 2**30)
    from spledio.ui.main_window import MainWindow

    win = MainWindow()
    win.resize(1440, 900)
    win.show()
    ctl = win.ctl
    smp = Sampler()
    smp.start()
    res: dict = {"minutes": minutes, "area_minutes": area_min, "file": os.path.basename(path)}
    tmp = os.path.join(HERE, "out")
    os.makedirs(tmp, exist_ok=True)

    def settled_mem():
        gc.collect()
        time.sleep(3.0)  # macOS returns freed blocks to the system lazily (about 2 s)
        return mem()

    def record(name, t0):
        r, f = settled_mem()
        with smp.lock:
            pr, pf = smp.peak_rss, smp.peak_fp
        res[name] = {"rss": r / GB, "footprint": f / GB, "peak_rss": pr / GB, "peak_footprint": pf / GB,
                     "seconds": time.monotonic() - t0,
                     "tile_cache": ctl.analyzer.cache_bytes() / GB,
                     "patch_store_ram": (ctl.session.history.store.ram_bytes / GB) if ctl.session else 0.0}
        print(f"{name:>8}: rss {r / GB:.2f} GB (peak {pr / GB:.2f}), footprint {f / GB:.2f} GB (peak {pf / GB:.2f}),"
              f" {res[name]['seconds']:.1f} s, tiles {res[name]['tile_cache']:.3f} GB,"
              f" patch RAM {res[name]['patch_store_ram']:.3f} GB", flush=True)

    def snapshot(name):
        if not trace:
            return
        import tracemalloc

        snap = tracemalloc.take_snapshot()
        stats = snap.statistics("traceback")
        print(f"--- tracemalloc after {name}: {sum(s.size for s in stats) / GB:.2f} GB traced")
        for s in stats[:12]:
            if s.size < 20e6:
                break
            print(f"{s.size / 1e6:9.1f} MB  {s.count} blocks")
            for line in s.traceback.format()[-8:]:
                print("      " + line)

    def wait(cond, then, limit=900.0):
        t0 = time.monotonic()

        def poll():
            try:
                ok = cond()
                if ok:
                    then()
            except BaseException:
                import traceback
                traceback.print_exc()
                os._exit(3)
            if ok:
                return
            elif time.monotonic() - t0 > limit:
                print("TIMEOUT", flush=True)
                app.exit(2)
            else:
                QTimer.singleShot(20, poll)

        QTimer.singleShot(20, poll)

    def settled():
        return (ctl.session is not None and not ctl.busy and win.editor.canvas.is_settled()
                and win.editor.canvas.render_count > 0)

    state = {}

    def start():
        r, f = settled_mem()
        res["baseline"] = {"rss": r / GB, "footprint": f / GB}
        print(f"baseline: rss {r / GB:.2f} GB, footprint {f / GB:.2f} GB", flush=True)
        smp.reset()
        state["t"] = time.monotonic()
        win.open_path(path)
        wait(settled, opened)

    def opened():
        QTimer.singleShot(1500, opened2)  # let late tiles land

    def opened2():
        wait(settled, opened3)

    def opened3():
        record("open", state["t"])
        snapshot("open")
        import hashlib
        state["sha"] = hashlib.sha1(memoryview(ctl.session.project.layers[0].audio)).hexdigest()
        from spledio.selections.geometry import RectShape

        s = ctl.session
        t0 = 30.0
        ctl.commit_selection_shape(RectShape(t0, t0 + area_min * 60.0, 200.0, 4000.0))
        smp.reset()
        state["t"] = time.monotonic()
        ctl.adjust_area(target_db=-6.0)
        wait(lambda: settled() and len(s.areas) == 1, area_done)

    def area_done():
        QTimer.singleShot(1500, lambda: wait(settled, area_done2))

    def area_done2():
        record("area", state["t"])
        snapshot("area")
        from spledio.audio.export import ExportSettings

        smp.reset()
        state["t"] = time.monotonic()
        out = os.path.join(tmp, f"export_{minutes:g}.wav")
        if os.path.exists(out):
            os.remove(out)
        req = {"path": out, "scope": "mix", "layer_ids": None, "time_range": None,
               "settings": ExportSettings(format="WAV", subtype="FLOAT", overwrite=True)}
        win._run_export(req)
        wait(lambda: not ctl.busy, export_done)

    def export_done():
        record("export", state["t"])
        out = os.path.join(tmp, f"export_{minutes:g}.wav")
        # correctness: the export equals the playback mix bit for bit (float32 WAV)
        s = ctl.session
        with sf.SoundFile(out) as f:
            ok = f.frames == s.project.n_frames
            blk = 1 << 20
            src = ctl.mix_source().data
            for a in range(0, f.frames, blk):
                d = f.read(blk, dtype="float32", always_2d=True)
                if not np.array_equal(d, src[a : a + len(d)]):
                    ok = False
                    break
        res["export_equals_playback_mix"] = bool(ok)
        print("export == playback mix:", ok, flush=True)
        os.remove(out)
        # export dialog peak measurement (rendered when the dialog opens)
        from spledio.ui.export_dialog import ExportDialog

        gc.collect()
        smp.reset()
        state["t"] = time.monotonic()
        dlg = ExportDialog(s, win)
        record("dialog", state["t"])
        res["dialog_peak_text"] = dlg.peak.text()
        dlg.deleteLater()
        # undo the area: restores the original bit-exactly
        smp.reset()
        state["t"] = time.monotonic()
        ctl.undo()
        wait(lambda: settled() and len(s.areas) == 0, undone)

    def undone():
        record("undo", state["t"])
        import hashlib
        res["undo_bit_exact"] = hashlib.sha1(memoryview(ctl.session.project.layers[0].audio)).hexdigest() == state["sha"]
        print("undo bit-exact:", res["undo_bit_exact"], flush=True)
        with open(out_path, "w") as f:
            json.dump(res, f, indent=1)
        smp.stop = True
        ctl.shutdown()
        app.exit(0)

    QTimer.singleShot(300, start)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
