"""'Reduce selected sound' module panel (Preview / Bypass / Apply / Cancel).

Every button that has a registry action (``spledio.ui.actions``) triggers that
QAction, so the panel, the menus and the keyboard shortcuts share one
implementation. When an id is not registered (e.g. the panel is used on its
own) the panel falls back to the equivalent controller call. A disabled
button always says *why* in its tooltip (no session, no selection, busy,
locked layer, 0 dB ...).

The module helpers :func:`module_preview`, :func:`module_apply`,
:func:`module_cancel` and :func:`edit_reason` are meant to be used as the
``module.*`` action handlers / ``enabled_when`` callbacks by the main window,
so the semantics are identical everywhere:

* Preview renders on a worker thread, then plays the preview region.
* Apply commits *exactly* the previewed samples as one undo step; with no
  preview it applies the current gain amount directly.
* Cancel discards the preview (or cancels a preview render in progress);
  the audio is untouched.
"""

from __future__ import annotations

import math
import re
import weakref
from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QObject, Qt, Signal, Slot
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractSpinBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from spledio.commands.history import CompositeCommand, PatchCommand
from spledio.engine.session import channel_scope_label
from spledio.ui import icons

GAIN_MIN_DB = -36.0
GAIN_MAX_DB = 36.0
GAIN_STEP_DB = 0.5
DEFAULT_GAIN_DB = -6.0  # moderate; never default to maximum removal
QUICK_GAINS: tuple[tuple[float, str], ...] = (
    (-3.0, "edit.gain_m3"),
    (-6.0, "edit.gain_m6"),
    (-12.0, "edit.gain_m12"),
    (3.0, "edit.gain_p3"),
    (6.0, "edit.gain_p6"),
)

TITLE = "Reduce selected sound"
NO_SESSION_TEXT = "No audio open – open a file first"
NO_SELECTION_TEXT = "No selection – make a selection first (the edit will not silently process the whole file)"
BOOST_WARNING_TEXT = "Boosting may exceed 0 dBFS; you will be warned, nothing is clipped internally"
NOTE_TIMING = "Keep timing unchanged: spectral edits never shorten the recording."
NOTE_OVERLAP = ("The wanted sound and the resonance can overlap in the same time-frequency cells; "
                "reducing one also reduces the other there.")
STATUS_PENDING = "Preview pending…"
STATUS_READY = "Preview ready – not applied"
STATUS_APPLIED = "Applied"
PREVIEW_JOB_LABEL = "Render preview"  # label used by EditorController.preview_gain


# ---------------------------------------------------------------- formatting
def fmt_gain(g: float) -> str:
    if g is None or (isinstance(g, float) and math.isinf(g) and g < 0):
        return "mute"
    if abs(g) < 0.05:
        return "0 dB"
    return f"{'+' if g > 0 else '−'}{abs(g):.1f} dB"


def dash_ranges(text: str) -> str:
    """'1.20-1.85 s' -> '1.20–1.85 s' (en dash between numbers)."""
    return re.sub(r"(?<=\d)-(?=\d)", "–", text)


def clamp_gain(g: float) -> float:
    try:
        g = float(g)
    except (TypeError, ValueError):
        return DEFAULT_GAIN_DB
    if math.isnan(g):
        return DEFAULT_GAIN_DB
    return min(GAIN_MAX_DB, max(GAIN_MIN_DB, g))


# ---------------------------------------------------------------- state queries
def is_audio_edit(cmd) -> bool:
    """True for commands that changed layer samples (patch edits, extracts ...)."""
    if isinstance(cmd, PatchCommand):
        return True
    if isinstance(cmd, CompositeCommand):
        return any(is_audio_edit(c) for c in cmd.children)
    return False


def has_audio_edit(session) -> bool:
    return session is not None and any(is_audio_edit(c) for c in session.history.undo_stack)


def scope_text(controller) -> str:
    """'Acts on: Layer 'X' · 1.20–1.85 s, 910–1060 Hz · L+R linked' (or the no-selection notice)."""
    s = controller.session
    if s is None:
        return NO_SESSION_TEXT
    if s.selection is None:
        return NO_SELECTION_TEXT
    layer = s.active_layer
    name = layer.name if layer is not None else "(none)"
    scope = channel_scope_label(controller.tools.channel_scope, s.project.channels)
    text = f"Acts on: Layer '{name}' · {dash_ranges(s.selection_label())} · {scope}"
    if layer is not None and layer.locked:
        text += " · layer is locked"
    return text


def edit_reason(controller, kind: str) -> str | None:
    """Why an edit-panel command is unavailable right now (``None`` = available).

    kind: 'preview' | 'apply' | 'quick' | 'cancel' | 'bypass' | 'edit' (mute/delete/extract) |
    'copy' | 'audition' (needs a selection) | 'compare' (needs a preview or an applied edit) | 'reset'.
    """
    s = controller.session
    if kind == "reset":
        return "The amount is already 0 dB" if abs(controller.tools.gain_db) < 1e-9 else None
    if s is None:
        return "Open an audio file first"
    if kind == "cancel":
        if controller.busy and _busy_label(controller) == PREVIEW_JOB_LABEL:
            return None  # cancels the render in progress
        if controller.busy:
            return "Busy – wait for the current operation to finish"
        return None if s.preview is not None else "There is no preview to cancel"
    if controller.busy:
        return "Busy – wait for the current operation to finish (Esc cancels long renders)"
    if kind == "compare":
        if s.preview is None and not has_audio_edit(s):
            return "Preview or apply an edit first – there is nothing to compare yet"
        return None
    if kind == "bypass":
        return None if s.preview is not None else "Press Preview first – Bypass switches between the original and the preview"
    if kind == "apply" and s.preview is not None:
        return None  # commits the rendered preview
    if s.selection is None:
        return "Make a selection first – the edit will not silently process the whole file"
    layer = s.active_layer
    if layer is None:
        return "There is no layer to work on"
    if kind == "audition":
        return None
    if kind in ("preview", "apply", "quick", "edit") and layer.locked:
        return f"Layer '{layer.name}' is locked – unlock it in the Layers panel to edit it"
    if kind in ("preview", "apply") and abs(controller.tools.gain_db) < 1e-9:
        return "0 dB changes nothing – set a gain amount first"
    return None


_BUSY_LABELS: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def track_busy_label(controller) -> None:
    """Remember the label of the controller's running job (the controller only emits it).

    Idempotent; called by the panels. ``busy_label(controller)`` then tells a
    preview render apart from other jobs (Cancel can stop a pending preview)."""
    if controller in _BUSY_LABELS:
        return
    _BUSY_LABELS[controller] = ""

    def on_busy(busy: bool, label: str, ref=weakref.ref(controller)) -> None:
        c = ref()
        if c is not None:
            _BUSY_LABELS[c] = label if busy else ""

    controller.busyChanged.connect(on_busy)


def busy_label(controller) -> str:
    try:
        return _BUSY_LABELS.get(controller, "") if controller.busy else ""
    except TypeError:
        return ""


_busy_label = busy_label


# ---------------------------------------------------------------- module handlers
def module_preview(controller, gain_db: float | None = None, then_play: bool = True) -> None:
    """Render a preview of the gain module at ``gain_db`` (default: the current amount), then play it."""
    if controller.session is None:
        return
    g = controller.tools.gain_db if gain_db is None else float(gain_db)
    if abs(g) < 1e-9:
        controller.statusMessage.emit("0 dB changes nothing – set a gain amount first", 4000)
        return
    controller.preview_gain(g, then_play=then_play)


def module_apply(controller) -> None:
    """Commit the pending preview exactly (one undo step); without a preview apply the current amount."""
    s = controller.session
    if s is None:
        return
    if s.preview is not None:
        controller.commit_preview()
        return
    g = controller.tools.gain_db
    if abs(g) < 1e-9:
        controller.statusMessage.emit("0 dB changes nothing – set a gain amount first", 4000)
        return
    controller.apply_gain(g)


def module_cancel(controller) -> None:
    """Discard the preview (audio unchanged); also cancels a preview render that is still running."""
    if controller.busy and _busy_label(controller) == PREVIEW_JOB_LABEL:
        controller.cancel_job()
    controller.cancel_preview()


# ---------------------------------------------------------------- action binding
ActionId = str | Callable[[], str]


@dataclass
class _Binding:
    button: QAbstractButton
    action_id: ActionId  # fixed id, or a callable choosing the id at click time
    label: str
    fallback: Callable[[], None] | dict[str, Callable[[], None]] | None
    reason: Callable[[], str | None] | None
    tooltip: str
    before: Callable[[], None] | None
    after: Callable[[], None] | None
    activate: bool  # checkable action: ensure it is ON (tools) instead of toggling it

    def current_id(self) -> str:
        return self.action_id() if callable(self.action_id) else self.action_id

    def current_fallback(self) -> Callable[[], None] | None:
        if isinstance(self.fallback, dict):
            return self.fallback.get(self.current_id())
        return self.fallback


class ActionBinder(QObject):
    """Binds push buttons to registry actions with a fallback and live 'why disabled' tooltips.

    * If the action id is registered, clicking triggers that QAction, so the
      button, the menu item and the shortcut run one implementation.
    * Otherwise the ``fallback`` callable runs (same controller call); with no
      fallback the button is disabled and says so.
    * The button keeps its own (panel-specific) text; its enabled state and
      tooltip combine the panel's reason, the action spec's ``enabled_when``
      and the action's shortcut.
    """

    refused = Signal(str)  # reason text when a click could not run

    NOT_AVAILABLE = "This command is not available in this window"

    def __init__(self, actions, parent: QObject | None = None):
        super().__init__(parent)
        self.actions = actions
        self._bindings: dict[int, _Binding] = {}
        self._watchers: dict[str, list[QAbstractButton]] = {}
        self._connected: set[str] = set()

    # -- registry access ------------------------------------------------------
    def action(self, aid: str) -> QAction | None:
        acts = getattr(self.actions, "actions", None)
        return acts.get(aid) if acts else None

    def spec(self, aid: str):
        specs = getattr(self.actions, "specs", None)
        return specs.get(aid) if specs else None

    def spec_reason(self, aid: str) -> str | None:
        spec = self.spec(aid)
        if spec is None or spec.enabled_when is None:
            return None
        try:
            return spec.enabled_when()
        except Exception as e:  # noqa: BLE001 - a broken predicate must not break the panel
            return f"unavailable ({e})"

    def shortcut_text(self, aid: str) -> str:
        act = self.action(aid)
        if act is None:
            return ""
        return " / ".join(s.toString(QKeySequence.NativeText) for s in act.shortcuts() if not s.isEmpty())

    # -- binding ----------------------------------------------------------------
    def bind(self, button: QAbstractButton, action_id: ActionId,
             fallback: Callable[[], None] | dict[str, Callable[[], None]] | None = None,
             reason: Callable[[], str | None] | None = None, tooltip: str = "",
             before: Callable[[], None] | None = None, after: Callable[[], None] | None = None,
             activate: bool = False, watch: tuple[str, ...] = ()) -> QAbstractButton:
        """Bind ``button``; ``watch`` lists extra action ids whose changes refresh it (dynamic ids)."""
        b = _Binding(button, action_id, button.text().replace("&", ""), fallback, reason, tooltip, before, after,
                     activate)
        self._bindings[id(button)] = b
        button.clicked.connect(lambda _=False, btn=button: self.trigger(btn))
        ids = set(watch)
        if not callable(action_id):
            ids.add(action_id)
        if isinstance(fallback, dict):
            ids.update(fallback)
        for aid in ids:
            self._watchers.setdefault(aid, []).append(button)
            act = self.action(aid)
            if act is not None and aid not in self._connected:
                # bound-method slot: Qt disconnects it automatically when the binder is destroyed
                act.changed.connect(self._on_action_changed)
                self._connected.add(aid)
        self.update(button)
        return button

    @Slot()
    def _on_action_changed(self) -> None:
        act = self.sender()
        aid = act.data() if isinstance(act, QAction) else None
        for btn in self._watchers.get(aid, ()) if aid else ():
            self.update(btn)

    def binding(self, button: QAbstractButton) -> _Binding:
        return self._bindings[id(button)]

    def action_id_for(self, button: QAbstractButton) -> str:
        return self._bindings[id(button)].current_id()

    def reason(self, button: QAbstractButton) -> str | None:
        b = self._bindings[id(button)]
        if b.reason is not None:
            r = b.reason()
            if r:
                return r
        aid = b.current_id()
        if self.action(aid) is None:
            return None if b.current_fallback() is not None else self.NOT_AVAILABLE
        return self.spec_reason(aid)

    def update(self, button: QAbstractButton) -> None:
        b = self._bindings.get(id(button))
        if b is None:
            return
        r = self.reason(button)
        button.setEnabled(r is None)
        sc = self.shortcut_text(b.current_id())
        head = f"<b>{b.label}</b>" + (f" &nbsp;<span style='color:#9aa4b2'>({sc})</span>" if sc else "")
        lines = [head]
        if b.tooltip:
            lines.append(b.tooltip)
        if r:
            lines.append(f"<span style='color:#f0a050'>Unavailable: {r}</span>")
        button.setToolTip("<br>".join(lines))
        button.setStatusTip(b.tooltip if r is None else f"{b.label} unavailable: {r}")
        button.setAccessibleDescription(b.tooltip if r is None else f"Unavailable: {r}")

    def refresh(self) -> None:
        for b in list(self._bindings.values()):
            self.update(b.button)

    def run_action(self, aid: str, activate: bool = False) -> bool:
        """Trigger registry action ``aid`` (even if the registry has not refreshed its enabled state yet)."""
        act = self.action(aid)
        if act is None:
            return False
        spec = self.spec(aid)
        checkable = act.isCheckable()
        if checkable and activate:
            if not act.isChecked():
                act.setChecked(True)  # toggled(True) -> registry handler
            elif spec is not None:
                spec.handler(True)  # already on: re-run so the state is certainly applied
        elif act.isEnabled():
            act.trigger()
        elif spec is not None:
            # enabled_when says it is available but the QAction was not refreshed yet:
            # run the very handler the QAction runs.
            if checkable:
                act.toggle()
            else:
                spec.handler()
        return True

    def trigger(self, button: QAbstractButton) -> bool:
        """Run the bound command (registry action if present, else the fallback)."""
        b = self._bindings.get(id(button))
        if b is None:
            return False
        if b.before is not None:
            b.before()
        r = self.reason(button)
        if r:
            self.refused.emit(r)
            self.update(button)
            return False
        aid = b.current_id()
        if not self.run_action(aid, b.activate):
            fb = b.current_fallback()
            if fb is not None:
                fb()
        if b.after is not None:
            b.after()
        self.update(button)
        return True


def make_button(text: str, icon_name: str = "", object_name: str = "") -> QPushButton:
    btn = QPushButton(text)
    if icon_name:
        ic = icons.icon(icon_name)
        if ic is not None:
            btn.setIcon(ic)
    if object_name:
        btn.setObjectName(object_name)
    btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    return btn


def note_label(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setWordWrap(True)
    lab.setStyleSheet("color: #8a94a3; font-size: 11px;")
    return lab


# ---------------------------------------------------------------- the panel
class EditPanel(QWidget):
    """The 'Reduce selected sound' module."""

    def __init__(self, controller, actions, parent=None):
        super().__init__(parent)
        self.setObjectName("EditPanel")
        self.controller = controller
        self.actions = actions
        track_busy_label(controller)  # connected before our own busy slot, so the label is current there
        self.binder = ActionBinder(actions, self)
        self.binder.refused.connect(self._on_refused)
        self._syncing = False
        self._applied_label: str | None = None
        self._note = ""
        self._last_top = None
        self._last_len = 0
        self._build()
        self._connect()
        self._remember_history()
        self._sync_gain_from_tools()
        self.refresh()

    # ------------------------------------------------------------------ UI
    def _build(self) -> None:
        c = self.controller
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        self.title_label = QLabel(TITLE)
        self.title_label.setObjectName("panelTitle")
        f = self.title_label.font()
        f.setBold(True)
        f.setPointSizeF(f.pointSizeF() * 1.15 if f.pointSizeF() > 0 else 13)
        self.title_label.setFont(f)
        root.addWidget(self.title_label)

        self.scope_label = QLabel()
        self.scope_label.setObjectName("scopeLabel")
        self.scope_label.setWordWrap(True)
        self.scope_label.setFrameShape(QFrame.StyledPanel)
        self.scope_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.scope_label.setStyleSheet("QLabel#scopeLabel { padding: 5px; border: 1px solid #323a47; border-radius: 4px; }")
        root.addWidget(self.scope_label)

        # ---- amount
        amount = QGroupBox("Amount")
        al = QVBoxLayout(amount)
        row = QHBoxLayout()
        self.gain_slider = QSlider(Qt.Horizontal)
        self.gain_slider.setObjectName("gainSlider")
        self.gain_slider.setRange(int(GAIN_MIN_DB / GAIN_STEP_DB), int(GAIN_MAX_DB / GAIN_STEP_DB))
        self.gain_slider.setSingleStep(1)  # 0.5 dB
        self.gain_slider.setPageStep(6)  # 3 dB
        self.gain_slider.setTickPosition(QSlider.TicksBelow)
        self.gain_slider.setTickInterval(12)  # 6 dB
        self.gain_slider.setMinimumWidth(80)
        self.gain_slider.setAccessibleName("Gain amount slider (dB)")
        self.gain_slider.setToolTip("Gain applied to the selected content: −36 … +36 dB in 0.5 dB steps. "
                                    "Negative values reduce, positive values boost.")
        self.gain_spin = QDoubleSpinBox()
        self.gain_spin.setObjectName("gainSpin")
        self.gain_spin.setRange(GAIN_MIN_DB, GAIN_MAX_DB)
        self.gain_spin.setDecimals(1)
        self.gain_spin.setSingleStep(GAIN_STEP_DB)
        self.gain_spin.setSuffix(" dB")
        self.gain_spin.setKeyboardTracking(False)
        self.gain_spin.setCorrectionMode(QAbstractSpinBox.CorrectToNearestValue)  # typed −48 -> −36
        self.gain_spin.setAccelerated(True)
        self.gain_spin.setMinimumWidth(82)
        self.gain_spin.setAccessibleName("Gain amount (dB)")
        self.gain_spin.setToolTip("Type an exact amount (−36 … +36 dB). Values outside the range are limited to it.")
        row.addWidget(self.gain_slider, 1)
        row.addWidget(self.gain_spin)
        al.addLayout(row)

        quick = QHBoxLayout()
        quick.setSpacing(4)
        self.quick_buttons: dict[float, QPushButton] = {}
        for g, aid in QUICK_GAINS:
            btn = make_button(fmt_gain(g).replace(".0 dB", ""), "", f"quick_{aid.split('.')[-1]}")
            btn.setMinimumWidth(30)
            btn.setStyleSheet("padding: 3px 2px;")
            self.binder.bind(
                btn, aid, fallback=lambda g=g: self.controller.apply_gain(g),
                reason=lambda: edit_reason(self.controller, "quick"),
                tooltip=f"Applies {fmt_gain(g)} to the selection immediately – no preview (one undo step)",
            )
            self.quick_buttons[g] = btn
            quick.addWidget(btn)
        al.addLayout(quick)
        self.reset_button = make_button("Reset to 0 dB", "", "gainReset")
        self.binder.bind(self.reset_button, "edit.gain_reset", fallback=lambda: self.controller.tools.set(gain_db=0.0),
                         reason=lambda: edit_reason(self.controller, "reset"),
                         tooltip="Set the amount back to 0 dB. Changes nothing until you preview or apply.")
        al.addWidget(self.reset_button)
        self.boost_label = QLabel(BOOST_WARNING_TEXT)
        self.boost_label.setObjectName("boostWarning")
        self.boost_label.setWordWrap(True)
        self.boost_label.setStyleSheet("color: #f0a050;")
        al.addWidget(self.boost_label)
        root.addWidget(amount)

        # ---- preview / bypass / apply / cancel
        mod = QGroupBox("Preview, then apply")
        ml = QVBoxLayout(mod)
        grid = QGridLayout()
        grid.setSpacing(4)
        self.preview_button = make_button("Preview", "preview", "previewButton")
        self.bypass_button = make_button("Bypass", "bypass", "bypassButton")
        self.bypass_button.setCheckable(True)
        self.apply_button = make_button("Apply", "apply", "applyButton")
        self.cancel_button = make_button("Cancel", "cancel", "cancelButton")
        self.binder.bind(self.preview_button, "module.preview", fallback=lambda: module_preview(self.controller),
                         reason=lambda: edit_reason(self.controller, "preview"), before=self._commit_typed_value,
                         tooltip="Render the change at the current amount without applying it, then play the "
                                 "selected region. Nothing changes until you press Apply.")
        self.binder.bind(self.bypass_button, "module.bypass", fallback=self.controller.toggle_bypass,
                         reason=lambda: edit_reason(self.controller, "bypass"), after=self._sync_bypass,
                         tooltip="Switch between the original and the preview; while playing, the position is kept.")
        # Apply: with a preview -> module.apply (commits exactly the previewed samples);
        # without one -> edit.apply_gain (applies the current amount). Both are one undo step.
        self.binder.bind(self.apply_button, self._apply_action_id, fallback=lambda: module_apply(self.controller),
                         reason=lambda: edit_reason(self.controller, "apply"), before=self._commit_typed_value,
                         watch=("module.apply", "edit.apply_gain"),
                         tooltip="Commit exactly the previewed result as one undo step. Without a preview, "
                                 "applies the current amount directly.")
        # Cancel: discards the preview; while a preview is still rendering it cancels that render.
        self.binder.bind(self.cancel_button, self._cancel_action_id, fallback=lambda: module_cancel(self.controller),
                         reason=lambda: edit_reason(self.controller, "cancel"),
                         watch=("module.cancel", "help.cancel_job"),
                         tooltip="Discard the preview (or stop a preview that is still rendering). "
                                 "The audio stays exactly as it was.")
        grid.addWidget(self.preview_button, 0, 0)
        grid.addWidget(self.bypass_button, 0, 1)
        grid.addWidget(self.apply_button, 1, 0)
        grid.addWidget(self.cancel_button, 1, 1)
        ml.addLayout(grid)
        self.status_label = QLabel()
        self.status_label.setObjectName("previewStatus")
        self.status_label.setWordWrap(True)
        ml.addWidget(self.status_label)
        self.monitor_label = QLabel()
        self.monitor_label.setObjectName("monitorLabel")
        self.monitor_label.setWordWrap(True)
        self.monitor_label.setStyleSheet("color: #4fb3ff;")
        ml.addWidget(self.monitor_label)
        root.addWidget(mod)

        # ---- other edits
        other = QGroupBox("Other edits on the selection")
        ol = QGridLayout(other)
        ol.setSpacing(4)
        self.mute_button = make_button("Mute region", "mute", "muteButton")
        self.delete_button = make_button("Delete spectral content", "delete", "deleteButton")
        self.extract_button = make_button("Extract to new layer", "extract", "extractButton")
        self.copy_button = make_button("Copy to new layer", "copy_layer", "copyButton")
        self.binder.bind(self.mute_button, "edit.mute", fallback=self.controller.mute_selection,
                         reason=lambda: edit_reason(self.controller, "edit"),
                         tooltip="Silence the selected content (one undo step). The recording keeps its full length.")
        self.binder.bind(self.delete_button, "edit.delete_spectral", fallback=self.controller.delete_spectral,
                         reason=lambda: edit_reason(self.controller, "edit"),
                         tooltip="Removes the selected content; the recording keeps its full length")
        self.binder.bind(self.extract_button, "edit.extract", fallback=lambda: self.controller.extract(copy=False),
                         reason=lambda: edit_reason(self.controller, "edit"),
                         tooltip="Move the selected content to a new time-aligned layer. The original keeps the "
                                 "remainder, so together they sound exactly as before.")
        self.binder.bind(self.copy_button, "edit.copy_to_layer", fallback=lambda: self.controller.extract(copy=True),
                         reason=lambda: edit_reason(self.controller, "copy"),
                         tooltip="Copy the selected content to a new layer and keep the original. Warning: both "
                                 "are summed, so the level can rise (up to +6 dB where they overlap).")
        for i, b in enumerate((self.mute_button, self.delete_button, self.extract_button, self.copy_button)):
            ol.addWidget(b, i, 0)
        root.addWidget(other)

        # ---- audition
        listen = QGroupBox("Listen")
        ll = QVBoxLayout(listen)
        ll.setSpacing(4)
        specs = (
            ("selected", "Hear selected frequencies", "audition.selected", "hear_selected", "audition",
             lambda: c.audition_mode("selected"),
             "Play only the selected content of the active layer (exactly what an edit would act on)."),
            ("all_freqs", "Hear all frequencies (time range)", "audition.all_freqs", "hear_all", "audition",
             lambda: c.audition_mode("all_freqs"),
             "Play the full mix over the selection's time range, all frequencies."),
            ("remainder", "Hear remainder", "audition.remainder", "hear_remainder", "audition",
             lambda: c.audition_mode("remainder"),
             "Play the active layer over the selection's span with the selected content removed."),
            ("removed", "Hear what is being removed", "audition.removed", "hear_removed", "compare",
             lambda: c.audition_mode("difference"),
             "Play original minus processed for the preview (or the last applied edit)."),
            ("ab", "A/B compare", "audition.ab", "ab", "compare", c.ab_toggle,
             "Toggle A (original) / B (processed) for the preview or the last edit; the position is kept."),
        )
        self.audition_buttons: dict[str, QPushButton] = {}
        for key, text, aid, ic, kind, fb, tip in specs:
            btn = make_button(text, ic, f"audition_{key}")
            self.binder.bind(btn, aid, fallback=fb, reason=lambda kind=kind: edit_reason(self.controller, kind),
                             tooltip=tip)
            self.audition_buttons[key] = btn
            ll.addWidget(btn)
        root.addWidget(listen)

        self.note_timing = note_label(NOTE_TIMING)
        self.note_overlap = note_label(NOTE_OVERLAP)
        root.addWidget(self.note_timing)
        root.addWidget(self.note_overlap)
        root.addStretch(1)

    def _connect(self) -> None:
        c = self.controller
        self.gain_slider.valueChanged.connect(self._on_slider)
        self.gain_spin.valueChanged.connect(self._on_spin)
        c.tools.changed.connect(self._on_tools_changed)
        c.sessionReplaced.connect(self._on_session_replaced)
        c.historyChanged.connect(self._on_history)
        c.previewChanged.connect(self._on_preview_changed)
        c.selectionChanged.connect(self._on_selection_changed)
        c.busyChanged.connect(self._on_busy)
        c.warning.connect(self._on_warning)
        for sig in (c.structureChanged, c.propsChanged, c.playbackChanged):
            sig.connect(self.refresh)

    # ------------------------------------------------------------------ gain binding
    def _on_slider(self, v: int) -> None:
        if not self._syncing:
            self._set_gain(v * GAIN_STEP_DB)

    def _on_spin(self, v: float) -> None:
        if not self._syncing:
            self._set_gain(v)

    def _set_gain(self, g: float) -> None:
        g = clamp_gain(g)
        if g != self.controller.tools.gain_db:
            self.controller.tools.set(gain_db=g)  # -> tools.changed -> _sync_gain_from_tools
        else:
            self._sync_gain_from_tools()

    def set_gain_db(self, g: float) -> None:
        """Programmatic setter (validated, clamped to −36 … +36 dB)."""
        self._set_gain(g)

    def gain_db(self) -> float:
        return self.controller.tools.gain_db

    def _sync_gain_from_tools(self) -> None:
        g = clamp_gain(self.controller.tools.gain_db)
        self._syncing = True
        try:
            if abs(self.gain_spin.value() - g) > 1e-9:
                self.gain_spin.setValue(g)
            sv = int(round(g / GAIN_STEP_DB))
            if self.gain_slider.value() != sv:
                self.gain_slider.setValue(sv)
        finally:
            self._syncing = False

    def _commit_typed_value(self) -> None:
        """Take over a value still being typed into the spin box before Preview/Apply."""
        if self.gain_spin.hasFocus():
            self.gain_spin.interpretText()

    def _on_tools_changed(self) -> None:
        self._sync_gain_from_tools()
        self.refresh()

    # ------------------------------------------------------------------ controller events
    def _remember_history(self) -> None:
        s = self.controller.session
        stack = s.history.undo_stack if s is not None else []
        self._last_top = stack[-1] if stack else None
        self._last_len = len(stack)

    def _on_session_replaced(self) -> None:
        self._applied_label = None
        self._note = ""
        self._remember_history()
        self.refresh()

    def _on_history(self) -> None:
        s = self.controller.session
        if s is not None:
            stack = s.history.undo_stack
            top = stack[-1] if stack else None
            n = len(stack)
            if top is not None and top is not self._last_top and n >= self._last_len:
                if is_audio_edit(top):
                    self._applied_label = top.label
                    self._note = ""
            elif n < self._last_len:
                self._applied_label = None
                self._note = "Undone – the audio is back to how it was before that edit"
            self._last_top, self._last_len = top, n
        self.refresh()

    def _on_preview_changed(self) -> None:
        s = self.controller.session
        if s is not None and s.preview is not None:
            self._applied_label = None
            self._note = ""
        elif s is not None:
            self._note = "Preview discarded – the audio is unchanged"
        self.refresh()

    def _on_selection_changed(self) -> None:
        self._applied_label = None
        self.refresh()

    def _on_busy(self, busy: bool, label: str) -> None:
        self.refresh()

    def _on_warning(self, text: str) -> None:
        self._note = text
        self.refresh()

    def _on_refused(self, reason: str) -> None:
        self._note = reason
        self._update_status()

    def _apply_action_id(self) -> str:
        s = self.controller.session
        return "module.apply" if s is not None and s.preview is not None else "edit.apply_gain"

    def _cancel_action_id(self) -> str:
        c = self.controller
        return "help.cancel_job" if c.busy and busy_label(c) == PREVIEW_JOB_LABEL else "module.cancel"

    def _sync_bypass(self) -> None:
        want = self.controller.audition.mode == "bypass"
        if self.bypass_button.isChecked() != want:
            self.bypass_button.blockSignals(True)
            self.bypass_button.setChecked(want)
            self.bypass_button.blockSignals(False)

    # ------------------------------------------------------------------ refresh
    def status_text(self) -> str:
        c = self.controller
        s = c.session
        if s is None:
            return NO_SESSION_TEXT
        if c.busy:
            lab = _busy_label(c)
            if lab == PREVIEW_JOB_LABEL:
                return STATUS_PENDING
            return f"Working: {lab or 'processing'}…"
        pv = s.preview
        if pv is not None:
            text = f"{STATUS_READY} ({dash_ranges(pv.label)})"
            if abs(pv.gain_db - c.tools.gain_db) > 1e-9:
                text += (f". The amount is now {fmt_gain(c.tools.gain_db)}: press Preview again to hear it – "
                         f"Apply would commit the previewed {fmt_gain(pv.gain_db)}.")
            return text
        if self._applied_label:
            return f"{STATUS_APPLIED} – {dash_ranges(self._applied_label)} (Undo reverts it in one step)"
        if self._note:
            return self._note
        return "No preview yet – Preview lets you hear the change before anything is applied"

    def _update_status(self) -> None:
        self.status_label.setText(self.status_text())
        c = self.controller
        mode = c.audition.mode
        playing = bool(getattr(c.playback, "is_playing", False))
        if playing and mode in ("preview", "bypass", "ab_a", "ab_b", "selected", "all_freqs", "remainder", "difference"):
            self.monitor_label.setText(f"Hearing: {c.audition.label}")
            self.monitor_label.show()
        else:
            self.monitor_label.clear()
            self.monitor_label.hide()

    def refresh(self) -> None:
        c = self.controller
        self.scope_label.setText(scope_text(c))
        self.boost_label.setVisible(c.tools.gain_db > 0)
        no_session = c.session is None
        for w in (self.gain_slider, self.gain_spin):
            w.setEnabled(not no_session)
            if no_session:
                w.setToolTip("Unavailable: open an audio file first")
        self._sync_bypass()
        self.binder.refresh()
        self._update_status()


__all__ = [
    "EditPanel",
    "ActionBinder",
    "module_preview",
    "module_apply",
    "module_cancel",
    "edit_reason",
    "scope_text",
    "fmt_gain",
    "has_audio_edit",
    "track_busy_label",
    "busy_label",
    "is_audio_edit",
    "GAIN_MIN_DB",
    "GAIN_MAX_DB",
    "GAIN_STEP_DB",
    "DEFAULT_GAIN_DB",
    "NO_SELECTION_TEXT",
    "BOOST_WARNING_TEXT",
]
