"""Floating level bar shown next to the selection / picked area on the spectrogram.

    [ Area 1 ]  [−]  ━━━━━━●━━━━  [+]   −12 dB   [▶]  [Silence]  [✕]

The simplest way to change how loud a highlighted part is: click − / + (hold to
repeat, 1 dB per step), or drag the slider. Every change becomes (or updates) an
*adjusted area*, exactly like the ↑ / ↓ keys, so it stays outlined, can be
picked again later and removed exactly. The bar never takes keyboard focus, so
the arrow keys keep working on the spectrogram.
"""

from __future__ import annotations

import math

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QSlider, QToolButton

from spledio.engine.areas import FLOOR_DB, MAX_BOOST_DB, gain_text
from spledio.selections.selection import INVERT

SILENT_POS = int(FLOOR_DB) - 1  # slider position meaning "silenced"
MARGIN = 6


def _btn(text: str, tip: str, name: str) -> QToolButton:
    b = QToolButton()
    b.setText(text)
    b.setToolTip(tip)
    b.setAccessibleName(name)
    b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


class LevelBar(QFrame):
    """Child of :class:`~spledio.ui.spectrogram_view.SpectrogramCanvas`."""

    def __init__(self, canvas):
        super().__init__(canvas)
        self.canvas = canvas
        self.controller = canvas.controller
        self.setObjectName("LevelBar")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            "#LevelBar { background: rgba(16,19,24,238); border: 1px solid #46505f; border-radius: 7px; }"
            "#LevelBar QLabel { color: #dfe4ec; background: transparent; }"
            "#LevelBar QToolButton { color: #dfe4ec; background: rgba(255,255,255,0.07); border: 1px solid #46505f;"
            " border-radius: 4px; padding: 1px 7px; min-height: 20px; }"
            "#LevelBar QToolButton:hover { background: rgba(79,179,255,0.28); }"
            "#LevelBar QToolButton:pressed { background: rgba(79,179,255,0.45); }"
            "#LevelBar QToolButton:disabled { color: #6b7380; }"
        )
        h = QHBoxLayout(self)
        h.setContentsMargins(8, 4, 6, 4)
        h.setSpacing(5)
        self.title = QLabel("Selection")
        self.title.setStyleSheet("font-weight: 600;")
        h.addWidget(self.title)
        self.minus = _btn("−", "Quieter by 1 dB (hold to keep going) · keyboard: ↓ or −, Shift+↓ = 6 dB", "Quieter")
        self.minus.setAutoRepeat(True)
        self.minus.setAutoRepeatDelay(350)
        self.minus.setAutoRepeatInterval(110)
        self.minus.clicked.connect(lambda: self.controller.adjust_area(-1))
        h.addWidget(self.minus)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(SILENT_POS, int(MAX_BOOST_DB))
        self.slider.setValue(0)
        self.slider.setSingleStep(1)
        self.slider.setPageStep(6)
        self.slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.slider.setFixedWidth(130)
        self.slider.setToolTip("Drag to set the level: left = quieter (far left = silence), right = louder")
        self.slider.setAccessibleName("Level")
        self.slider.valueChanged.connect(self._on_slider)
        h.addWidget(self.slider)
        self.plus = _btn("+", "Louder by 1 dB (hold to keep going) · keyboard: ↑ or +, Shift+↑ = 6 dB", "Louder")
        self.plus.setAutoRepeat(True)
        self.plus.setAutoRepeatDelay(350)
        self.plus.setAutoRepeatInterval(110)
        self.plus.clicked.connect(lambda: self.controller.adjust_area(+1))
        h.addWidget(self.plus)
        self.value = QLabel("0 dB")
        self.value.setMinimumWidth(self.value.fontMetrics().horizontalAdvance("Silenced") + 6)
        self.value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.value.setStyleSheet("font-weight: 600;")
        h.addWidget(self.value)
        self.play = _btn("▶", "Play this part in the full mix (changes are heard live) · Space plays everything",
                         "Play this part")
        self.play.clicked.connect(self.controller.play_selection_in_mix)
        h.addWidget(self.play)
        self.silence = _btn("Silence", "Turn this part all the way down (keyboard: 0). It can be brought back", "Silence")
        self.silence.clicked.connect(lambda: self.controller.adjust_area(target_db=-math.inf))
        h.addWidget(self.silence)
        self.remove = _btn("✕", "Remove this adjustment – the audio here comes back exactly (keyboard: Delete)",
                           "Remove adjustment")
        self.remove.clicked.connect(lambda: self.controller.remove_area())
        h.addWidget(self.remove)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._syncing = False
        self._area_id: str | None = None
        self.hide()

    # ------------------------------------------------------------------ state
    def _show_value(self, g: float) -> None:
        self.value.setText(gain_text(g))
        if math.isinf(g):
            col = "#c8ccd4"
        elif g > 0.05:
            col = "#7be08a"
        elif g < -0.05:
            col = "#ffb070"
        else:
            col = "#dfe4ec"
        self.value.setStyleSheet(f"font-weight: 600; color: {col};")

    def _on_slider(self, v: int) -> None:
        if self._syncing:
            return
        g = -math.inf if v <= SILENT_POS else float(v)
        self._show_value(g)
        self.controller.adjust_area(target_db=g, area_id=self._area_id)

    def wanted(self) -> bool:
        c = self.canvas
        s = self.controller.session
        if s is None or c._drag is not None or c._poly:
            return False
        if s.current_area() is None and (s.selection is None or s.selection.is_empty):
            return False
        return c.width() > 360 and c.height() > 120

    def sync(self) -> None:
        """Update value / enabled state and move next to the selection (or hide)."""
        if not self.wanted():
            if self.isVisible():
                self.hide()
            return
        s = self.controller.session
        a = s.current_area()
        self._area_id = a.id if a is not None else None
        g = a.gain_db if a is not None else 0.0
        self.title.setText(a.name if a is not None else "Selection")
        self.remove.setVisible(a is not None)
        layer = s.project.layer(a.layer_id) if a is not None else s.active_layer
        locked = layer is None or layer.locked
        for w in (self.minus, self.plus, self.slider, self.silence):
            w.setEnabled(not locked)
        self.setToolTip(f"Layer '{layer.name}' is locked – unlock it to change levels" if locked and layer else "")
        if not self.slider.isSliderDown():
            self._syncing = True
            try:
                self.slider.setValue(SILENT_POS if math.isinf(g) else int(round(max(FLOOR_DB, min(MAX_BOOST_DB, g)))))
            finally:
                self._syncing = False
            self._show_value(g)
        self.adjustSize()
        self._place()
        if not self.isVisible():
            self.show()
            self.raise_()

    def _place(self) -> None:
        c = self.canvas
        s = self.controller.session
        sel = s.selection
        a = s.current_area()
        if a is not None:
            sel = a.selection
        W, H = c.width(), c.height()
        w, h = self.width(), self.height()
        g = c.view_geometry()
        b = None
        if sel is not None and not any(op.mode == INVERT for op in sel.ops):
            b = sel.bounds(s.project.duration, s.project.nyquist)
        if b is None:
            x, y = W - w - MARGIN, MARGIN
        else:
            t0, t1, f0, f1 = b
            x_right = float(g.x_of_t(t1))
            y_top = float(g.y_of_f(min(f1, g.scale.f_hi)))
            y_bot = float(g.y_of_f(max(f0, g.scale.f_lo)))
            x = x_right - w
            y = y_top - h - MARGIN
            if y < MARGIN:  # no room above: below the box, else inside its top
                y = y_bot + MARGIN
                if y + h > H - 30:
                    y = max(y_top, 0) + MARGIN
        x = int(min(max(MARGIN, x), W - w - MARGIN))
        y = int(min(max(MARGIN, y), H - h - MARGIN))
        if (x, y) != (self.x(), self.y()):
            self.move(x, y)


__all__ = ["LevelBar"]
