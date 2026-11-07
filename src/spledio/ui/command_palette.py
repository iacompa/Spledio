"""Command palette: search every registry action by name and run it.

Typing filters the list (every word must match the label, the menu path, the
id or the description; whole substrings score higher than scattered
letters). Unavailable commands stay in the list, greyed, with the reason.
Enter runs the highlighted command if it is available; Esc closes.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QColor, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHeaderView,
    QLabel,
    QLineEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

DISABLED_COLOR = "#6b7480"


@dataclass
class PaletteEntry:
    id: str
    label: str
    menu: str
    shortcut: str
    tooltip: str
    reason: str | None
    checkable: bool
    checked: bool
    icon: object = None

    @property
    def enabled(self) -> bool:
        return self.reason is None


def _subsequence_score(q: str, t: str) -> float | None:
    """Fuzzy match: all letters of ``q`` in order in ``t``. Fewer gaps = higher score."""
    pos = -1
    gaps = 0
    first = None
    for ch in q:
        nxt = t.find(ch, pos + 1)
        if nxt < 0:
            return None
        if first is None:
            first = nxt
        elif nxt != pos + 1:
            gaps += nxt - pos - 1
        pos = nxt
    return max(1.0, 30.0 - gaps - 0.2 * (first or 0))


def _token_score(tok: str, text: str) -> float | None:
    t = text.lower()
    idx = t.find(tok)
    if idx >= 0:
        word_start = idx == 0 or not t[idx - 1].isalnum()
        return 100.0 - min(idx, 40) * 0.5 + (40.0 if word_start else 0.0) + (25.0 if t == tok else 0.0)
    return _subsequence_score(tok, t)


def match_score(query: str, entry: PaletteEntry) -> float | None:
    """Score of ``entry`` for ``query`` (None = no match). Every word of the query must match somewhere."""
    tokens = query.lower().split()
    if not tokens:
        return 0.0
    hay = ((entry.label, 3.0), (f"{entry.menu} {entry.id}", 1.5), (entry.tooltip, 0.4))
    total = 0.0
    for tok in tokens:
        best = None
        for text, weight in hay:
            s = _token_score(tok, text)
            if s is not None:
                best = max(best or 0.0, s * weight)
        if best is None:
            return None
        total += best
    return total


def collect_entries(actions) -> list[PaletteEntry]:
    out = []
    for aid, spec in actions.specs.items():
        act = actions.actions.get(aid)
        try:
            reason = spec.enabled_when() if spec.enabled_when else None
        except Exception as e:  # noqa: BLE001
            reason = f"unavailable ({e})"
        if spec.status == "unavailable" and reason is None:
            reason = "not available in this build"
        if act is not None:
            sc = " / ".join(s.toString(QKeySequence.NativeText) for s in act.shortcuts() if not s.isEmpty())
            checked = act.isChecked()
            ic = act.icon() if not act.icon().isNull() else None
        else:
            seqs = [spec.shortcut] if isinstance(spec.shortcut, str) else list(spec.shortcut)
            sc = " / ".join(QKeySequence(s).toString(QKeySequence.NativeText) for s in seqs if s)
            checked = bool(spec.checked()) if spec.checked else False
            ic = None
        out.append(PaletteEntry(aid, spec.label, spec.menu.replace("/", " ▸ "), sc, spec.tooltip, reason,
                                spec.checkable, checked, ic))
    return out


def run_action(actions, aid: str) -> bool:
    """Trigger ``aid`` exactly like its menu item would (even if the QAction's enabled state is stale)."""
    spec = actions.specs.get(aid)
    act = actions.actions.get(aid)
    if spec is None:
        return False
    try:
        reason = spec.enabled_when() if spec.enabled_when else None
    except Exception:  # noqa: BLE001
        reason = "unavailable"
    if reason:
        return False
    if act is not None and act.isEnabled():
        act.trigger()
    elif act is not None and act.isCheckable():
        act.toggle()
    else:
        spec.handler()
    return True


class CommandPalette(QDialog):
    """Searchable list of all registry actions."""

    def __init__(self, actions, parent=None):
        super().__init__(parent)
        self.actions = actions
        self.setWindowTitle("Command palette")
        self.setObjectName("CommandPalette")
        self.resize(620, 440)
        self.last_run: str | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        self.filter_edit = QLineEdit()
        self.filter_edit.setObjectName("paletteFilter")
        self.filter_edit.setPlaceholderText("Type to search commands… (Enter runs, Esc closes)")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.setAccessibleName("Search commands")
        lay.addWidget(self.filter_edit)
        self.list = QTreeWidget()
        self.list.setObjectName("paletteList")
        self.list.setColumnCount(3)
        self.list.setHeaderLabels(["Command", "Shortcut", "Menu"])
        self.list.setRootIsDecorated(False)
        self.list.setUniformRowHeights(True)
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list.setAlternatingRowColors(True)
        hh = self.list.header()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hh.setStretchLastSection(False)
        lay.addWidget(self.list, 1)
        self.info = QLabel()
        self.info.setObjectName("paletteInfo")
        self.info.setWordWrap(True)
        self.info.setStyleSheet("color: #9aa4b2;")
        lay.addWidget(self.info)
        self.entries = collect_entries(actions)
        self.filter_edit.textChanged.connect(self.set_filter)
        self.list.currentItemChanged.connect(lambda *_: self._show_info())
        self.list.itemActivated.connect(lambda *_: self.run_current())
        self.filter_edit.installEventFilter(self)
        self.set_filter("")
        self.filter_edit.setFocus()

    # ------------------------------------------------------------------ filtering
    def set_filter(self, text: str) -> None:
        if self.filter_edit.text() != text:
            self.filter_edit.blockSignals(True)
            self.filter_edit.setText(text)
            self.filter_edit.blockSignals(False)
        scored = []
        for i, e in enumerate(self.entries):
            s = match_score(text, e)
            if s is not None:
                scored.append((s, e, i))
        if text.strip():
            scored.sort(key=lambda x: (-x[0], not x[1].enabled, x[1].label.lower()))
        else:
            scored.sort(key=lambda x: (x[1].menu.lower(), x[2]))
        self.list.clear()
        grey = QColor(DISABLED_COLOR)
        for _s, e, _i in scored:
            label = e.label + ((" (on)" if e.checked else " (off)") if e.checkable else "")
            if not e.enabled:
                label += f" — unavailable: {e.reason}"
            it = QTreeWidgetItem([label, e.shortcut, e.menu])
            it.setData(0, Qt.UserRole, e.id)
            tip = e.tooltip + (f"\nUnavailable: {e.reason}" if e.reason else "")
            for col in range(3):
                it.setToolTip(col, tip)
                if not e.enabled:
                    it.setForeground(col, grey)
            if e.icon is not None:
                it.setIcon(0, e.icon)
            self.list.addTopLevelItem(it)
        # highlight the best available match
        target = None
        for row in range(self.list.topLevelItemCount()):
            it = self.list.topLevelItem(row)
            if self._entry(it.data(0, Qt.UserRole)).enabled:
                target = it
                break
        if target is None and self.list.topLevelItemCount():
            target = self.list.topLevelItem(0)
        self.list.setCurrentItem(target)
        self._show_info()

    def visible_ids(self) -> list[str]:
        return [self.list.topLevelItem(i).data(0, Qt.UserRole) for i in range(self.list.topLevelItemCount())]

    def current_id(self) -> str | None:
        it = self.list.currentItem()
        return None if it is None else it.data(0, Qt.UserRole)

    def select_id(self, aid: str) -> bool:
        for i in range(self.list.topLevelItemCount()):
            it = self.list.topLevelItem(i)
            if it.data(0, Qt.UserRole) == aid:
                self.list.setCurrentItem(it)
                return True
        return False

    def _entry(self, aid: str | None) -> PaletteEntry | None:
        return next((e for e in self.entries if e.id == aid), None)

    def _show_info(self) -> None:
        e = self._entry(self.current_id())
        if e is None:
            self.info.setText("No matching command." if self.filter_edit.text().strip() else "")
            return
        text = e.tooltip
        if e.reason:
            text = f"<span style='color:#f0a050'>Unavailable: {e.reason}</span><br>{text}"
        self.info.setText(text)

    # ------------------------------------------------------------------ running
    def run_current(self) -> bool:
        """Run the highlighted command if it is available; the palette closes first."""
        aid = self.current_id()
        e = self._entry(aid)
        if e is None:
            return False
        # re-check now (state may have changed since the palette opened)
        spec = self.actions.specs.get(aid)
        try:
            reason = spec.enabled_when() if spec is not None and spec.enabled_when else None
        except Exception as ex:  # noqa: BLE001
            reason = str(ex)
        if reason:
            self.info.setText(f"<span style='color:#f0a050'>Cannot run “{e.label}”: {reason}</span>")
            return False
        self.last_run = aid
        self.accept()
        return run_action(self.actions, aid)

    def eventFilter(self, obj: QObject, ev: QEvent) -> bool:
        if obj is self.filter_edit and ev.type() == QEvent.KeyPress:
            key = ev.key()
            steps = {Qt.Key_Down: 1, Qt.Key_Up: -1, Qt.Key_PageDown: 10, Qt.Key_PageUp: -10}
            if key in steps:
                n = self.list.topLevelItemCount()
                if n:
                    cur = self.list.currentItem()
                    row = self.list.indexOfTopLevelItem(cur) if cur is not None else -1
                    row = min(n - 1, max(0, row + steps[key]))
                    self.list.setCurrentItem(self.list.topLevelItem(row))
                return True
            if key in (Qt.Key_Return, Qt.Key_Enter):
                self.run_current()
                return True
        return super().eventFilter(obj, ev)

    def keyPressEvent(self, ev) -> None:
        if ev.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.run_current()
            return
        super().keyPressEvent(ev)  # Esc -> reject()


def open_command_palette(parent, actions) -> CommandPalette:
    """Show the palette (window-modal, non-blocking) and return it."""
    dlg = CommandPalette(actions, parent)
    dlg.setAttribute(Qt.WA_DeleteOnClose, True)
    if parent is not None:
        g = parent.geometry()
        dlg.move(g.x() + (g.width() - dlg.width()) // 2, g.y() + 60)
    dlg.open()
    dlg.filter_edit.setFocus()
    return dlg


__all__ = ["CommandPalette", "open_command_palette", "match_score", "collect_entries", "run_action", "PaletteEntry"]
