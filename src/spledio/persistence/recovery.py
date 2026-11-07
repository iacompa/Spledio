"""Autosave + crash recovery.

While a project has unsaved changes, a recovery copy is written every
``AUTOSAVE_SECONDS`` to the per-user data directory (see
:func:`recovery_dir`) using the normal project writer (linked to the source
audio, not portable). A small JSON journal records the original project path
and time. A clean save or a clean quit deletes the recovery files. On the
next launch, leftover recovery files (from a crash) are offered for restore
or discard. Nothing leaves the computer.
"""

from __future__ import annotations

import json
import os
import re
import time

from spledio.branding import APP_NAME

AUTOSAVE_SECONDS = 120
_SAFE = re.compile(r"^[a-z]+-[0-9a-f]{12}$")


def recovery_dir() -> str:
    try:
        from PySide6.QtCore import QStandardPaths

        base = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)
    except Exception:
        base = ""
    if not base:
        base = os.path.join(os.path.expanduser("~"), f".{APP_NAME.lower()}")
    d = os.path.join(base, "Recovery")
    os.makedirs(d, exist_ok=True)
    return d


def _paths(project_id: str) -> tuple[str, str]:
    if not _SAFE.match(project_id):
        raise ValueError("bad project id")
    d = recovery_dir()
    return os.path.join(d, f"{project_id}.spledio"), os.path.join(d, f"{project_id}.json")


def write_recovery(session) -> str:
    from spledio.persistence.project_file import save_project

    proj, journal = _paths(session.project.id)
    save_project(session, proj, portable=False)
    with open(journal + ".tmp", "w", encoding="utf-8") as f:
        json.dump({"project_path": session.path, "name": session.project.name, "time": time.time()}, f)
    os.replace(journal + ".tmp", journal)
    return proj


def clear_recovery(project_id: str) -> None:
    try:
        for p in _paths(project_id):
            if os.path.exists(p):
                os.remove(p)
    except ValueError:
        pass


def list_recoveries() -> list[dict]:
    out = []
    d = recovery_dir()
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".json"):
            continue
        pid = fn[:-5]
        if not _SAFE.match(pid):
            continue
        proj = os.path.join(d, pid + ".spledio")
        if not os.path.exists(proj):
            continue
        try:
            with open(os.path.join(d, fn), encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            meta = {}
        out.append({"id": pid, "file": proj, **(meta if isinstance(meta, dict) else {})})
    return out


def offer_recovery(win) -> None:
    from PySide6.QtWidgets import QMessageBox

    items = list_recoveries()
    for it in items:
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(it.get("time", 0)))
        r = QMessageBox.question(
            win, "Recover unsaved work?",
            f"{APP_NAME} found unsaved work from {when}: “{it.get('name', '?')}”"
            + (f"\n(project: {it['project_path']})" if it.get("project_path") else "")
            + "\n\nRestore it? (No = discard the recovery copy)",
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
        if r == QMessageBox.Yes:
            win.open_project(it["file"], recovered_from=it)
            return
        if r == QMessageBox.No:
            clear_recovery(it["id"])


def start_autosave(win) -> None:
    from PySide6.QtCore import QTimer

    timer = QTimer(win)
    timer.setInterval(AUTOSAVE_SECONDS * 1000)

    def tick():
        s = win.ctl.session
        if s is None or not s.dirty or win.ctl.busy:
            return
        try:
            write_recovery(s)
        except Exception as e:  # noqa: BLE001
            win.statusBar().showMessage(f"Autosave failed: {e}", 5000)

    timer.timeout.connect(tick)
    timer.start()
    win._autosave_timer = timer
