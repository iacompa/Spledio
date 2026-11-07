"""Adjusted Areas dock: every area you turned up or down, with its level.

Select something on the spectrogram and press ↑/↓ (or +/−) to make it louder
or quieter; it becomes an *adjusted area* that stays outlined. This panel
lists them: click one to pick it again (the arrow keys then act on it), type
a level, silence it, or remove its adjustment (the audio there comes back
exactly; other areas are not affected). "Show areas on the spectrogram" (also the Show / Hide
buttons under the spectrogram) shows or hides the outlines.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QEvent, QSize, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from spledio.engine.areas import FLOOR_DB, MAX_BOOST_DB, gain_text
from spledio.ui.theme import ACCENT
from spledio.ui.panels.common import TEXT_DIM, PanelBase, SpinBox, button_grid, esc, note_label

SILENT_SPIN = FLOOR_DB - 1.0  # spin-box value shown as "Silenced"
ID_ROLE = Qt.ItemDataRole.UserRole

HOW_TO = ("Select something on the spectrogram, then press <b>↑ / ↓</b> (or <b>+ / −</b>) to make it 1 dB louder "
          "or quieter – <b>Shift</b> jumps 6 dB, <b>0</b> silences it – or use the <b>− / +</b> bar that appears next "
          "to the selection. Each adjusted area stays outlined: click it later to change it again; <b>Delete</b> "
          "removes the adjustment. <b>[ ]</b> step through areas.")


class _GainSpin(SpinBox):
    def __init__(self):
        super().__init__(SILENT_SPIN, MAX_BOOST_DB, 1.0, 1, " dB", "Level of this area (type a value, or use the arrows)",
                         "Area level")
        self.setSpecialValueText("Silenced")
        self.setAccelerated(False)


class _AreaRow(QWidget):
    def __init__(self, panel: "AreasPanel", area, colour: str, layer_name: str, range_text: str):
        super().__init__()
        self.area_id = area.id
        # two lines so nothing is clipped in a narrow dock:
        #   ● Area 3 · 1.47–4.54 s, 2.31–2.43 kHz · Layer 1
        #     [ -12.0 dB ⇅ ]  Silence  ✕
        v = QVBoxLayout(self)
        v.setContentsMargins(4, 3, 4, 3)
        v.setSpacing(2)
        top = QHBoxLayout()
        top.setSpacing(6)
        self.chip = chip = QLabel()
        chip.setFixedSize(10, 10)
        self.set_colour(colour)
        top.addWidget(chip, 0, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.title = QLabel()
        self.title.setTextFormat(Qt.TextFormat.RichText)
        self.title.setWordWrap(True)
        self.title.setText(f"<b>{esc(area.name)}</b> <span style='color:{TEXT_DIM}'>{esc(range_text)}"
                           f" · {esc(layer_name)}</span>")
        self.title.setToolTip(f"{area.name}: {range_text} on layer '{layer_name}'. Click to pick it.")
        top.addWidget(self.title, 1)
        v.addLayout(top)
        h = QHBoxLayout()
        h.setContentsMargins(16, 0, 0, 0)
        h.setSpacing(6)
        v.addLayout(h)
        self.spin = _GainSpin()
        self.spin.setMinimumWidth(self.spin.fontMetrics().horizontalAdvance("Silenced") + 48)
        self.spin.set_quiet(SILENT_SPIN if math.isinf(area.gain_db) else area.gain_db)
        self.spin.valueChanged.connect(lambda v: panel._on_spin(self.area_id, v))
        h.addWidget(self.spin)
        self.mute = QToolButton()
        self.mute.setText("Silence")
        self.mute.setToolTip("Turn this area all the way down (it can be brought back)")
        self.mute.clicked.connect(lambda: panel.controller.set_area_gain(self.area_id, -math.inf))
        h.addWidget(self.mute)
        self.remove = QToolButton()
        self.remove.setText("✕")
        self.remove.setAccessibleName(f"Remove {area.name}")
        self.remove.setToolTip(f"Remove {area.name}'s adjustment – the audio there comes back exactly")
        self.remove.clicked.connect(lambda: panel.controller.remove_area(self.area_id))
        h.addWidget(self.remove)
        h.addStretch(1)

    def set_colour(self, colour: str) -> None:
        self.chip.setStyleSheet(f"background:{colour}; border-radius:5px;")


class AreasPanel(PanelBase):
    """``AreasPanel(controller, actions, parent=None)``."""

    def __init__(self, controller, actions, parent=None):
        super().__init__(controller, actions, parent, scrollable=False)
        self.setObjectName("AreasPanel")
        self.setWindowTitle("Adjusted Areas")
        lay = self.body_layout
        how = QLabel(HOW_TO)
        how.setWordWrap(True)
        how.setTextFormat(Qt.TextFormat.RichText)
        how.setStyleSheet(f"color: {TEXT_DIM};")
        lay.addWidget(how)

        top = QHBoxLayout()
        self.highlight = QCheckBox("Show areas on the spectrogram")
        self.highlight.setToolTip("Show every adjusted area outlined with its level on the spectrogram "
                                  "(display only). Turn off when there are many.")
        self.highlight.toggled.connect(self._on_highlight)
        top.addWidget(self.highlight)
        top.addStretch(1)
        self.count_label = QLabel("")
        self.count_label.setStyleSheet(f"color: {TEXT_DIM};")
        top.addWidget(self.count_label)
        lay.addLayout(top)

        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setAccessibleName("Adjusted areas")
        # picked area: a tinted row with an accent bar, so the dim range text stays readable
        self.list.setStyleSheet(
            "QListWidget::item { border-left: 3px solid transparent; }"
            f"QListWidget::item:selected {{ background: rgba(79,179,255,0.20); border-left: 3px solid {ACCENT}; }}"
            "QListWidget::item:hover:!selected { background: rgba(255,255,255,0.05); }")
        self.list.itemClicked.connect(self._on_item_clicked)
        self.list.currentItemChanged.connect(self._on_current_changed)
        self.list.viewport().installEventFilter(self)  # re-fit wrapped rows when the dock is resized
        lay.addWidget(self.list, 1)
        self.empty = note_label("No adjusted areas yet.")
        lay.addWidget(self.empty)

        self.btn_quieter = self.action_button("Quieter ↓", "Lower the selection / picked area by 1 dB",
                                              "area.quieter", lambda: controller.adjust_area(-1))
        self.btn_louder = self.action_button("Louder ↑", "Raise the selection / picked area by 1 dB",
                                             "area.louder", lambda: controller.adjust_area(+1))
        self.btn_remove_all = self.action_button("Remove all", "Remove every adjustment (one undo step)",
                                                 "area.remove_all", controller.remove_all_areas)
        self.btn_silence = self.action_button("Silence (0)", "Silence the selection / picked area",
                                              "area.silence", lambda: controller.adjust_area(target_db=-math.inf))
        lay.addLayout(button_grid([self.btn_quieter, self.btn_louder, self.btn_silence, self.btn_remove_all], 2))

        self._ids: tuple = ()
        self._syncing = False
        c = controller
        c.sessionReplaced.connect(self.refresh)
        c.areasChanged.connect(self.refresh)
        c.structureChanged.connect(self.refresh)
        c.propsChanged.connect(self.refresh)
        c.selectionChanged.connect(self._sync_current)
        c.busyChanged.connect(lambda *_: self._update_enabled())
        c.jobEnded.connect(self._update_enabled)
        c.view.changed.connect(lambda _w: self._sync_highlight())
        self.refresh()

    # ------------------------------------------------------------ helpers
    def row_widget(self, area_id: str) -> _AreaRow | None:
        for i in range(self.list.count()):
            w = self.list.itemWidget(self.list.item(i))
            if isinstance(w, _AreaRow) and w.area_id == area_id:
                return w
        return None

    def area_ids(self) -> list[str]:
        return [self.list.item(i).data(ID_ROLE) for i in range(self.list.count())]

    def eventFilter(self, obj, ev):  # noqa: N802 (Qt API)
        if ev.type() == QEvent.Type.Resize and obj is self.list.viewport():
            self._fit_rows()
        return super().eventFilter(obj, ev)

    def _fit_rows(self) -> None:
        width = max(120, self.list.viewport().width())
        for i in range(self.list.count()):
            it = self.list.item(i)
            w = self.list.itemWidget(it)
            if w is None:
                continue
            h = w.heightForWidth(width) if w.hasHeightForWidth() else -1
            it.setSizeHint(QSize(width, h if h > 0 else w.sizeHint().height()))

    def _canvas(self):
        win = self.window()
        ed = getattr(win, "editor", None)
        return getattr(ed, "canvas", None)

    def _range_text(self, a) -> str:
        s = self.session
        b = a.selection.bounds(s.project.duration, s.project.nyquist)
        if b is None:
            return "empty"
        t0, t1, f0, f1 = b

        def hz(f):
            return f"{f / 1000:.2f} kHz" if f >= 1000 else f"{f:.0f} Hz"

        band = "all frequencies" if f0 <= 0 and f1 >= s.project.nyquist else f"{hz(f0)}–{hz(f1)}"
        return f"{t0:.2f}–{t1:.2f} s, {band}"

    # ------------------------------------------------------------ refresh
    def refresh(self) -> None:
        s = self.session
        self.set_session_available(s is not None)
        self._sync_highlight()
        areas = s.visible_areas() if s is not None else []
        ids = tuple(a.id for a in areas)
        self._syncing = True
        try:
            if ids != self._ids:
                from spledio.ui.spectrogram_view import SpectrogramCanvas

                self.list.clear()
                for a in areas:
                    layer = s.project.layer(a.layer_id)
                    it = QListWidgetItem()
                    it.setData(ID_ROLE, a.id)
                    row = _AreaRow(self, a, SpectrogramCanvas.area_colour(a.gain_db).name(), layer.name,
                                   self._range_text(a))
                    self.list.addItem(it)
                    self.list.setItemWidget(it, row)
                self._ids = ids
                self._fit_rows()
            else:
                for a in areas:
                    w = self.row_widget(a.id)
                    if w is not None and not w.spin.hasFocus():
                        w.spin.set_quiet(SILENT_SPIN if math.isinf(a.gain_db) else a.gain_db)
                    if w is not None:
                        from spledio.ui.spectrogram_view import SpectrogramCanvas

                        w.spin.setToolTip(f"{a.name}: {gain_text(a.gain_db)}")
                        w.set_colour(SpectrogramCanvas.area_colour(a.gain_db).name())
            self.empty.setVisible(not areas)
            self.count_label.setText(f"{len(areas)} area{'s' if len(areas) != 1 else ''}" if areas else "")
        finally:
            self._syncing = False
        self._sync_current()
        self._update_enabled()

    def _sync_highlight(self) -> None:
        self.highlight.blockSignals(True)
        self.highlight.setChecked(bool(self.controller.view.show_areas))
        self.highlight.blockSignals(False)

    def _sync_current(self) -> None:
        s = self.session
        cur = s.current_area() if s is not None else None
        self._syncing = True
        try:
            for i in range(self.list.count()):
                it = self.list.item(i)
                if cur is not None and it.data(ID_ROLE) == cur.id:
                    self.list.setCurrentItem(it)
                    break
            else:
                self.list.setCurrentItem(None)
                self.list.clearSelection()
        finally:
            self._syncing = False

    def _update_enabled(self) -> None:
        busy = bool(getattr(self.controller, "busy", False))
        for i in range(self.list.count()):
            w = self.list.itemWidget(self.list.item(i))
            if isinstance(w, _AreaRow):
                for b in (w.spin, w.mute, w.remove):
                    b.setEnabled(not busy)

    # ------------------------------------------------------------ actions
    def _on_highlight(self, on: bool) -> None:
        act = getattr(self.actions, "actions", {}).get("view.show_areas")
        if act is not None:
            act.setChecked(on)  # same handler as the toolbar button / menu item
        else:
            self.controller.view.set_display(show_areas=on)

    def pick(self, area_id: str, focus_canvas: bool = True) -> None:
        self.controller.select_area(area_id)
        cv = self._canvas()
        if focus_canvas and cv is not None:
            cv.setFocus(Qt.FocusReason.OtherFocusReason)  # arrow keys now adjust the picked area

    def _on_item_clicked(self, it: QListWidgetItem) -> None:
        if not self._syncing and it is not None:
            self.pick(it.data(ID_ROLE))

    def _on_current_changed(self, cur: QListWidgetItem | None, _prev) -> None:
        # keyboard navigation in the list picks areas without stealing focus from the list
        if self._syncing or cur is None or not self.list.hasFocus():
            return
        self.pick(cur.data(ID_ROLE), focus_canvas=False)

    def _on_spin(self, area_id: str, v: float) -> None:
        if self._syncing:
            return
        g = -math.inf if v <= SILENT_SPIN + 1e-9 else float(v)
        self.controller.set_area_gain(area_id, g)


__all__ = ["AreasPanel", "HOW_TO"]
