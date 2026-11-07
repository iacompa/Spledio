"""Selection dock: description, numeric bounds, feathering, expand/contract
steps, combination mode, channel scope, named selections and the
processing-grid resolution hint.

Selections never change audio; they only decide what the next edit acts on.
"""

from __future__ import annotations

import math

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QGridLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
)

from spledio.engine.session import channel_scope_label
from spledio.selections.geometry import INF_F, RectShape
from spledio.selections.selection import REPLACE, Selection
from spledio.ui.actions import ActionRegistry
from spledio.ui.panels.common import (
    DANGER,
    TEXT_DIM,
    WARN,
    Combo,
    PanelBase,
    SpinBox,
    button_grid,
    note_label,
    panel_icon,
)

DEFAULT_EXPAND_STEP_S = 0.010
DEFAULT_EXPAND_STEP_HZ = 50.0

MODE_BUTTONS = (
    # mode, label, modifier hint, action id, tooltip
    ("replace", "Replace", "", "select.mode_replace", "New shapes replace the current selection (plain drag)"),
    ("add", "Add", "Shift", "select.mode_add", "New shapes are added to the selection (or hold Shift while drawing)"),
    ("subtract", "Subtract", "Alt", "select.mode_subtract",
     "New shapes are removed from the selection (or hold Alt / Option while drawing)"),
    ("intersect", "Intersect", "Shift+Alt", "select.mode_intersect",
     "Keep only the overlap with new shapes (or hold Shift+Alt / Shift+Option while drawing)"),
)


def _channel_name(c: int, n: int) -> str:
    if n == 2:
        return ("Left", "Right")[c]
    return f"channel {c + 1}"


def scope_key(scope: tuple[int, ...] | None) -> str:
    """Combo key for a channel scope (string keys survive QVariant round trips)."""
    return "linked" if scope is None else "+".join(f"ch{c}" for c in scope)


def scope_from_key(key: str) -> tuple[int, ...] | None:
    if not key or key == "linked":
        return None
    return tuple(int(part[2:]) for part in key.split("+"))


def single_rect(sel: Selection | None) -> RectShape | None:
    if sel is None or len(sel.ops) != 1:
        return None
    op = sel.ops[0]
    return op.shape if op.mode == REPLACE and isinstance(op.shape, RectShape) else None


class SelectionPanel(PanelBase):
    """Selection dock panel. ``SelectionPanel(controller, actions, parent=None)``."""

    def __init__(self, controller, actions: ActionRegistry, parent=None):
        super().__init__(controller, actions, parent, scrollable=True)
        self.setObjectName("SelectionPanel")
        self.setWindowTitle("Selection")
        tools = controller.tools
        # expand/contract steps used by the select.expand_* / contract_* actions
        if not hasattr(tools, "expand_step_s"):
            setattr(tools, "expand_step_s", DEFAULT_EXPAND_STEP_S)
        if not hasattr(tools, "expand_step_hz"):
            setattr(tools, "expand_step_hz", DEFAULT_EXPAND_STEP_HZ)
        lay = self.body_layout

        # ---- summary
        self.desc_label = QLabel()
        self.desc_label.setWordWrap(True)
        self.desc_label.setTextFormat(Qt.TextFormat.PlainText)
        self.desc_label.setToolTip("What is selected (shape operations in order)")
        lay.addWidget(self.desc_label)
        self.range_label = note_label("")
        self.range_label.setToolTip("Time / frequency extent of the selection (including feathering) and channel scope")
        lay.addWidget(self.range_label)
        self.grid_label = note_label("")
        self.grid_label.setToolTip(
            "The selection is sampled on this time × frequency grid when an edit is processed. "
            "Change it in Display ▸ Processing resolution (affects future edits only).")
        lay.addWidget(self.grid_label)

        # ---- mode + commands
        g_mode = QGroupBox("Mode for new shapes")
        vm = QVBoxLayout(g_mode)
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        self.mode_buttons: dict[str, QToolButton] = {}
        btns = []
        for mode, label, hint, _aid, tip in MODE_BUTTONS:
            b = QToolButton()
            b.setText(label)
            ic = panel_icon(f"mode_{mode}")
            if ic is not None:
                b.setIcon(ic)
                b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            b.setCheckable(True)
            b.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            b.setAccessibleName(f"Selection mode {label}")
            b.setToolTip(tip)
            b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            b.clicked.connect(lambda _=False, m=mode: self.set_mode(m))
            self.mode_group.addButton(b)
            self.mode_buttons[mode] = b
            btns.append(b)
        vm.addLayout(button_grid(btns, 2))
        vm.addWidget(note_label("While drawing: Shift = Add · Alt/Option = Subtract · Shift+Alt = Intersect"))
        s_ = lambda: self.session  # noqa: E731
        self.btn_all = self.action_button("Select all", "Select the whole project (all time, all frequencies)",
                                          "select.all", lambda: self.call(s_().select_all))
        self.btn_none = self.action_button("Deselect", "Clear the selection", "select.none",
                                           lambda: self.call(s_().clear_selection))
        self.btn_reselect = self.action_button("Reselect", "Restore the previous selection", "select.reselect",
                                               lambda: self.call(s_().reselect))
        self.btn_invert = self.action_button("Invert", "Invert the selection over the whole project",
                                             "select.invert", lambda: self.call(s_().invert_selection), "invert")
        vm.addLayout(button_grid([self.btn_all, self.btn_none, self.btn_reselect, self.btn_invert], 2))
        lay.addWidget(g_mode)

        # ---- numeric bounds
        g_b = QGroupBox("Bounds")
        gb = QGridLayout(g_b)
        gb.setHorizontalSpacing(4)
        self.t0 = SpinBox(0.0, 1.0, 0.01, 4, " s", "Selection start time (seconds)", "Selection start time")
        self.t1 = SpinBox(0.0, 1.0, 0.01, 4, " s", "Selection end time (seconds)", "Selection end time")
        self.f0 = SpinBox(0.0, 22050.0, 10.0, 1, " Hz", "Lowest selected frequency (Hz)", "Selection low frequency")
        self.f1 = SpinBox(0.0, 22050.0, 10.0, 1, " Hz",
                          "Highest selected frequency (Hz). At the Nyquist frequency it means “all frequencies above f0”.",
                          "Selection high frequency")
        gb.addWidget(QLabel("Time"), 0, 0)
        gb.addWidget(self.t0, 0, 1)
        gb.addWidget(QLabel("–"), 0, 2)
        gb.addWidget(self.t1, 0, 3)
        gb.addWidget(QLabel("Freq"), 1, 0)
        gb.addWidget(self.f0, 1, 1)
        gb.addWidget(QLabel("–"), 1, 2)
        gb.addWidget(self.f1, 1, 3)
        gb.setColumnStretch(1, 1)
        gb.setColumnStretch(3, 1)
        self.bounds_note = note_label("")
        gb.addWidget(self.bounds_note, 2, 0, 1, 4)
        self.btn_apply = QPushButton("Apply bounds")
        self.btn_apply.setToolTip("Replace the selection with a rectangle of these bounds "
                                  "(feathering and channel scope are kept)")
        self.btn_apply.clicked.connect(self.apply_bounds)
        gb.addWidget(self.btn_apply, 3, 0, 1, 4)
        for sp in (self.t0, self.t1, self.f0, self.f1):
            sp.returnPressed.connect(self.apply_bounds)
        lay.addWidget(g_b)

        # ---- feather
        g_f = QGroupBox("Feather (soft edges)")
        gf = QGridLayout(g_f)
        self.feather_enabled = QCheckBox("Feather enabled")
        self.feather_enabled.setToolTip("Turn the soft edges of the current selection on or off without "
                                        "losing the feather amounts")
        self.feather_enabled.toggled.connect(self._on_feather_changed)
        gf.addWidget(self.feather_enabled, 0, 0, 1, 2)
        self.feather_t = SpinBox(0.0, 2000.0, 1.0, 1, " ms",
                                 "Time feather: raised-cosine fade outside the shape along time. Applies to the "
                                 "current selection and to new selections.", "Feather time")
        self.feather_f = SpinBox(0.0, 10000.0, 5.0, 1, " Hz",
                                 "Frequency feather: raised-cosine fade outside the shape along frequency. Applies "
                                 "to the current selection and to new selections.", "Feather frequency")
        gf.addWidget(QLabel("Time"), 1, 0)
        gf.addWidget(self.feather_t, 1, 1)
        gf.addWidget(QLabel("Frequency"), 2, 0)
        gf.addWidget(self.feather_f, 2, 1)
        gf.setColumnStretch(1, 1)
        self.feather_t.valueChanged.connect(self._on_feather_changed)
        self.feather_f.valueChanged.connect(self._on_feather_changed)
        gf.addWidget(note_label("Changing the feather never moves the selection's shape."), 3, 0, 1, 2)
        lay.addWidget(g_f)

        # ---- expand / contract
        g_e = QGroupBox("Expand / contract")
        ge = QGridLayout(g_e)
        self.step_t = SpinBox(0.1, 5000.0, 1.0, 1, " ms", "Time step for Expand / Contract time", "Expand time step")
        self.step_f = SpinBox(0.1, 10000.0, 5.0, 1, " Hz", "Frequency step for Expand / Contract frequency",
                              "Expand frequency step")
        self.step_t.valueChanged.connect(self._on_steps_changed)
        self.step_f.valueChanged.connect(self._on_steps_changed)
        ge.addWidget(QLabel("Step"), 0, 0)
        ge.addWidget(self.step_t, 0, 1)
        ge.addWidget(self.step_f, 0, 2)
        self.btn_exp_t = self.action_button("Expand time", "Grow the selection in time by the step on each side",
                                            "select.expand_time", lambda: self._expand(+1, 0))
        self.btn_con_t = self.action_button("Contract time", "Shrink the selection in time by the step on each side",
                                            "select.contract_time", lambda: self._expand(-1, 0))
        self.btn_exp_f = self.action_button("Expand freq", "Grow the selection in frequency by the step on each side",
                                            "select.expand_freq", lambda: self._expand(0, +1))
        self.btn_con_f = self.action_button("Contract freq", "Shrink the selection in frequency by the step",
                                            "select.contract_freq", lambda: self._expand(0, -1))
        ge.addLayout(button_grid([self.btn_exp_t, self.btn_con_t, self.btn_exp_f, self.btn_con_f], 2), 1, 0, 1, 3)
        ge.setColumnStretch(1, 1)
        ge.setColumnStretch(2, 1)
        lay.addWidget(g_e)

        # ---- channels
        g_c = QGroupBox("Channels")
        gc = QVBoxLayout(g_c)
        self.scope_combo = Combo("Which channels the next edit changes. Linked keeps the stereo image intact.",
                                 "Channel scope")
        self.scope_combo.activated.connect(self._on_scope)
        gc.addWidget(self.scope_combo)
        self.scope_warning = note_label("", WARN, small=False)
        gc.addWidget(self.scope_warning)
        lay.addWidget(g_c)

        # ---- named selections
        g_n = QGroupBox("Named selections")
        gn = QGridLayout(g_n)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Name for the current selection")
        self.name_edit.setAccessibleName("Named selection name")
        self.name_edit.returnPressed.connect(self.save_named)
        self.btn_save = QPushButton("Save")
        self.btn_save.setToolTip("Store the current selection under this name (saved with the project)")
        self.btn_save.clicked.connect(self.save_named)
        self.named_combo = Combo("Stored selections", "Stored selections")
        self.btn_load = QPushButton("Load")
        self.btn_load.setToolTip("Replace the current selection with the stored one")
        self.btn_load.clicked.connect(self.load_named)
        gn.addWidget(self.name_edit, 0, 0)
        gn.addWidget(self.btn_save, 0, 1)
        gn.addWidget(self.named_combo, 1, 0)
        gn.addWidget(self.btn_load, 1, 1)
        gn.setColumnStretch(0, 1)
        lay.addWidget(g_n)
        lay.addWidget(note_label("Selections never change audio; they decide what the next edit acts on."))
        lay.addStretch(1)

        c = controller
        c.sessionReplaced.connect(self._on_session_replaced)
        c.selectionChanged.connect(self.refresh)
        c.structureChanged.connect(self.refresh)
        c.tools.changed.connect(self._refresh_tools)
        self._on_session_replaced()

    # ------------------------------------------------------------ refresh
    def _on_session_replaced(self) -> None:
        self._refresh_named()
        self._refresh_scope_items()
        self.refresh()

    def refresh(self) -> None:
        s = self.session
        self.set_session_available(s is not None)
        self._refresh_tools()
        if s is None:
            self.desc_label.setText("No project open.")
            self.range_label.setText("")
            self.grid_label.setText("")
            self.bounds_note.setText("")
            return
        sel = s.selection
        if sel is None:
            self.desc_label.setText("Nothing selected. Draw on the spectrogram or enter bounds below.")
            self.range_label.setText(f"Channel scope for new selections: "
                                     f"{channel_scope_label(self.controller.tools.channel_scope, s.project.channels)}")
        else:
            d = sel.describe()
            self.desc_label.setText(d if len(d) <= 240 else d[:237] + "…")
            self.desc_label.setToolTip(d)
            self.range_label.setText(f"{s.selection_label()} · {channel_scope_label(sel.channels, s.project.channels)}")
        cfg = s.edit_config
        sr = s.sample_rate
        self.grid_label.setText(f"Processing grid: {cfg.frame_spacing(sr) * 1000:.1f} ms × "
                                f"{cfg.bin_spacing(sr):.1f} Hz ({cfg.window} {cfg.n_fft}/{cfg.hop})")
        self._refresh_bounds()
        self._refresh_feather()
        self._refresh_named_buttons()

    def _refresh_bounds(self) -> None:
        s = self.session
        dur, nyq = s.project.duration, s.project.nyquist
        for sp in (self.t0, self.t1):
            sp.set_range_quiet(0.0, dur)
        for sp in (self.f0, self.f1):
            sp.set_range_quiet(0.0, nyq)
        sel = s.selection
        rect = single_rect(sel)
        if rect is not None:
            t0, t1 = max(0.0, rect.t0), min(dur, rect.t1)
            f0, f1 = rect.f0, min(nyq, rect.f1)
            self.bounds_note.setText("Rectangle selection." if not (rect.full_band or rect.full_time) else
                                     ("Time range across all frequencies." if rect.full_band and not rect.full_time
                                      else "Frequency band over the whole duration." if rect.full_time
                                      and not rect.full_band else "Everything."))
            self.bounds_note.setStyleSheet(f"color: {TEXT_DIM}; font-size: 11px;")
        elif sel is not None:
            b = sel.with_feather(enabled=False).bounds(dur, nyq)
            t0, t1, f0, f1 = b if b is not None else (0.0, dur, 0.0, nyq)
            self.bounds_note.setText("Bounding box of a complex selection: Apply will replace it with a rectangle.")
            self.bounds_note.setStyleSheet(f"color: {WARN}; font-size: 11px;")
        else:
            v = self.controller.view
            t0, t1, f0, f1 = v.t0, v.t1, v.f_lo, v.f_hi
            self.bounds_note.setText("No selection: Apply creates a rectangle (values show the visible area).")
            self.bounds_note.setStyleSheet(f"color: {TEXT_DIM}; font-size: 11px;")
        self.t0.set_quiet(t0)
        self.t1.set_quiet(t1)
        self.f0.set_quiet(f0)
        self.f1.set_quiet(f1)

    def _refresh_feather(self) -> None:
        s = self.session
        tools = self.controller.tools
        sel = s.selection if s is not None else None
        if sel is not None:
            ft, ff, en = sel.feather_t, sel.feather_f, sel.feather_enabled
        else:
            ft, ff, en = tools.feather_t, tools.feather_f, True
        self.feather_t.sync(ft * 1000.0)
        self.feather_f.sync(ff)
        self.feather_enabled.blockSignals(True)
        self.feather_enabled.setChecked(en)
        self.feather_enabled.blockSignals(False)
        self.feather_enabled.setEnabled(sel is not None)
        self.feather_enabled.setToolTip(
            "Turn the soft edges of the current selection on or off without losing the feather amounts"
            if sel is not None else "Applies to the current selection. New selections use the amounts below "
            "(set them to 0 for hard edges).")

    def _refresh_tools(self) -> None:
        tools = self.controller.tools
        mode = tools.selection_mode
        for m, b in self.mode_buttons.items():
            if b.isChecked() != (m == mode):
                b.blockSignals(True)
                b.setChecked(m == mode)
                b.blockSignals(False)
        self.step_t.sync(float(getattr(tools, "expand_step_s", DEFAULT_EXPAND_STEP_S)) * 1000.0)
        self.step_f.sync(float(getattr(tools, "expand_step_hz", DEFAULT_EXPAND_STEP_HZ)))
        self._refresh_scope_current()
        if self.session is not None and self.session.selection is None:
            self._refresh_feather()

    def _refresh_scope_items(self) -> None:
        s = self.session
        if s is None:
            self.scope_combo.set_items([("All channels (linked)", "linked")])
            return
        n = s.project.channels
        items = [("All channels (linked)", "linked")]
        if n > 1:
            items += [(f"{_channel_name(c, n).capitalize()} only", scope_key((c,))) for c in range(n)]  # "Left only" …
        self.scope_combo.set_items(items)
        self.scope_combo.setEnabled(n > 1)
        self.scope_combo.setToolTip("Which channels the next edit changes. Linked keeps the stereo image intact."
                                    if n > 1 else "Mono audio: edits always affect the single channel.")

    def _refresh_scope_current(self) -> None:
        s = self.session
        scope = self.controller.tools.channel_scope
        n = s.project.channels if s is not None else 1
        if scope is not None and len(scope) >= n:
            scope = None
        if not self.scope_combo.set_data_quiet(scope_key(scope)):
            label = "Custom: " + channel_scope_label(scope, n)
            self.scope_combo.addItem(label, scope_key(scope))
            self.scope_combo.set_data_quiet(scope_key(scope))
        if scope is None or s is None:
            self.scope_warning.setText("")
            self.scope_warning.setVisible(False)
        else:
            if n == 2:
                what = f"the {_channel_name(scope[0], n)} channel"
            else:
                what = ", ".join(_channel_name(c, n) for c in scope)
            self.scope_warning.setText(f"Edits will affect only {what}; this can shift the stereo image.")
            self.scope_warning.setVisible(True)

    def _refresh_named(self) -> None:
        s = self.session
        names = sorted(s.named_selections) if s is not None else []
        cur = self.named_combo.currentData()
        self.named_combo.set_items([(n, n) for n in names] or [("(none saved)", None)], cur)
        self._refresh_named_buttons()

    def _refresh_named_buttons(self) -> None:
        s = self.session
        has_sel = s is not None and s.selection is not None
        self.btn_save.setEnabled(has_sel)
        self.name_edit.setEnabled(has_sel)
        self.btn_save.setToolTip("Store the current selection under this name (saved with the project)"
                                 if has_sel else "Make a selection first")
        has_named = s is not None and bool(s.named_selections)
        self.btn_load.setEnabled(has_named)
        self.named_combo.setEnabled(has_named)

    # ------------------------------------------------------------ handlers
    def set_mode(self, mode: str) -> None:
        aid = next(a for m, _l, _h, a, _t in MODE_BUTTONS if m == mode)
        act = self.actions.actions.get(aid) if self.actions is not None else None
        if act is not None and act.isEnabled():
            if act.isCheckable():
                if not act.isChecked():
                    act.trigger()
            else:
                act.trigger()
        if self.controller.tools.selection_mode != mode:  # no action registered (or it did not set the mode)
            self.controller.tools.set(selection_mode=mode)
        self._refresh_tools()

    def bounds_values(self) -> tuple[float, float, float, float]:
        return self.t0.value(), self.t1.value(), self.f0.value(), self.f1.value()

    def apply_bounds(self) -> None:
        s = self.session
        if s is None or self.guard_busy():
            return
        for sp in (self.t0, self.t1, self.f0, self.f1):
            sp.interpretText()
        t0, t1, f0, f1 = self.bounds_values()
        if t1 - t0 <= 0.0 or f1 - f0 <= 0.0:
            self.bounds_note.setText("End must be after start on both axes; nothing was changed.")
            self.bounds_note.setStyleSheet(f"color: {DANGER}; font-size: 11px;")
            return
        dur, nyq = s.project.duration, s.project.nyquist
        old = single_rect(s.selection)
        if old is not None and old.full_time and t0 <= 0.0 and t1 >= dur - 1e-9:
            t0, t1 = -math.inf, math.inf  # keep "whole duration"
        if f1 >= nyq - 1e-6:
            f1 = INF_F  # up to Nyquist = all frequencies above f0 (exact time-range path when f0 == 0)
        rect = RectShape(t0, t1, max(0.0, f0), f1)
        if s.selection is not None:
            sel = s.selection.combine(REPLACE, rect)  # keeps feather, channels and name
        else:
            tools = self.controller.tools
            sel = Selection.of(rect, feather_t=tools.feather_t, feather_f=tools.feather_f,
                               channels=tools.channel_scope)
        self.controller.safe(s.set_selection, sel)

    def _on_feather_changed(self, *_):
        s = self.session
        if s is None:
            return
        ft = self.feather_t.value() / 1000.0
        ff = self.feather_f.value()
        tools = self.controller.tools
        if abs(tools.feather_t - ft) > 1e-12 or abs(tools.feather_f - ff) > 1e-9:
            tools.set(feather_t=ft, feather_f=ff)
        sel = s.selection
        if sel is not None:
            new = sel.with_feather(ft, ff, enabled=self.feather_enabled.isChecked())
            if new != sel and not self.guard_busy():
                self.controller.safe(s.set_selection, new)

    def _on_steps_changed(self, *_):
        tools = self.controller.tools
        tools.set(expand_step_s=self.step_t.value() / 1000.0, expand_step_hz=self.step_f.value())

    def _expand(self, sign_t: int, sign_f: int) -> None:
        s = self.session
        if s is None:
            return
        if s.selection is None:
            self.controller.statusMessage.emit("Nothing selected to expand or contract", 4000)
            return
        tools = self.controller.tools
        dt = sign_t * float(getattr(tools, "expand_step_s", DEFAULT_EXPAND_STEP_S))
        df = sign_f * float(getattr(tools, "expand_step_hz", DEFAULT_EXPAND_STEP_HZ))
        self.call(s.set_selection, s.selection.expanded(dt=dt, df=df))

    def _on_scope(self, _index: int) -> None:
        if self.session is None or self.guard_busy():
            self._refresh_scope_current()
            return
        self.controller.set_channel_scope(scope_from_key(self.scope_combo.currentData()))
        self._refresh_scope_current()

    def set_scope(self, scope: tuple[int, ...] | None) -> None:
        """Programmatic equivalent of picking an entry in the channel combo."""
        if self.scope_combo.set_data_quiet(scope_key(scope)):
            self._on_scope(self.scope_combo.currentIndex())

    def save_named(self) -> None:
        s = self.session
        if s is None:
            return
        if s.selection is None:
            self.controller.statusMessage.emit("Make a selection first, then save it under a name", 4000)
            return
        name = self.name_edit.text().strip() or f"Selection {len(s.named_selections) + 1}"
        replaced = name in s.named_selections
        if self.guard_busy():
            return
        self.controller.safe(s.save_named_selection, name)
        if name not in s.named_selections:
            return
        self.name_edit.clear()
        self._refresh_named()
        self.named_combo.set_data_quiet(name)
        self.controller.statusMessage.emit(f"{'Replaced' if replaced else 'Saved'} named selection “{name}”", 4000)

    def load_named(self) -> None:
        s = self.session
        name = self.named_combo.currentData()
        if s is None or not name or name not in s.named_selections:
            return
        self.call(s.load_named_selection, name)


__all__ = ["SelectionPanel"]
