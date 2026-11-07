"""Generate docs/ACTIONS.md (the button/action behaviour guide) from the live action registry.

Usage: QT_QPA_PLATFORM=offscreen .venv/bin/python scripts/gen_action_docs.py
"""

import inspect
import os
import re
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QKeySequence  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def reason_text(spec, win):
    fn = spec.enabled_when
    if fn is None:
        return "Always available"
    name = getattr(fn, "__name__", "")
    table = {
        "_need_session": "No audio/project open",
        "_need_idle": "No project open, or an operation is running",
        "_need_sel": "No selection (or busy / no project)",
        "_need_editable_sel": "No selection, active layer locked, busy, or no project",
        "_need_preview": "No pending preview",
        "_need_undo": "Nothing to undo (or busy)",
        "_need_redo": "Nothing to redo (or busy)",
        "_need_edit_history": "No preview and no audio edit yet",
    }
    if name in table:
        return table[name]
    try:
        src = inspect.getsource(fn).strip()
        m = re.findall(r'"([^"]{6,})"', src)
        return "; ".join(m) if m else "See tooltip"
    except (OSError, TypeError):
        return "See tooltip"


def main():
    app = QApplication.instance() or QApplication(sys.argv[:1])
    from spledio.playback.engine import FakeBackend
    from spledio.ui.controller import EditorController
    from spledio.ui.main_window import MainWindow

    win = MainWindow(EditorController(playback_backend=FakeBackend()))
    lines = [
        "# Button & action guide",
        "",
        "Generated from the live action registry by `scripts/gen_action_docs.py` — every toolbar button,",
        "menu item and shortcut below runs the same handler. *Changes audio* = alters what plays/exports;",
        "*Undoable* = creates one history entry. View/display actions never change audio.",
        "Disabled actions show the reason in their tooltip and in the status bar.",
        "",
    ]
    by_menu: dict[str, list] = {}
    for spec in win.actions.specs.values():
        by_menu.setdefault(spec.menu.split("/")[0], []).append(spec)
    for menu, specs in by_menu.items():
        lines += [f"## {menu}", "", "| Action | Shortcut | What it does | Acts on | Changes audio | Undoable | Disabled when | Status |",
                  "|---|---|---|---|---|---|---|---|"]
        for s in specs:
            seqs = [s.shortcut] if isinstance(s.shortcut, str) else list(s.shortcut or ())
            sc = " / ".join(QKeySequence(k).toString(QKeySequence.PortableText) or k for k in seqs if k)
            sc = sc.replace("|", "\\|")
            lines.append(
                f"| **{s.label}** `{s.id}` | {sc or '—'} | {s.tooltip} | {s.acts_on or ('view' if not s.changes_audio else 'project')} | "
                f"{'yes' if s.changes_audio else 'no'} | {'yes' if s.undoable else 'no'} | {reason_text(s, win)} | {s.status} |"
            )
        lines.append("")
    lines += [
        "## Mouse & modifiers on the spectrogram",
        "",
        "| Gesture | Effect |",
        "|---|---|",
        "| Drag with a selection tool | Create a selection (Replace by default) |",
        "| Shift + drag | Add to selection |",
        "| Alt/Option + drag | Subtract from selection |",
        "| Shift + Alt/Option + drag | Intersect with selection |",
        "| Click (no drag) | Move the playhead there (selection kept) |",
        "| Drag a rectangle handle | Resize the rectangle selection |",
        "| Wheel / trackpad | Pan time |",
        "| Ctrl/Cmd + wheel | Zoom time around the cursor |",
        "| Shift + wheel | Zoom frequency around the cursor |",
        "| Esc | Cancel drawing → cancel running job → cancel preview → clear selection (in that order) |",
        "| Enter / double-click | Finish polygon lasso |",
        "",
        "Single-key shortcuts (Space, P, B, A, S, R, D, L, T, F, …) are suspended while a text or number field has focus,",
        "so typing a value never triggers playback or an edit. Shortcuts can be remapped in Help → Keyboard Shortcuts.",
        "",
    ]
    out = os.path.join(ROOT, "docs", "ACTIONS.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"wrote {out} ({len(win.actions.specs)} actions)")
    win.ctl.shutdown()


if __name__ == "__main__":
    main()
