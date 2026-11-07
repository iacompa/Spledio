"""The bar under the spectrogram: zoom buttons and Show / Hide for the adjusted areas.

Everything here is display only and reuses the window's registered actions (``view.zoom_in``,
``view.show_areas`` …), so a button does exactly what its menu item and keyboard shortcut do and
is enabled when they are. Zoom is also on ⌘/Ctrl + scroll, Shift + scroll (frequency) and a
trackpad pinch over the spectrogram.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QButtonGroup, QFrame, QHBoxLayout, QLabel, QSizePolicy, QToolButton, QWidget

from spledio.ui.theme import ACCENT, BORDER, PANEL, PANEL_2, TEXT, TEXT_DIM

HEIGHT = 34

# action id, button text, accessible name, tooltip
ZOOM_BUTTONS = (
    (None, "Zoom", None, None),
    ("view.zoom_out", "−", "Zoom out in time",
     "Zoom out: see more time (⌘− · or ⌘ + scroll, or pinch on the trackpad)"),
    ("view.zoom_in", "+", "Zoom in in time",
     "Zoom in: see less time in more detail (⌘= · or ⌘ + scroll, or pinch on the trackpad)"),
    (None, "Frequency", None, None),
    ("view.zoom_out_freq", "−", "Zoom out in frequency", "Zoom out: see more of the frequency range (⌘⇧↓ · or Shift + scroll)"),
    ("view.zoom_in_freq", "+", "Zoom in in frequency", "Zoom in on fewer frequencies (⌘⇧↑ · or Shift + scroll)"),
    ("view.fit_project", "Whole File", "Show the whole file", "Zoom out to the whole file, all frequencies (⌘0)"),
    ("view.fit_selection", "Selection", "Zoom to the selection", "Zoom in on the current selection (⌘⇧E)"),
)


class ViewBar(QFrame):
    """``ViewBar(controller, actions, parent=None)``: zoom and area-visibility buttons.

    Buttons never shrink (their text must stay readable). When the window is too narrow for all
    of them, the bar goes compact: it drops *Selection* (also in the View menu, ⌘⇧E) and shortens
    "Adjusted areas" to "Areas"."""

    def __init__(self, controller, actions, parent: QWidget | None = None):
        super().__init__(parent)
        self.controller = controller
        self.actions = actions
        self.setObjectName("ViewBar")
        self.setFixedHeight(HEIGHT)
        self.setStyleSheet(
            f"#ViewBar {{ background: {PANEL}; border-top: 1px solid {BORDER}; }}"
            f"#ViewBar QLabel {{ color: {TEXT_DIM}; background: transparent; }}"
            f"#ViewBar QToolButton {{ color: {TEXT}; background: {PANEL_2}; border: 1px solid {BORDER};"
            " border-radius: 4px; padding: 1px 9px; min-height: 22px; min-width: 14px; }"
            "#ViewBar QToolButton:hover { background: #2c3442; border-color: #4a5566; }"
            "#ViewBar QToolButton:pressed { background: #24405a; }"
            "#ViewBar QToolButton:disabled { color: #5d6673; background: #1f232b; }"
            f"#ViewBar QToolButton:checked {{ background: #24405a; border-color: {ACCENT}; color: #ffffff; }}"
            "#ViewBar QToolButton#zoomStep { font-size: 15px; font-weight: 600; padding: 0 8px; }"
        )
        h = QHBoxLayout(self)
        h.setContentsMargins(10, 4, 10, 4)
        h.setSpacing(4)

        self.buttons: dict[str, QToolButton] = {}
        for aid, text, name, tip in ZOOM_BUTTONS:
            if aid is None:
                if text != "Zoom":
                    h.addSpacing(10)
                h.addWidget(_fixed(QLabel(text)))
                continue
            if aid == "view.fit_project":
                h.addSpacing(10)
            b = self._action_button(aid, text, name, tip)
            if text in ("−", "+"):
                b.setObjectName("zoomStep")
            h.addWidget(b)
        h.addStretch(1)

        self.areas_label = _fixed(QLabel("Adjusted areas"))
        h.addWidget(self.areas_label)
        self.show_btn = self._toggle_button("Show", "Show adjusted areas",
                                            "Outline every part you turned up, turned down or silenced, with its level "
                                            "(display only: the sound does not change) · Shift+H")
        self.hide_btn = self._toggle_button("Hide", "Hide adjusted areas",
                                            "Hide those outlines to see the spectrogram clearly (display only). "
                                            "The area you picked stays outlined so you can still adjust it · Shift+H")
        group = QButtonGroup(self)
        group.setExclusive(True)
        group.addButton(self.show_btn)
        group.addButton(self.hide_btn)
        self.show_btn.clicked.connect(lambda: self._set_areas_shown(True))
        self.hide_btn.clicked.connect(lambda: self._set_areas_shown(False))
        h.addWidget(self.show_btn)
        h.addWidget(self.hide_btn)

        self.compact = False
        self.areas_label.setText("Adjusted areas (00)")  # widest usual text
        self.full_width = h.sizeHint().width() + 8
        self.setMinimumWidth(240)  # never forces the editor wider; the bar goes compact instead

        c = controller
        c.view.changed.connect(lambda _what: self.sync())
        for sig in ("sessionReplaced", "areasChanged", "structureChanged"):
            s = getattr(c, sig, None)
            if s is not None:
                s.connect(self.sync)
        self.sync()

    # ------------------------------------------------------------ building
    def _action_button(self, aid: str, text: str, name: str, tip: str) -> QToolButton:
        act = self.actions[aid]
        b = QToolButton(self)
        b.setText(text)
        b.setToolTip(tip)
        b.setAccessibleName(name)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # keep the keyboard on the spectrogram
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        _fixed(b)
        b.clicked.connect(act.trigger)
        b.setEnabled(act.isEnabled())
        act.changed.connect(lambda b=b, act=act: b.setEnabled(act.isEnabled()))
        self.buttons[aid] = b
        return b

    def _toggle_button(self, text: str, name: str, tip: str) -> QToolButton:
        b = QToolButton(self)
        b.setText(text)
        b.setToolTip(tip)
        b.setAccessibleName(name)
        b.setCheckable(True)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        return _fixed(b)

    # ------------------------------------------------------------ behaviour
    def _set_areas_shown(self, on: bool) -> None:
        act = self.actions.actions.get("view.show_areas") if hasattr(self.actions, "actions") else None
        if act is not None:
            if act.isChecked() != on:
                act.setChecked(on)  # same handler as the menu item and Shift+H
        else:
            self.controller.view.set_display(show_areas=on)
        self.sync()

    def sync(self, *_args) -> None:
        s = self.controller.session
        shown = bool(self.controller.view.show_areas)
        self.show_btn.setChecked(shown)
        self.hide_btn.setChecked(not shown)
        has_session = s is not None
        self.show_btn.setEnabled(has_session)
        self.hide_btn.setEnabled(has_session)
        n = len(s.visible_areas()) if has_session else 0
        name = "Areas" if self.compact else "Adjusted areas"
        self.areas_label.setText(f"{name} ({n})" if n else name)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        compact = self.width() < self.full_width
        if compact != self.compact:
            self.compact = compact
            self.buttons["view.fit_selection"].setVisible(not compact)
            self.sync()


def _fixed(w):
    """Keep ``w`` at its natural width so its text is never cut short."""
    w.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    return w
