"""History dock: the undo stack (oldest at the top) followed by the redo
stack in grey italics. Clicking an entry undoes / redoes until that entry is
the last applied one; the top "Initial state" row undoes everything.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QListWidget,
    QListWidgetItem,
)

from spledio.ui.actions import ActionRegistry
from spledio.ui.panels.common import TEXT_DIM, PanelBase, button_grid, note_label

POSITION_ROLE = Qt.ItemDataRole.UserRole  # number of applied commands if this row is the current one


class _HistoryList(QListWidget):
    """Return / Enter / Space jump to the current row (item views do not activate on Return on macOS)."""

    def __init__(self, panel: "HistoryPanel"):
        super().__init__()
        self.panel = panel

    def keyPressEvent(self, ev):  # noqa: N802 - Qt API
        if ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space) and self.currentItem() is not None:
            self.panel._on_item_chosen(self.currentItem())
            return
        super().keyPressEvent(ev)


class HistoryPanel(PanelBase):
    """History dock panel. ``HistoryPanel(controller, actions, parent=None)``."""

    def __init__(self, controller, actions: ActionRegistry, parent=None):
        super().__init__(controller, actions, parent, scrollable=False)
        self.setObjectName("HistoryPanel")
        self.setWindowTitle("History")
        self._navigating = False
        lay = self.body_layout

        self.list = _HistoryList(self)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.list.setAccessibleName("Undo history")
        self.list.setToolTip("Click a step (or press Enter on it) to undo / redo up to that step. "
                             "Grey italic steps are undone and can be redone.")
        self.list.itemClicked.connect(self._on_item_chosen)
        lay.addWidget(self.list, 1)
        self.position_label = note_label("")
        lay.addWidget(self.position_label)

        self.btn_undo = self.action_button("Undo", "Undo the last step", "edit.undo", self.controller.undo, "undo")
        self.btn_redo = self.action_button("Redo", "Redo the next step", "edit.redo", self.controller.redo, "redo")
        lay.addLayout(button_grid([self.btn_undo, self.btn_redo], 2))

        self.record_selection = QCheckBox("Record selection changes in history")
        self.record_selection.setToolTip("When on, every selection change becomes an undo step of its own. "
                                         "Selections never change audio either way.")
        self.record_selection.toggled.connect(self._on_record_selection)
        lay.addWidget(self.record_selection)
        lay.addWidget(note_label("Undo history is kept for this session; the project file stores the edited audio "
                                 "and an operation log."))

        c = controller
        c.sessionReplaced.connect(self.refresh)
        c.historyChanged.connect(self.refresh)
        c.dirtyChanged.connect(lambda *_: self.refresh())
        c.busyChanged.connect(lambda *_: self._update_enabled())
        self.refresh()

    # ------------------------------------------------------------ queries
    def entries(self) -> list[tuple[str, str]]:
        """``[(kind, label)]`` as shown: kind is 'initial', 'applied' or 'undone'."""
        out = []
        for i in range(self.list.count()):
            it = self.list.item(i)
            out.append((it.data(Qt.ItemDataRole.UserRole + 1), it.data(Qt.ItemDataRole.UserRole + 2)))
        return out

    def current_row(self) -> int:
        s = self.session
        return len(s.history.undo_stack) if s is not None else -1

    # ------------------------------------------------------------ refresh
    def refresh(self) -> None:
        if self._navigating:
            return
        s = self.session
        self.set_session_available(s is not None)
        self.list.blockSignals(True)
        try:
            self.list.clear()
            if s is None:
                self.position_label.setText("")
                self.record_selection.blockSignals(True)
                self.record_selection.setChecked(False)
                self.record_selection.blockSignals(False)
                return
            h = s.history
            undo_cmds = list(h.undo_stack)
            redo_cmds = list(reversed(h.redo_stack))  # next to redo first
            saved_at = h.saved_index()  # position of the last save on this path, or None
            n_applied = len(undo_cmds)
            dim = QBrush(QColor(TEXT_DIM))

            def add(text: str, kind: str, position: int, label: str, tooltip: str):
                if position == n_applied:
                    text += "   ◀ current"
                it = QListWidgetItem(text)
                it.setData(POSITION_ROLE, position)
                it.setData(Qt.ItemDataRole.UserRole + 1, kind)
                it.setData(Qt.ItemDataRole.UserRole + 2, label)
                it.setToolTip(tooltip)
                if kind == "undone":
                    f = QFont(self.list.font())
                    f.setItalic(True)
                    it.setFont(f)
                    it.setForeground(dim)
                if position == n_applied:
                    f = QFont(it.font())
                    f.setBold(True)
                    it.setFont(f)
                self.list.addItem(it)
                return it

            init_saved = saved_at == 0
            dropped = len(undo_cmds) >= h.limit  # the stack is capped; older steps may be gone
            init_label = "Initial state" + (" (older steps may have been dropped)" if dropped else "")
            add(init_label + ("  • saved" if init_saved else ""), "initial", 0, init_label,
                "Undo every listed step")
            for i, cmd in enumerate(undo_cmds, start=1):
                mark = "  • saved" if saved_at == i else ""
                add(f"{i}. {cmd.label}{mark}", "applied", i, cmd.label,
                    f"{cmd.label}\nApplied. Click to undo everything after this step.")
            for j, cmd in enumerate(redo_cmds, start=1):
                pos = n_applied + j
                mark = "  • saved" if saved_at == pos else ""
                add(f"{pos}. {cmd.label}{mark}", "undone", pos, cmd.label,
                    f"{cmd.label}\nUndone. Click to redo up to and including this step.")
            cur = self.list.item(n_applied)
            if cur is not None:
                self.list.setCurrentItem(cur)
                self.list.scrollToItem(cur)
            self.position_label.setText(
                f"{n_applied} step{'s' if n_applied != 1 else ''} applied · {len(redo_cmds)} undone "
                f"(keeps up to {h.limit} steps)" + (" · unsaved changes" if s.dirty else ""))
            self.record_selection.blockSignals(True)
            self.record_selection.setChecked(bool(s.record_selection_history))
            self.record_selection.blockSignals(False)
        finally:
            self.list.blockSignals(False)
        self._update_enabled()

    def _update_enabled(self) -> None:
        busy = getattr(self.controller, "busy", False)
        ok = self.session is not None and not busy
        self.list.setEnabled(ok)
        s = self.session
        self.btn_undo.set_local_enabled(ok and s is not None and s.history.can_undo(),
                                        "busy" if busy else "nothing to undo")
        self.btn_redo.set_local_enabled(ok and s is not None and s.history.can_redo(),
                                        "busy" if busy else "nothing to redo")

    # ------------------------------------------------------------ handlers
    def _on_item_chosen(self, item: QListWidgetItem) -> None:
        if item is None:
            return
        self.go_to(int(item.data(POSITION_ROLE)))

    def go_to(self, position: int) -> None:
        """Undo / redo until ``position`` commands are applied (0 = initial state)."""
        s = self.session
        if s is None or self.guard_busy():
            return
        h = s.history
        position = max(0, min(position, len(h.undo_stack) + len(h.redo_stack)))
        self._navigating = True
        try:
            while len(h.undo_stack) > position:
                before = len(h.undo_stack)
                self.controller.undo()
                if len(h.undo_stack) >= before:  # refused (busy) - stop instead of looping
                    break
            while len(h.undo_stack) < position:
                before = len(h.undo_stack)
                self.controller.redo()
                if len(h.undo_stack) <= before:
                    break
        finally:
            self._navigating = False
        self.refresh()

    def _on_record_selection(self, on: bool) -> None:
        s = self.session
        if s is not None:
            s.record_selection_history = bool(on)


__all__ = ["HistoryPanel"]
