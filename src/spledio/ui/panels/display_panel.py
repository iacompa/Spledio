"""Display dock: colour map, dB range, gamma, overlays, frequency scale,
display analysis (FFT / window / overlap), channel and layer display,
numeric viewport, and - separately - the processing resolution used for
FUTURE edits.

Everything except the "Processing resolution" group goes through
``controller.view`` (``ViewState``) and can never change audio.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
)

from spledio.dsp.stft import SUPPORTED_FFT_SIZES, SUPPORTED_HOP_DIVISORS, SUPPORTED_WINDOWS, STFTConfig, STFTConfigError
from spledio.rendering.colormaps import COLORBLIND_FRIENDLY, COLORMAP_NAMES
from spledio.ui.actions import ActionRegistry
from spledio.ui.panels.common import (
    DANGER,
    WARN,
    Combo,
    PanelBase,
    SpinBox,
    button_grid,
    note_label,
)

OVERLAP_LABELS = {2: "50 %", 4: "75 %", 8: "87.5 %"}

FFT_TIP = ("Analysis window length. Larger windows favour frequency detail (finer bins) but blur timing; "
           "smaller windows favour timing detail but blur frequency.")
WINDOW_TIP = ("Window shape. Hann is a good default; Blackman has lower side lobes (cleaner background) but wider "
              "peaks; Hamming has narrower peaks with higher side lobes.")
OVERLAP_TIP = ("Overlap between successive analysis frames (hop = FFT size / 2, / 4, / 8). More overlap gives a "
               "denser time grid; it does not add frequency detail.")
INTERP_NOTE = "Display interpolation smooths the picture; it does not create recorded information."

TOGGLES = (
    # view attribute, label, tooltip
    ("show_note_labels", "Note labels", "Label the frequency axis with musical note names (A4 = 440 Hz)"),
    ("show_crosshair", "Crosshair", "Show a crosshair with time / frequency / level readout under the pointer"),
    ("show_mask_overlay", "Selection mask overlay", "Tint the selected cells exactly as they will be processed"),
    ("show_overview", "Overview strip", "Show the whole-project overview strip above the spectrogram"),
    ("show_waveform", "Waveform", "Show the waveform lane"),
)


def stft_combos(tip_prefix: str) -> tuple[Combo, Combo, Combo]:
    fft = Combo(FFT_TIP, f"{tip_prefix} FFT size")
    fft.set_items([(str(n), n) for n in SUPPORTED_FFT_SIZES])
    win = Combo(WINDOW_TIP, f"{tip_prefix} window")
    win.set_items([(w, w) for w in SUPPORTED_WINDOWS])
    ovl = Combo(OVERLAP_TIP, f"{tip_prefix} overlap")
    ovl.set_items([(OVERLAP_LABELS.get(d, f"1/{d}"), d) for d in SUPPORTED_HOP_DIVISORS])
    return fft, win, ovl


def config_from(fft: Combo, win: Combo, ovl: Combo) -> STFTConfig:
    n = int(fft.currentData())
    return STFTConfig(n, n // int(ovl.currentData()), str(win.currentData())).validate()


def show_config(cfg: STFTConfig, fft: Combo, win: Combo, ovl: Combo) -> None:
    fft.set_data_quiet(cfg.n_fft)
    win.set_data_quiet(cfg.window)
    ovl.set_data_quiet(cfg.n_fft // cfg.hop if cfg.hop else 4)


def grid_text(cfg: STFTConfig, sr: float) -> str:
    return f"{cfg.frame_spacing(sr) * 1000:.1f} ms × {cfg.bin_spacing(sr):.1f} Hz ({cfg.window} {cfg.n_fft}/{cfg.hop})"


class DisplayPanel(PanelBase):
    """Display dock panel. ``DisplayPanel(controller, actions, parent=None)``."""

    def __init__(self, controller, actions: ActionRegistry, parent=None):
        super().__init__(controller, actions, parent, scrollable=True)
        self.setObjectName("DisplayPanel")
        self.setWindowTitle("Display")
        view = controller.view
        lay = self.body_layout
        lay.addWidget(note_label("Display settings never change the audio."))

        # ---- appearance
        g_a = QGroupBox("Appearance")
        ga = QGridLayout(g_a)
        ga.setHorizontalSpacing(6)
        self.cmap = Combo("Colour map of the spectrogram. Maps marked “colour-blind friendly” stay readable with "
                          "common colour-vision deficiencies.", "Colour map")
        self.cmap.set_items([(f"{n} (colour-blind friendly)" if n in COLORBLIND_FRIENDLY else n, n)
                             for n in COLORMAP_NAMES])
        self.cmap.activated.connect(lambda _i: self._set(colormap=self.cmap.currentData()))
        ga.addWidget(QLabel("Colour map"), 0, 0)
        ga.addWidget(self.cmap, 0, 1, 1, 2)
        self.min_db = SpinBox(-240.0, 40.0, 1.0, 1, " dB", "Level shown as the darkest colour (must be below max)",
                              "Minimum dB")
        self.max_db = SpinBox(-240.0, 40.0, 1.0, 1, " dB", "Level shown as the brightest colour (must be above min)",
                              "Maximum dB")
        self.min_db.valueChanged.connect(self._on_db_range)
        self.max_db.valueChanged.connect(self._on_db_range)
        ga.addWidget(QLabel("Range"), 1, 0)
        ga.addWidget(self.min_db, 1, 1)
        ga.addWidget(self.max_db, 1, 2)
        self.db_error = note_label("", DANGER)
        self.db_error.setVisible(False)
        ga.addWidget(self.db_error, 2, 0, 1, 3)
        self.gamma = SpinBox(0.3, 3.0, 0.05, 2, "", "Contrast curve: below 1 brightens quiet detail, above 1 darkens it",
                             "Gamma")
        self.gamma.valueChanged.connect(lambda v: self._set(gamma=float(v)))
        ga.addWidget(QLabel("Gamma"), 3, 0)
        ga.addWidget(self.gamma, 3, 1)
        self.scale = Combo("Frequency axis: Log spreads low frequencies (music), Linear spreads high frequencies",
                           "Frequency scale")
        self.scale.set_items([("Log", "log"), ("Linear", "linear")])
        self.scale.activated.connect(lambda _i: self._set(freq_scale=self.scale.currentData()))
        ga.addWidget(QLabel("Freq scale"), 4, 0)
        ga.addWidget(self.scale, 4, 1, 1, 2)
        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(0, 100)
        self.opacity.setPageStep(10)
        self.opacity.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.opacity.setToolTip("Opacity of the selection / mask overlay (display only)")
        self.opacity.setAccessibleName("Overlay opacity")
        self.opacity_label = QLabel("35 %")
        self.opacity_label.setMinimumWidth(36)
        self.opacity.valueChanged.connect(self._on_opacity)
        ga.addWidget(QLabel("Overlay"), 5, 0)
        ga.addWidget(self.opacity, 5, 1)
        ga.addWidget(self.opacity_label, 5, 2)
        ga.setColumnStretch(1, 1)
        ga.setColumnStretch(2, 1)
        lay.addWidget(g_a)

        g_t = QGroupBox("Show")
        gt = QVBoxLayout(g_t)
        gt.setSpacing(2)
        self.toggles: dict[str, QCheckBox] = {}
        for attr, label, tip in TOGGLES:
            cb = QCheckBox(label)
            cb.setToolTip(tip + " (display only)")
            cb.toggled.connect(lambda on, a=attr: self._set(**{a: bool(on)}))
            gt.addWidget(cb)
            self.toggles[attr] = cb
        lay.addWidget(g_t)

        # ---- display analysis
        g_d = QGroupBox("Display analysis")
        gd = QGridLayout(g_d)
        self.d_fft, self.d_win, self.d_ovl = stft_combos("Display")
        for cmb in (self.d_fft, self.d_win, self.d_ovl):
            cmb.activated.connect(self._on_display_config)
        gd.addWidget(QLabel("FFT size"), 0, 0)
        gd.addWidget(self.d_fft, 0, 1)
        gd.addWidget(QLabel("Window"), 1, 0)
        gd.addWidget(self.d_win, 1, 1)
        gd.addWidget(QLabel("Overlap"), 2, 0)
        gd.addWidget(self.d_ovl, 2, 1)
        self.d_grid = note_label("")
        gd.addWidget(self.d_grid, 3, 0, 1, 2)
        self.d_error = note_label("", DANGER)
        self.d_error.setVisible(False)
        gd.addWidget(self.d_error, 4, 0, 1, 2)
        self.chan = Combo("Which channel the spectrogram shows. Mix averages the channels' power (display only).",
                          "Channel display")
        self.chan.activated.connect(lambda _i: self._set(channel_mode=self.chan.currentData()))
        gd.addWidget(QLabel("Channels"), 5, 0)
        gd.addWidget(self.chan, 5, 1)
        self.layer_disp = Combo("Composite shows the audible mix of all layers; Active layer only shows the layer "
                                "you are editing (display only)", "Layer display")
        self.layer_disp.set_items([("Composite", "composite"), ("Active layer only", "active")])
        self.layer_disp.activated.connect(lambda _i: self._set(layer_display=self.layer_disp.currentData()))
        gd.addWidget(QLabel("Layers"), 6, 0)
        gd.addWidget(self.layer_disp, 6, 1)
        gd.addWidget(note_label(INTERP_NOTE), 7, 0, 1, 2)
        gd.setColumnStretch(1, 1)
        lay.addWidget(g_d)

        # ---- viewport
        g_v = QGroupBox("Visible range")
        gv = QGridLayout(g_v)
        gv.setHorizontalSpacing(4)
        self.v_t0 = SpinBox(0.0, 1.0, 0.1, 3, " s", "Left edge of the view (seconds)", "View start time")
        self.v_t1 = SpinBox(0.0, 1.0, 0.1, 3, " s", "Right edge of the view (seconds)", "View end time")
        self.v_f0 = SpinBox(0.0, 22050.0, 10.0, 1, " Hz", "Bottom of the view (Hz)", "View low frequency")
        self.v_f1 = SpinBox(0.0, 22050.0, 10.0, 1, " Hz", "Top of the view (Hz)", "View high frequency")
        gv.addWidget(QLabel("Time"), 0, 0)
        gv.addWidget(self.v_t0, 0, 1)
        gv.addWidget(QLabel("–"), 0, 2)
        gv.addWidget(self.v_t1, 0, 3)
        gv.addWidget(QLabel("Freq"), 1, 0)
        gv.addWidget(self.v_f0, 1, 1)
        gv.addWidget(QLabel("–"), 1, 2)
        gv.addWidget(self.v_f1, 1, 3)
        gv.setColumnStretch(1, 1)
        gv.setColumnStretch(3, 1)
        for sp in (self.v_t0, self.v_t1, self.v_f0, self.v_f1):
            sp.returnPressed.connect(self.go)
        self.btn_go = QPushButton("Go")
        self.btn_go.setToolTip("Show exactly this time / frequency range (Enter in a field does the same)")
        self.btn_go.clicked.connect(self.go)
        self.btn_fit = self.action_button("Fit project", "Show the whole project", "view.fit_project",
                                          lambda: self.controller.view.fit_project(), "fit_project")
        self.btn_reset = self.action_button("Reset view", "Reset colour map, dB range, gamma, overlay, scale and zoom",
                                            "view.reset", lambda: self.controller.view.reset(), "reset_view")
        row = QHBoxLayout()
        row.addWidget(self.btn_go)
        row.addLayout(button_grid([self.btn_fit, self.btn_reset], 2), 1)
        gv.addLayout(row, 2, 0, 1, 4)
        lay.addWidget(g_v)

        # ---- processing resolution (audio-affecting: future edits only)
        g_p = QGroupBox("Processing resolution")
        g_p.setToolTip("Processing resolution (affects FUTURE edits only)")
        gp = QGridLayout(g_p)
        future = note_label("Affects FUTURE edits only – this changes how the next edits are computed, "
                            "not the display.", WARN, small=False)
        self.p_fft, self.p_win, self.p_ovl = stft_combos("Processing")
        for cmb in (self.p_fft, self.p_win, self.p_ovl):
            cmb.setToolTip(cmb.toolTip() + " Used for the next edits; committed edits keep their own stored settings.")
            cmb.activated.connect(self._on_processing_config)
        gp.addWidget(future, 0, 0, 1, 2)
        gp.addWidget(QLabel("FFT size"), 1, 0)
        gp.addWidget(self.p_fft, 1, 1)
        gp.addWidget(QLabel("Window"), 2, 0)
        gp.addWidget(self.p_win, 2, 1)
        gp.addWidget(QLabel("Overlap"), 3, 0)
        gp.addWidget(self.p_ovl, 3, 1)
        self.p_grid = note_label("")
        gp.addWidget(self.p_grid, 4, 0, 1, 2)
        self.p_error = note_label("", DANGER)
        self.p_error.setVisible(False)
        gp.addWidget(self.p_error, 5, 0, 1, 2)
        gp.addWidget(note_label("This is the grid edits are computed on. Committed edits keep the settings they were "
                                "made with, so changing it never reinterprets earlier edits. Independent of the "
                                "display analysis above."), 6, 0, 1, 2)
        gp.setColumnStretch(1, 1)
        lay.addWidget(g_p)
        lay.addStretch(1)

        c = controller
        c.sessionReplaced.connect(self._on_session_replaced)
        c.structureChanged.connect(self._refresh_ranges)
        c.selectionChanged.connect(self._refresh_processing)  # set_edit_config emits a selection event
        view.changed.connect(self._on_view_changed)
        self._on_session_replaced()

    # ------------------------------------------------------------ refresh
    def _on_session_replaced(self) -> None:
        s = self.session
        self.set_session_available(s is not None)
        n = s.project.channels if s is not None else 1
        items = [("Mix (power average)", "mix")]
        if n == 2:
            items += [("Left", "ch0"), ("Right", "ch1")]
        elif n > 2:
            items += [(f"Channel {i + 1}", f"ch{i}") for i in range(n)]
        self.chan.set_items(items)
        self.chan.setEnabled(n > 1)
        self._refresh_ranges()
        self._refresh_display()
        self._refresh_processing()

    def _on_view_changed(self, what: str) -> None:
        if what == "viewport":
            self._refresh_viewport()
        else:
            self._refresh_display()

    def _refresh_display(self) -> None:
        v = self.controller.view
        if not self.cmap.set_data_quiet(v.colormap):
            self.cmap.blockSignals(True)
            self.cmap.addItem(f"{v.colormap} (unknown)", v.colormap)
            self.cmap.setCurrentIndex(self.cmap.count() - 1)
            self.cmap.blockSignals(False)
        self.min_db.sync(v.min_db)
        self.max_db.sync(v.max_db)
        self.db_error.setVisible(False)
        self.gamma.sync(v.gamma)
        self.scale.set_data_quiet(v.freq_scale)
        self.opacity.blockSignals(True)
        self.opacity.setValue(int(round(v.overlay_opacity * 100)))
        self.opacity.blockSignals(False)
        self.opacity_label.setText(f"{self.opacity.value()} %")
        for attr, cb in self.toggles.items():
            cb.blockSignals(True)
            cb.setChecked(bool(getattr(v, attr)))
            cb.blockSignals(False)
        show_config(v.display_config, self.d_fft, self.d_win, self.d_ovl)
        s = self.session
        if s is not None:
            self.d_grid.setText("Display grid: " + grid_text(v.display_config, s.sample_rate))
        self.chan.set_data_quiet(v.channel_mode)
        self.layer_disp.set_data_quiet(v.layer_display)

    def _refresh_ranges(self) -> None:
        s = self.session
        if s is None:
            return
        dur, nyq = s.project.duration, s.project.nyquist
        for sp in (self.v_t0, self.v_t1):
            sp.set_range_quiet(0.0, dur)
        for sp in (self.v_f0, self.v_f1):
            sp.set_range_quiet(0.0, nyq)
        self._refresh_viewport(force=True)

    def _refresh_viewport(self, force: bool = False) -> None:
        v = self.controller.view
        for sp, val in ((self.v_t0, v.t0), (self.v_t1, v.t1), (self.v_f0, v.f_lo), (self.v_f1, v.f_hi)):
            sp.sync(val, force=force)  # never fights a value the user is typing

    def _refresh_processing(self) -> None:
        s = self.session
        if s is None:
            self.p_grid.setText("")
            return
        show_config(s.edit_config, self.p_fft, self.p_win, self.p_ovl)
        self.p_grid.setText("Processing grid: " + grid_text(s.edit_config, s.sample_rate))

    # ------------------------------------------------------------ handlers
    def _set(self, **kw) -> None:
        if self.session is None:
            return
        v = self.controller.view
        if all(getattr(v, k) == val for k, val in kw.items()):
            return
        v.set_display(**kw)

    def _on_db_range(self, *_):
        lo, hi = self.min_db.value(), self.max_db.value()
        v = self.controller.view
        if lo >= hi:
            self.min_db.set_quiet(v.min_db)
            self.max_db.set_quiet(v.max_db)
            self.db_error.setText(f"Min must be below max; kept {v.min_db:.1f} … {v.max_db:.1f} dB.")
            self.db_error.setVisible(True)
            return
        self.db_error.setVisible(False)
        self._set(min_db=float(lo), max_db=float(hi))

    def _on_opacity(self, value: int) -> None:
        self.opacity_label.setText(f"{value} %")
        self._set(overlay_opacity=value / 100.0)

    def _on_display_config(self, *_):
        if self.session is None:
            return
        try:
            cfg = config_from(self.d_fft, self.d_win, self.d_ovl)
        except STFTConfigError as e:
            self.d_error.setText(f"{e}. Display settings unchanged.")
            self.d_error.setVisible(True)
            self._refresh_display()
            return
        self.d_error.setVisible(False)
        self._set(display_config=cfg)

    def _on_processing_config(self, *_):
        s = self.session
        if s is None:
            return
        if self.guard_busy():
            self._refresh_processing()
            return
        try:
            cfg = config_from(self.p_fft, self.p_win, self.p_ovl)
        except STFTConfigError as e:
            self.p_error.setText(f"{e}. Processing resolution unchanged.")
            self.p_error.setVisible(True)
            self._refresh_processing()
            return
        self.p_error.setVisible(False)
        if cfg == s.edit_config:
            return
        try:
            self.controller.safe(s.set_edit_config, cfg)
        except STFTConfigError as e:  # validate() inside the session
            self.p_error.setText(str(e))
            self.p_error.setVisible(True)
        self._refresh_processing()
        self.controller.statusMessage.emit(
            f"Processing resolution for future edits: {grid_text(s.edit_config, s.sample_rate)}. "
            "Committed edits are unchanged.", 6000)

    def go(self) -> None:
        if self.session is None:
            return
        for sp in (self.v_t0, self.v_t1, self.v_f0, self.v_f1):
            sp.interpretText()
        t0, t1 = sorted((self.v_t0.value(), self.v_t1.value()))
        f0, f1 = sorted((self.v_f0.value(), self.v_f1.value()))
        v = self.controller.view
        v.set_time_range(t0, t1)
        v.set_freq_range(f0, f1)
        self._refresh_viewport(force=True)  # show the clamped values actually in effect


__all__ = ["DisplayPanel"]
