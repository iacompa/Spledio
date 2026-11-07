"""Small shared widgets and helpers for the dock panels (GUI thread only).

* :class:`PanelBase` - scroll area + "Open an audio file to begin" hint +
  disabled state while no session is open, a busy guard and access to the
  registry.
* :class:`ActionButton` - a text tool button that triggers a registry
  ``QAction`` when that id is registered (so menus, shortcuts and the panel
  share one handler) and otherwise calls a direct fallback.
* :class:`SpinBox` / :class:`Combo` - ignore the mouse wheel unless focused
  (so scrolling a dock never changes a value by accident), commit on Enter,
  and can be set without emitting signals.
* :class:`ElidedLabel` - a label that elides long names and keeps the full
  text in its tooltip.
"""

from __future__ import annotations

import html
from typing import Callable

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

try:  # shared palette (falls back to the same values if the theme module moves)
    from spledio.ui.theme import ACCENT, BORDER, DANGER, PANEL_2, TEXT, TEXT_DIM, WARN
except ImportError:  # pragma: no cover - defensive
    ACCENT, BORDER, DANGER, PANEL_2 = "#4fb3ff", "#323a47", "#ff6b6b", "#232833"
    TEXT, TEXT_DIM, WARN = "#dfe4ec", "#9aa4b2", "#f0a050"

NO_SESSION_HINT = "Open an audio file to begin."
BUSY_MESSAGE = "Busy: wait for the current operation to finish (Esc cancels it)"


def esc(text: str) -> str:
    return html.escape(str(text), quote=False)


def panel_icon(name: str) -> QIcon | None:
    """Optional icon from ``spledio.ui.icons``; ``None`` -> text-only button."""
    if not name:
        return None
    try:
        from spledio.ui.icons import icon  # written separately; optional
    except ImportError:
        return None
    try:
        ic = icon(name)
    except Exception:  # noqa: BLE001 - unknown icon id etc.: fall back to text
        return None
    if not isinstance(ic, QIcon) or ic.isNull():
        return None
    return ic


def note_label(text: str, color: str = TEXT_DIM, small: bool = True) -> QLabel:
    lab = QLabel(text)
    lab.setWordWrap(True)
    lab.setTextFormat(Qt.TextFormat.PlainText if "<" not in text else Qt.TextFormat.RichText)
    size = "font-size: 11px;" if small else ""
    lab.setStyleSheet(f"color: {color}; {size}")
    return lab


class ElidedLabel(QLabel):
    """Single-line label that elides on the right; full text in the tooltip."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._full = ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(24)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.set_full_text(text)

    def full_text(self) -> str:
        return self._full

    def set_full_text(self, text: str) -> None:
        self._full = str(text)
        self.setToolTip(self._full)
        self._relayout()

    def resizeEvent(self, ev):  # noqa: N802 - Qt API
        super().resizeEvent(ev)
        self._relayout()

    def _relayout(self) -> None:
        w = max(0, self.width() - 2)
        shown = self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideRight, w) if w > 0 else self._full
        QLabel.setText(self, shown)


class SpinBox(QDoubleSpinBox):
    """Double spin box for dock panels.

    Wheel only when focused; ``keyboardTracking`` off (typing commits on Enter
    / focus loss, arrow steps commit immediately); ``returnPressed`` on Enter.
    """

    returnPressed = Signal()

    def __init__(self, lo: float, hi: float, step: float, decimals: int, suffix: str = "", tooltip: str = "",
                 accessible: str = "", parent=None):
        super().__init__(parent)
        self.setDecimals(decimals)
        self.setRange(lo, hi)
        self.setSingleStep(step)
        if suffix:
            self.setSuffix(suffix)
        self.setKeyboardTracking(False)
        self.setAccelerated(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)  # may shrink in narrow docks
        self.setCorrectionMode(QDoubleSpinBox.CorrectionMode.CorrectToNearestValue)
        if tooltip:
            self.setToolTip(tooltip)
        if accessible:
            self.setAccessibleName(accessible)

    # narrow docks: let layouts shrink spin boxes instead of clipping the panel
    def sizeHint(self):  # noqa: N802 - Qt API
        h = super().sizeHint()
        return QSize(min(h.width(), 104), h.height())

    def minimumSizeHint(self):  # noqa: N802 - Qt API
        h = super().minimumSizeHint()
        return QSize(min(h.width(), 58), h.height())

    def wheelEvent(self, ev):  # noqa: N802 - Qt API
        if not self.hasFocus():
            ev.ignore()
            return
        super().wheelEvent(ev)

    def keyPressEvent(self, ev):  # noqa: N802 - Qt API
        super().keyPressEvent(ev)
        if ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.returnPressed.emit()

    def set_quiet(self, value: float) -> None:
        self.blockSignals(True)
        try:
            self.setValue(float(value))
        finally:
            self.blockSignals(False)

    def is_typing(self) -> bool:
        """True while the field holds typed text that has not been committed yet."""
        return self.text() != self.prefix() + self.textFromValue(self.value()) + self.suffix()

    def sync(self, value: float, force: bool = False) -> None:
        """Show the model value unless the user is mid-typing (never fights typing,
        never leaves a stale value behind after undo even while focused)."""
        if force or not self.is_typing():
            if abs(self.value() - float(value)) > 10.0 ** -(self.decimals() + 2) or self.is_typing():
                self.set_quiet(value)

    def set_range_quiet(self, lo: float, hi: float) -> None:
        self.blockSignals(True)
        try:
            self.setRange(lo, hi)
        finally:
            self.blockSignals(False)


class Combo(QComboBox):
    """Combo box that ignores the wheel unless focused and can be set quietly by item data."""

    def __init__(self, tooltip: str = "", accessible: str = "", parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.setMinimumContentsLength(6)
        if tooltip:
            self.setToolTip(tooltip)
        if accessible:
            self.setAccessibleName(accessible)

    def wheelEvent(self, ev):  # noqa: N802 - Qt API
        if not self.hasFocus():
            ev.ignore()
            return
        super().wheelEvent(ev)

    def set_items(self, items: list[tuple[str, object]], current=None) -> None:
        self.blockSignals(True)
        try:
            self.clear()
            for text, data in items:
                self.addItem(text, data)
            if current is not None or items:
                self._select_data(current)
        finally:
            self.blockSignals(False)

    def set_data_quiet(self, data) -> bool:
        self.blockSignals(True)
        try:
            return self._select_data(data)
        finally:
            self.blockSignals(False)

    def _select_data(self, data) -> bool:
        for i in range(self.count()):
            if self.itemData(i) == data:
                self.setCurrentIndex(i)
                return True
        return False


class ActionButton(QToolButton):
    """Text button bound to a registry action when available.

    When ``actions.actions`` holds ``action_id`` the click triggers that QAction
    (same handler as the menu item and the keyboard shortcut) and the button
    mirrors its enabled state and tooltip (which carries the shortcut and the
    "Unavailable: ..." reason). Otherwise ``fallback`` is called directly.
    ``set_local_enabled`` adds a panel-level condition (e.g. "needs two
    selected layers") on top of the action's own state.
    """

    def __init__(self, text: str, tooltip: str, actions=None, action_id: str = "",
                 fallback: Callable[[], None] | None = None, icon_name: str = "", parent=None):
        super().__init__(parent)
        reg = getattr(actions, "actions", None) or {}
        self.action = reg.get(action_id) if action_id else None
        self.action_id = action_id
        self._fallback = fallback
        self._extra_tip = tooltip
        self._local_ok = True
        self._local_reason = ""
        self.setText(text)
        self.setAccessibleName(text)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        ic = None
        if self.action is not None and not self.action.icon().isNull():
            ic = self.action.icon()
        elif icon_name:
            ic = panel_icon(icon_name)
        if ic is not None:
            self.setIcon(ic)
            self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        else:
            self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.clicked.connect(self._on_clicked)
        if self.action is not None:
            self.action.changed.connect(self._sync)
        self._sync()

    @property
    def bound(self) -> bool:
        return self.action is not None

    def set_local_enabled(self, ok: bool, reason: str = "") -> None:
        self._local_ok = bool(ok)
        self._local_reason = reason if not ok else ""
        self._sync()

    def _sync(self) -> None:
        try:
            act_ok = self.action.isEnabled() if self.action is not None else True
        except RuntimeError:  # action deleted with its window
            self.action = None
            act_ok = True
        self.setEnabled(self._local_ok and act_ok)
        if self.action is not None:
            tip = self.action.toolTip()
            if self._extra_tip:
                tip += "<br>" + esc(self._extra_tip)
        else:
            tip = esc(self._extra_tip)
        if self._local_reason:
            tip += f"<br><span style='color:{WARN}'>Unavailable: {esc(self._local_reason)}</span>"
        self.setToolTip(tip)

    def _on_clicked(self) -> None:
        if not self._local_ok:
            return
        if self.action is not None:
            if self.action.isEnabled():
                self.action.trigger()
        elif self._fallback is not None:
            self._fallback()


def button_grid(buttons: list[QWidget], columns: int = 2) -> QGridLayout:
    grid = QGridLayout()
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setHorizontalSpacing(4)
    grid.setVerticalSpacing(4)
    for i, b in enumerate(buttons):
        grid.addWidget(b, i // columns, i % columns)
    for c in range(columns):
        grid.setColumnStretch(c, 1)
    return grid


class PanelBase(QWidget):
    """Common scaffold: hint while no session, optional scroll area, busy guard."""

    def __init__(self, controller, actions, parent=None, scrollable: bool = True):
        super().__init__(parent)
        self.controller = controller
        self.actions = actions
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.hint = QLabel(NO_SESSION_HINT)
        self.hint.setObjectName("panelHint")
        self.hint.setWordWrap(True)
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint.setStyleSheet(f"color: {TEXT_DIM}; padding: 10px; border-bottom: 1px solid {BORDER};")
        outer.addWidget(self.hint)
        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(6, 6, 6, 6)
        self.body_layout.setSpacing(6)
        if scrollable:
            self.scroll = QScrollArea()
            self.scroll.setWidgetResizable(True)
            self.scroll.setFrameShape(QFrame.Shape.NoFrame)
            self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.scroll.setWidget(self.body)
            outer.addWidget(self.scroll, 1)
        else:
            self.scroll = None
            outer.addWidget(self.body, 1)

    # ------------------------------------------------------------- helpers
    @property
    def session(self):
        return self.controller.session

    def action_button(self, text: str, tooltip: str, action_id: str, fallback: Callable[[], None] | None,
                      icon_name: str = "") -> ActionButton:
        return ActionButton(text, tooltip, self.actions, action_id, fallback, icon_name, parent=self.body)

    def set_session_available(self, ok: bool) -> None:
        self.hint.setVisible(not ok)
        self.body.setEnabled(ok)

    def guard_busy(self) -> bool:
        """True (and a status message) when a background job is running."""
        if getattr(self.controller, "busy", False):
            self.controller.statusMessage.emit(BUSY_MESSAGE, 4000)
            return True
        return False

    def call(self, fn: Callable, *a, **kw):
        """Session call through ``controller.safe`` unless a job is running."""
        if self.session is None or self.guard_busy():
            return None
        return self.controller.safe(fn, *a, **kw)


__all__ = [
    "ACCENT", "BORDER", "DANGER", "PANEL_2", "TEXT", "TEXT_DIM", "WARN",
    "NO_SESSION_HINT", "BUSY_MESSAGE", "esc", "panel_icon", "note_label",
    "ElidedLabel", "SpinBox", "Combo", "ActionButton", "button_grid", "PanelBase",
]
