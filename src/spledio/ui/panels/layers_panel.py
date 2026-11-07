"""Layers dock: per-layer mute / solo / lock / polarity / visibility / gain,
active-layer ("editing") selection, rename, structure operations, merge,
export of selected layers and the transfer-brush target.

Every audible change goes through :meth:`EditorSession.set_layer_props` and
friends (via ``controller.safe``), so each one is exactly one undo step.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QItemSelectionModel, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
)

from spledio.engine.session import channel_scope_label
from spledio.ui.actions import ActionRegistry
from spledio.ui.panels.common import (
    ACCENT,
    BORDER,
    TEXT_DIM,
    WARN,
    Combo,
    ElidedLabel,
    PanelBase,
    SpinBox,
    button_grid,
    esc,
    note_label,
)

GAIN_MIN_DB, GAIN_MAX_DB = -60.0, 24.0

SOLO_MUTE_RULE = "Solo does not override mute: a muted layer stays silent even when it is soloed."

TOGGLES = (
    # attribute, button text, layer property, tooltip
    ("btn_mute", "M", "mute", "Mute: silence this layer in playback and export. " + SOLO_MUTE_RULE),
    ("btn_solo", "S", "solo", "Solo: while any layer is soloed only soloed layers are heard. " + SOLO_MUTE_RULE),
    ("btn_lock", "L", "locked", "Lock: protect this layer's audio from edits (gain, brushes, extract, merge, delete)."),
    ("btn_polarity", "Ø", "polarity_inverted",
     "Polarity invert: flip this layer's sign in the mix (useful to null it against another layer)."),
    ("btn_visible", "Vis", "visible",
     "Visible in the spectrogram (composite display). Display only: never affects playback or export."),
)


class LayerRow(QFrame):
    """Widget shown for one layer inside the list (two compact lines)."""

    def __init__(self, panel: "LayersPanel", layer_id: str):
        super().__init__()
        self.panel = panel
        self.layer_id = layer_id
        self._active = False
        self.setObjectName("layerRow")
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 3, 4, 3)
        v.setSpacing(2)

        top = QHBoxLayout()
        top.setSpacing(5)
        self.swatch = QLabel()
        self.swatch.setFixedSize(12, 12)
        self.swatch.setToolTip("Layer colour")
        top.addWidget(self.swatch)
        self.name_stack = QStackedWidget()
        self.name_label = ElidedLabel()
        self.name_label.setToolTip("Double-click (or F2) to rename")
        self.name_edit = QLineEdit()
        self.name_edit.setAccessibleName("Layer name")
        self.name_edit.setToolTip("Enter renames (one undo step); Esc cancels")
        self.name_edit.editingFinished.connect(self._finish_rename)
        self.name_edit.installEventFilter(self)
        self.name_stack.addWidget(self.name_label)
        self.name_stack.addWidget(self.name_edit)
        self.name_stack.setFixedHeight(self.name_edit.sizeHint().height())
        top.addWidget(self.name_stack, 1)
        self.badge = QLabel("EDITING")
        self.badge.setToolTip("This is the active layer: spectral edits, brushes and extraction act on it")
        self.badge.setStyleSheet(
            f"color: #0b0d10; background: {ACCENT}; border-radius: 3px; padding: 0 4px; font-size: 10px; font-weight: bold;")
        top.addWidget(self.badge)
        v.addLayout(top)

        bottom = QHBoxLayout()
        bottom.setSpacing(2)
        for attr, text, prop, tip in TOGGLES:
            b = QToolButton()
            b.setText(text)
            b.setCheckable(True)
            b.setToolTip(tip)
            b.setAccessibleName(f"{prop.replace('_', ' ')} toggle")
            b.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            b.setMinimumWidth(26 if len(text) == 1 else 32)
            b.clicked.connect(lambda checked, p=prop: self.panel._toggle_prop(self.layer_id, p, checked))
            setattr(self, attr, b)
            bottom.addWidget(b)
        bottom.addStretch(1)
        self.gain = SpinBox(GAIN_MIN_DB, GAIN_MAX_DB, 0.1, 1, " dB",
                            "Layer gain in the mix (−60 … +24 dB). Applied on Enter or when leaving the field: "
                            "one undo step per change. The stored samples are not altered.",
                            accessible="Layer gain")
        self.gain.setMinimumWidth(78)
        self.gain.editingFinished.connect(lambda: self.panel._commit_gain(self.layer_id, self.gain.value()))
        bottom.addWidget(self.gain)
        v.addLayout(bottom)
        self._apply_style()

    # ------------------------------------------------------------- state
    def update_from(self, layer, active: bool) -> None:
        self.swatch.setStyleSheet(f"background: {layer.color}; border: 1px solid {BORDER}; border-radius: 2px;")
        name = layer.name
        if layer.locked:
            name += "  (locked)"
        self.name_label.set_full_text(name)
        self.name_label.setToolTip(f"{layer.name}\nDouble-click (or F2) to rename")
        for attr, _text, prop, _tip in TOGGLES:
            b = getattr(self, attr)
            want = bool(getattr(layer, prop))
            if b.isChecked() != want:
                b.blockSignals(True)
                b.setChecked(want)
                b.blockSignals(False)
        self.gain.sync(min(max(layer.gain_db, GAIN_MIN_DB), GAIN_MAX_DB))
        if active != self._active:
            self._active = active
            self._apply_style()

    def _apply_style(self) -> None:
        self.badge.setVisible(self._active)
        edge = ACCENT if self._active else "transparent"
        self.setStyleSheet(f"QFrame#layerRow {{ border-left: 3px solid {edge}; }}")
        f = self.name_label.font()
        f.setBold(self._active)
        self.name_label.setFont(f)

    @property
    def active(self) -> bool:
        return self._active

    # ------------------------------------------------------------- rename
    def begin_rename(self) -> None:
        s = self.panel.session
        if s is None or not s.project.has_layer(self.layer_id):
            return
        self.name_edit.setText(s.project.layer(self.layer_id).name)
        self.name_stack.setCurrentWidget(self.name_edit)
        self.name_edit.setFocus(Qt.FocusReason.OtherFocusReason)
        self.name_edit.selectAll()

    @property
    def renaming(self) -> bool:
        return self.name_stack.currentWidget() is self.name_edit

    def cancel_rename(self) -> None:
        self.name_stack.setCurrentWidget(self.name_label)

    def _finish_rename(self) -> None:
        if not self.renaming:
            return
        text = self.name_edit.text().strip()
        self.name_stack.setCurrentWidget(self.name_label)
        if text:
            self.panel._rename(self.layer_id, text)

    def eventFilter(self, obj, ev):  # noqa: N802 - Qt API
        if obj is self.name_edit and ev.type() == QEvent.Type.KeyPress:
            if ev.key() == Qt.Key.Key_Escape:
                self.cancel_rename()
                self.panel.list.setFocus()
                return True
            if ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                # consume: an ignored Return would reach the list and start renaming again
                self._finish_rename()
                self.panel.list.setFocus()
                return True
        return super().eventFilter(obj, ev)

    def mouseDoubleClickEvent(self, ev):  # noqa: N802 - Qt API
        self.begin_rename()
        ev.accept()


class _LayerList(QListWidget):
    """List with F2 / Return = rename the current layer (Return is the macOS convention)."""

    def __init__(self, panel: "LayersPanel"):
        super().__init__()
        self.panel = panel

    def keyPressEvent(self, ev):  # noqa: N802 - Qt API
        if ev.key() in (Qt.Key.Key_F2, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            row = self.panel.row_for_item(self.currentItem())
            if row is not None:
                row.begin_rename()
                return
        super().keyPressEvent(ev)


class LayersPanel(PanelBase):
    """Layers dock panel. ``LayersPanel(controller, actions, parent=None)``."""

    def __init__(self, controller, actions: ActionRegistry, parent=None):
        super().__init__(controller, actions, parent, scrollable=False)
        self.setObjectName("LayersPanel")
        self.setWindowTitle("Layers")
        self._updating = False
        self._ids: list[str] = []
        self._rows: dict[str, LayerRow] = {}
        lay = self.body_layout

        self.banner = QLabel()
        self.banner.setWordWrap(True)
        self.banner.setTextFormat(Qt.TextFormat.RichText)
        self.banner.setToolTip("The active layer receives every spectral edit; the channel scope is set in the "
                               "Selection panel. Click a row to make it the active layer.")
        self.banner.setStyleSheet(f"padding: 4px 6px; border: 1px solid {BORDER}; border-radius: 4px;")
        lay.addWidget(self.banner)

        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filter layers by name")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.setAccessibleName("Filter layers by name")
        self.filter_edit.setToolTip("Show only layers whose name contains this text (display only)")
        self.filter_edit.textChanged.connect(self._apply_filter)
        lay.addWidget(self.filter_edit)

        self.list = _LayerList(self)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.setMinimumHeight(90)
        self.list.setAccessibleName("Layers")
        self.list.setToolTip("Click a layer to edit it. Ctrl/Cmd- or Shift-click selects several layers for "
                             "Merge or Export. Double-click, F2 or Return renames.")
        self.list.setStyleSheet("QListWidget::item:selected { background: #24405a; }"
                                f"QListWidget::item {{ border-bottom: 1px solid {BORDER}; }}")
        self.list.currentItemChanged.connect(self._on_current_changed)
        self.list.itemSelectionChanged.connect(self._update_buttons)
        lay.addWidget(self.list, 1)
        self.count_label = note_label("")
        lay.addWidget(self.count_label)

        s_ = lambda: self.session  # noqa: E731
        self.btn_new = self.action_button("New empty", "Add a new silent layer (undoable)", "layer.new",
                                          lambda: self.call(s_().add_empty_layer), "layer_new")
        self.btn_duplicate = self.action_button(
            "Duplicate", "Duplicate the active layer; the copy is summed with the original (louder mix)",
            "layer.duplicate", lambda: self.call(s_().duplicate_layer, s_().active_layer_id), "layer_dup")
        self.btn_delete = self.action_button("Delete", "Delete the active layer (undoable)", "layer.delete",
                                             lambda: self.call(s_().delete_layer, s_().active_layer_id), "layer_delete")
        self.btn_up = self.action_button("Move up", "Move the active layer one row up (undoable; mix unchanged)",
                                         "layer.up", lambda: self._move(-1), "up")
        self.btn_down = self.action_button("Move down", "Move the active layer one row down (undoable; mix unchanged)",
                                           "layer.down", lambda: self._move(+1), "down")
        self.btn_merge = self.action_button(
            "Merge selected",
            "Sum the selected layers (Ctrl/Cmd- or Shift-click rows) into one new unity-gain layer and remove them. "
            "Merges at their current gains and polarity and ignores mute/solo (merged as if audible). Undoable.",
            "", self.merge_selected, "merge")
        self.btn_export = self.action_button(
            "Export selected…", "Open the export dialog for the selected layers (summed at their gains)",
            "", self.export_selected, "export")
        lay.addLayout(button_grid([self.btn_new, self.btn_duplicate, self.btn_delete, self.btn_up, self.btn_down,
                                   self.btn_merge, self.btn_export], columns=2))

        # transfer brush target
        tr = QHBoxLayout()
        tr.setSpacing(4)
        tr_label = QLabel("Transfer to")
        tr_label.setToolTip("Target layer for the Transfer brush (moves painted content from the active layer)")
        tr.addWidget(tr_label)
        self.transfer_combo = Combo("Target layer for the Transfer brush. The sum of both layers is preserved.",
                                    "Transfer target layer")
        self.transfer_combo.activated.connect(self._on_transfer_target)
        tr.addWidget(self.transfer_combo, 1)
        self.transfer_amount = SpinBox(0, 100, 5, 0, " %", "How much of the painted content each Transfer brush "
                                       "stroke moves to the target layer (0–100 %)", "Transfer amount")
        self.transfer_amount.valueChanged.connect(self._on_transfer_amount)
        tr.addWidget(self.transfer_amount)
        lay.addLayout(tr)
        lay.addWidget(note_label("Solo does not override mute. “Vis” only affects the display."))

        c = controller
        c.sessionReplaced.connect(self.refresh)
        c.structureChanged.connect(self.refresh)
        c.propsChanged.connect(self.refresh)
        c.historyChanged.connect(self._update_buttons)
        c.tools.changed.connect(self._refresh_tools)
        if hasattr(c, "busyChanged"):
            c.busyChanged.connect(lambda *_: self._update_buttons())
        self.refresh()

    # ------------------------------------------------------------ queries
    def row_for_item(self, item: QListWidgetItem | None) -> LayerRow | None:
        if item is None:
            return None
        return self._rows.get(item.data(Qt.ItemDataRole.UserRole))

    def row(self, layer_id: str) -> LayerRow | None:
        return self._rows.get(layer_id)

    def item(self, layer_id: str) -> QListWidgetItem | None:
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.data(Qt.ItemDataRole.UserRole) == layer_id:
                return it
        return None

    def selected_layer_ids(self) -> list[str]:
        """Selected (and visible) layer ids in project order."""
        if self.session is None:
            return []
        sel = {it.data(Qt.ItemDataRole.UserRole) for it in self.list.selectedItems() if not it.isHidden()}
        return [l.id for l in self.session.project.layers if l.id in sel]

    def visible_layer_ids(self) -> list[str]:
        return [self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count())
                if not self.list.item(i).isHidden()]

    # ------------------------------------------------------------ refresh
    def refresh(self) -> None:
        s = self.session
        self.set_session_available(s is not None)
        if s is None:
            self._updating = True
            try:
                self.list.clear()
            finally:
                self._updating = False
            self._rows.clear()
            self._ids = []
            self.banner.setText("Editing: — (no project open)")
            self.transfer_combo.set_items([("(no layers)", None)])
            self.count_label.setText("")
            self._update_buttons()
            return
        ids = [l.id for l in s.project.layers]
        if ids != self._ids:
            self._rebuild(ids)
        for l in s.project.layers:
            row = self._rows.get(l.id)
            if row is not None:
                row.update_from(l, l.id == s.active_layer_id)
        self._sync_current()
        self._refresh_banner()
        self._refresh_transfer()
        self._apply_filter()
        self._update_buttons()

    def _rebuild(self, ids: list[str]) -> None:
        keep_sel = set(self.selected_layer_ids()) if self._ids else set()
        self._updating = True
        try:
            self.list.clear()
            self._rows.clear()
            for lid in ids:
                it = QListWidgetItem()
                it.setData(Qt.ItemDataRole.UserRole, lid)
                row = LayerRow(self, lid)
                self.list.addItem(it)
                self.list.setItemWidget(it, row)
                it.setSizeHint(row.sizeHint())
                self._rows[lid] = row
                if lid in keep_sel:
                    it.setSelected(True)
            self._ids = list(ids)
        finally:
            self._updating = False

    def _sync_current(self) -> None:
        s = self.session
        it = self.item(s.active_layer_id) if s.active_layer_id else None
        if it is None or self.list.currentItem() is it:
            return
        self._updating = True
        try:
            # keep a deliberate multi-selection (merge / export); otherwise select the active row
            flag = (QItemSelectionModel.SelectionFlag.NoUpdate if len(self.list.selectedItems()) > 1
                    else QItemSelectionModel.SelectionFlag.ClearAndSelect)
            self.list.setCurrentItem(it, flag)
        finally:
            self._updating = False

    def _refresh_banner(self) -> None:
        s = self.session
        if s is None:
            return
        layer = s.active_layer
        scope = channel_scope_label(self.controller.tools.channel_scope, s.project.channels)
        if layer is None:
            self.banner.setText("Editing: — (no layer)")
            return
        parts = [f"Editing: <b>{esc(layer.name)}</b> · {esc(scope)}"]
        if self.controller.tools.channel_scope is not None and len(self.controller.tools.channel_scope) < s.project.channels:
            parts[0] = f"Editing: <b>{esc(layer.name)}</b> · <span style='color:{WARN}'>{esc(scope)}</span>"
        if layer.locked:
            parts.append(f"<span style='color:{WARN}'>locked – unlock it to edit</span>")
        if layer not in s.project.audible_layers():
            parts.append(f"<span style='color:{TEXT_DIM}'>not audible (muted / not soloed) – edits still apply</span>")
        self.banner.setText(" · ".join(parts))

    def _refresh_tools(self) -> None:
        if self.session is None:
            return
        self._refresh_banner()
        self._refresh_transfer()

    def _refresh_transfer(self) -> None:
        s = self.session
        tools = self.controller.tools
        if s is None:
            return
        others = [l for l in s.project.layers if l.id != s.active_layer_id]
        target = tools.transfer_target
        if target is not None and target not in {l.id for l in others}:
            target = None
            tools.set(transfer_target=None)  # deleted layer or now the active one: no stale target
        items = [("(choose target layer)", None)] + [(l.name, l.id) for l in others]
        self.transfer_combo.set_items(items, target)
        self.transfer_combo.setEnabled(bool(others))
        self.transfer_amount.sync(round(float(tools.transfer_amount) * 100.0))

    def _apply_filter(self) -> None:
        text = self.filter_edit.text().strip().lower()
        s = self.session
        shown = 0
        for i in range(self.list.count()):
            it = self.list.item(i)
            lid = it.data(Qt.ItemDataRole.UserRole)
            name = s.project.layer(lid).name.lower() if s is not None and s.project.has_layer(lid) else ""
            hide = bool(text) and text not in name
            it.setHidden(hide)
            if hide and it.isSelected():
                it.setSelected(False)  # never merge/export a layer the user cannot see
            shown += not hide
        n = self.list.count()
        if s is None:
            self.count_label.setText("")
        elif text:
            self.count_label.setText(f"{shown} of {n} layers shown (filter: “{self.filter_edit.text().strip()}”)")
        else:
            self.count_label.setText(f"{n} layer{'s' if n != 1 else ''} · top row = first in project order")

    def _update_buttons(self) -> None:
        s = self.session
        busy = getattr(self.controller, "busy", False)
        if s is None:
            for b in (self.btn_new, self.btn_duplicate, self.btn_delete, self.btn_up, self.btn_down,
                      self.btn_merge, self.btn_export):
                b.set_local_enabled(False, "open an audio file first")
            return
        n = len(s.project.layers)
        idx = s.project.index_of(s.active_layer_id) if s.active_layer_id and s.project.has_layer(s.active_layer_id) else -1
        busy_reason = "busy with another operation" if busy else ""
        self.btn_new.set_local_enabled(not busy, busy_reason)
        self.btn_duplicate.set_local_enabled(idx >= 0 and not busy, busy_reason or "no active layer")
        active = s.active_layer
        if busy:
            self.btn_delete.set_local_enabled(False, busy_reason)
        elif n <= 1:
            self.btn_delete.set_local_enabled(False, "a project needs at least one layer")
        elif active is not None and active.locked:
            self.btn_delete.set_local_enabled(False, f"layer '{active.name}' is locked")
        else:
            self.btn_delete.set_local_enabled(idx >= 0, "no active layer")
        self.btn_up.set_local_enabled(idx > 0 and not busy, busy_reason or "already the top layer")
        self.btn_down.set_local_enabled(0 <= idx < n - 1 and not busy, busy_reason or "already the bottom layer")
        sel = self.selected_layer_ids()
        locked = [s.project.layer(i).name for i in sel if s.project.layer(i).locked]
        if busy:
            self.btn_merge.set_local_enabled(False, busy_reason)
        elif len(sel) < 2:
            self.btn_merge.set_local_enabled(False, "select at least two layers (Ctrl/Cmd- or Shift-click)")
        elif locked:
            self.btn_merge.set_local_enabled(False, f"layer '{locked[0]}' is locked")
        else:
            self.btn_merge.set_local_enabled(True)
        self.btn_export.set_local_enabled(bool(sel) or idx >= 0, "select a layer")

    # ------------------------------------------------------------ handlers
    def _on_current_changed(self, cur: QListWidgetItem | None, _prev) -> None:
        if self._updating or cur is None or self.session is None:
            return
        lid = cur.data(Qt.ItemDataRole.UserRole)
        if lid != self.session.active_layer_id:
            if self.guard_busy():
                self._sync_current()
                return
            self.controller.safe(self.session.set_active_layer, lid)

    def set_active(self, layer_id: str) -> None:
        it = self.item(layer_id)
        if it is not None:
            self.list.setCurrentItem(it)

    def _resync(self, layer_id: str) -> None:
        s = self.session
        row = self._rows.get(layer_id)
        if s is not None and row is not None and s.project.has_layer(layer_id):
            row.update_from(s.project.layer(layer_id), layer_id == s.active_layer_id)

    def _toggle_prop(self, layer_id: str, prop: str, checked: bool) -> None:
        s = self.session
        if s is None or not s.project.has_layer(layer_id):
            return
        if bool(getattr(s.project.layer(layer_id), prop)) != bool(checked):
            self.call(s.set_layer_props, layer_id, **{prop: bool(checked)})
        self._resync(layer_id)  # reflects the real state if the call was refused

    def _commit_gain(self, layer_id: str, value: float) -> None:
        s = self.session
        if s is None or not s.project.has_layer(layer_id):
            return
        value = round(float(value), 1)
        if abs(s.project.layer(layer_id).gain_db - value) > 1e-9:
            self.call(s.set_layer_props, layer_id, gain_db=value)
        self._resync(layer_id)

    def _rename(self, layer_id: str, name: str) -> None:
        s = self.session
        if s is None or not s.project.has_layer(layer_id):
            return
        if s.project.layer(layer_id).name != name:
            self.call(s.set_layer_props, layer_id, name=name)
        self._resync(layer_id)
        self._refresh_banner()

    def _move(self, delta: int) -> None:
        s = self.session
        if s is None or s.active_layer_id is None:
            return
        idx = s.project.index_of(s.active_layer_id)
        self.call(s.move_layer, s.active_layer_id, idx + delta)

    def merge_selected(self) -> None:
        s = self.session
        ids = self.selected_layer_ids()
        if s is None:
            return
        if len(ids) < 2:
            self.controller.statusMessage.emit("Select at least two layers to merge (Ctrl/Cmd- or Shift-click)", 5000)
            return
        res = self.call(s.merge_layers, ids)
        if res is not None:
            self.controller.statusMessage.emit(
                f"Merged {len(ids)} layers at their current gains (mute/solo ignored). Undo restores them.", 6000)

    def export_selected(self) -> None:
        s = self.session
        if s is None:
            return
        ids = self.selected_layer_ids() or ([s.active_layer_id] if s.active_layer_id else [])
        if ids:
            self.controller.requestExportLayers.emit(list(ids))

    def _on_transfer_target(self, _index: int) -> None:
        self.controller.tools.set(transfer_target=self.transfer_combo.currentData())

    def _on_transfer_amount(self, value: float) -> None:
        amount = max(0.0, min(1.0, float(value) / 100.0))
        if abs(self.controller.tools.transfer_amount - amount) > 1e-12:
            self.controller.tools.set(transfer_amount=amount)


__all__ = ["LayersPanel", "LayerRow"]
