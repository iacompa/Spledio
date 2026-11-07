"""Guided 'Reduce Resonance' workflow + resonance candidate finder.

Ten numbered steps, each with a one-line explanation and buttons that run
the real registry actions (``tool.freq``, ``audition.selected``,
``module.preview`` ...). The step that fits the current state (no audio,
no selection, preview pending, edit applied) is highlighted.

"Find resonance candidates" runs :func:`spledio.dsp.resonance.suggest_resonances`
on a worker thread over the audible mix (``Project.render_mix``) of the
selection's time span - or of the visible view range when nothing is
selected; the panel says which. Accepting a candidate only creates a
selection (a narrow frequency band over that time span, with a small
frequency feather) so it can be auditioned; it never edits audio.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QCheckBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from spledio.dsp.resonance import ResonanceCandidate, suggest_resonances
from spledio.dsp.stft import STFTConfig
from spledio.selections.geometry import RectShape
from spledio.selections.selection import Selection
from spledio.ui.panels.edit_panel import (
    GAIN_MAX_DB,
    GAIN_MIN_DB,
    GAIN_STEP_DB,
    ActionBinder,
    clamp_gain,
    edit_reason,
    fmt_gain,
    has_audio_edit,
    make_button,
    module_apply,
    module_preview,
    note_label,
    track_busy_label,
)

MODERATE_REDUCTION_DB = -6.0
MAX_ANALYSIS_S = 60.0  # longer ranges: the first 60 s are analysed (and the label says so)
FINDER_JOB_LABEL = "Find resonance candidates"

EXPLANATION = (
    "Candidates are narrow, persistent peaks that stand out from a local spectral baseline (the median level "
    "within about a third of an octave). A sustained musical note or one of its harmonics can look exactly like "
    "a resonance, so listen before you reduce anything. Accept only selects the band so you can audition it – "
    "nothing is removed automatically."
)


def _fmt_range(t0: float, t1: float, f0: float | None = None, f1: float | None = None) -> str:
    text = f"{t0:.2f}–{t1:.2f} s"
    if f0 is not None and f1 is not None:
        text += f", {f0:.0f}–{f1:.0f} Hz"
    return text


@dataclass
class AnalysisRange:
    t0: float
    t1: float
    f_lo: float
    f_hi: float
    source: str  # 'selection' | 'view'
    capped: bool = False

    def describe(self) -> str:
        where = "the selection's time span" if self.source == "selection" else "the visible view range (no selection)"
        text = f"{where}: {_fmt_range(self.t0, self.t1, self.f_lo, self.f_hi)}"
        if self.capped:
            text += f" – only the first {MAX_ANALYSIS_S:.0f} s are analysed; narrow the range for other parts"
        return text


@dataclass
class CandidateRow:
    """One listed candidate (possibly several nearby peaks shown as one)."""

    cand: ResonanceCandidate
    f_lo: float
    f_hi: float
    merged: int = 1
    accepted: bool = False
    widgets: dict = field(default_factory=dict)


def choose_config(n_samples: int) -> STFTConfig | None:
    """Largest analysis FFT (8192 … 1024, hop n/4) that the range can hold."""
    for n in (8192, 4096, 2048, 1024):
        if n_samples >= n:
            return STFTConfig(n, n // 4, "hann")
    return None


def merge_nearby(cands: list[ResonanceCandidate], distance_hz: float) -> list[CandidateRow]:
    """Show peaks closer than ``distance_hz`` as one row (the strongest; input is sorted strongest first)."""
    rows: list[CandidateRow] = []
    for c in cands:
        for r in rows:
            if r.f_lo - distance_hz <= c.frequency_hz <= r.f_hi + distance_hz:
                r.f_lo = min(r.f_lo, c.frequency_hz)
                r.f_hi = max(r.f_hi, c.frequency_hz)
                r.merged += 1
                break
        else:
            rows.append(CandidateRow(c, c.frequency_hz, c.frequency_hz))
    return rows


class ResonancePanel(QWidget):
    """The guided 'Reduce Resonance' workflow."""

    STEPS = (
        (1, "Open the recording", "Load the file; the source file itself is never modified."),
        (2, "Zoom to the problem", "Find the thin bright horizontal line that keeps ringing and zoom in on it."),
        (3, "Select it", "Drag a frequency band or a rectangle tightly around the ring – or paint with the "
                         "reduce brush (that reduces while you paint)."),
        (4, "Audition the selection", "Hear only what is selected: it should sound mostly like the ring."),
        (5, "Adjust bounds and feather", "Narrow or widen the band and soften its edges so the change does not "
                                         "leave an audible hole."),
        (6, "Apply a moderate reduction", "Start at −6 dB: Preview renders it so you can hear it before applying."),
        (7, "Compare A/B", "Switch between original and processed at the same playback position."),
        (8, "Listen to what is removed", "If the difference contains the wanted sound, the band is too wide or the "
                                         "reduction too deep."),
        (9, "Refine the amount", "Adjust the reduction and preview again. Already applied? Undo is one step."),
        (10, "Export or continue", "Export the result, or select the next resonance."),
    )

    def __init__(self, controller, actions, parent=None):
        super().__init__(parent)
        self.setObjectName("ResonancePanel")
        self.controller = controller
        self.actions = actions
        track_busy_label(controller)
        self.binder = ActionBinder(actions, self)
        self.binder.refused.connect(lambda r: self.finder_status.setText(r))
        self.rows: list[CandidateRow] = []
        self.result_range: AnalysisRange | None = None
        self.result_config: STFTConfig | None = None
        self.result_offset = 0.0  # seconds: candidate times are relative to the analysed snippet
        self._syncing = False
        self.step_frames: dict[int, QFrame] = {}
        self.step_buttons: dict[int, list[QPushButton]] = {}
        self._build()
        self._connect()
        self.refresh()

    # ------------------------------------------------------------------ UI
    def _build(self) -> None:
        c = self.controller
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)
        title = QLabel("Reduce Resonance – guided workflow")
        f = title.font()
        f.setBold(True)
        title.setFont(f)
        root.addWidget(title)
        root.addWidget(note_label("Follow the steps in order; each button runs the same command as the menu or "
                                  "shortcut. The highlighted step is the one that fits what you have done so far."))

        tools = c.tools
        self._add_step(root, 1, [("Open audio…", "file.open_audio", "open", self._open_fallback, None, False)])
        self._add_step(root, 2, [
            ("Zoom in", "view.zoom_in", "zoom_in", lambda: c.view.zoom_time(0.5), self._need_session, False),
            ("Fit selection", "view.fit_selection", "fit_selection", self._fit_selection_fallback, self._need_selection,
             False),
        ])
        self._add_step(root, 3, [
            ("Frequency band", "tool.freq", "freq", lambda: tools.set(tool="freq"), None, True),
            ("Rectangle", "tool.rect", "rect", lambda: tools.set(tool="rect"), None, True),
            ("Reduce brush", "tool.reduce_brush", "reduce_brush", lambda: tools.set(tool="reduce_brush"), None, True),
        ])
        self._add_step(root, 4, [("Hear selected frequencies", "audition.selected", "hear_selected",
                                  lambda: c.audition_mode("selected"), lambda: edit_reason(c, "audition"), False)])
        self._add_step(root, 5, [], extra=self._build_bounds_widget())
        self._add_step(root, 6, [
            (f"Preview {fmt_gain(MODERATE_REDUCTION_DB)}", "module.preview", "preview",
             lambda: module_preview(c), self._preview_reason, False),
            ("Apply", self._apply_action_id, "apply", lambda: module_apply(c), lambda: edit_reason(c, "apply"), False),
        ])
        # step 6 sets the moderate amount before previewing
        b6 = self.step_buttons[6][0]
        self.binder.binding(b6).before = lambda: tools.set(gain_db=MODERATE_REDUCTION_DB)
        self._add_step(root, 7, [("A/B compare", "audition.ab", "ab", c.ab_toggle, lambda: edit_reason(c, "compare"),
                                  False)])
        self._add_step(root, 8, [("Hear what is being removed", "audition.removed", "hear_removed",
                                  lambda: c.audition_mode("difference"), lambda: edit_reason(c, "compare"), False)])
        self._add_step(root, 9, [
            ("Preview again", "module.preview", "preview", lambda: module_preview(c),
             lambda: edit_reason(c, "preview"), False),
            ("Apply", self._apply_action_id, "apply", lambda: module_apply(c), lambda: edit_reason(c, "apply"), False),
        ], extra=self._build_amount_widget())
        self._add_step(root, 10, [("Export…", "file.export", "export", None, None, False)])

        root.addWidget(self._build_finder())
        root.addStretch(1)

    def _add_step(self, root: QVBoxLayout, n: int, buttons, extra: QWidget | None = None) -> None:
        _, title, text = self.STEPS[n - 1]
        frame = QFrame()
        frame.setObjectName("resonanceStep")
        frame.setFrameShape(QFrame.StyledPanel)
        lay = QGridLayout(frame)
        lay.setContentsMargins(6, 4, 6, 6)
        lay.setHorizontalSpacing(6)
        lay.setVerticalSpacing(3)
        badge = QLabel(str(n))
        badge.setAlignment(Qt.AlignCenter)
        badge.setFixedSize(22, 22)
        badge.setStyleSheet("border: 1px solid #4fb3ff; border-radius: 11px; color: #4fb3ff; font-weight: bold;")
        head = QLabel(f"<b>{title}</b>")
        expl = QLabel(text)
        expl.setWordWrap(True)
        expl.setStyleSheet("color: #9aa4b2;")
        lay.addWidget(badge, 0, 0, 2, 1, Qt.AlignTop)
        lay.addWidget(head, 0, 1)
        lay.addWidget(expl, 1, 1)
        btns: list[QPushButton] = []
        if buttons:
            row = QGridLayout()
            row.setSpacing(4)
            for label, aid, ic, fallback, reason, activate in buttons:
                btn = make_button(label, ic, f"step{n}_{len(btns)}")
                watch = ("module.apply", "edit.apply_gain") if callable(aid) else ()
                self.binder.bind(btn, aid, fallback=fallback, reason=reason, activate=activate, watch=watch,
                                 tooltip=f"Step {n}: {text}")
                row.addWidget(btn, len(btns) // 2, len(btns) % 2)
                btns.append(btn)
            lay.addLayout(row, 2, 1)
        if extra is not None:
            lay.addWidget(extra, 3, 1)
        self.step_frames[n] = frame
        self.step_buttons[n] = btns
        root.addWidget(frame)

    def _build_bounds_widget(self) -> QWidget:
        w = QWidget()
        g = QGridLayout(w)
        g.setContentsMargins(0, 0, 0, 0)
        g.setSpacing(4)
        self.narrow_button = QPushButton("Narrower band")
        self.wider_button = QPushButton("Wider band")
        self.narrow_button.setObjectName("narrowBand")
        self.wider_button.setObjectName("widerBand")
        self.narrow_button.clicked.connect(lambda: self.adjust_band(-1))
        self.wider_button.clicked.connect(lambda: self.adjust_band(+1))
        self.band_step = QDoubleSpinBox()
        self.band_step.setRange(1.0, 500.0)
        self.band_step.setValue(10.0)
        self.band_step.setSuffix(" Hz")
        self.band_step.setDecimals(0)
        self.band_step.setToolTip("How much each click moves the lower and upper band edges")
        self.feather_f_spin = QDoubleSpinBox()
        self.feather_f_spin.setObjectName("featherF")
        self.feather_f_spin.setRange(0.0, 2000.0)
        self.feather_f_spin.setDecimals(0)
        self.feather_f_spin.setSingleStep(5.0)
        self.feather_f_spin.setSuffix(" Hz")
        self.feather_f_spin.setKeyboardTracking(False)
        self.feather_f_spin.setToolTip("Soft frequency edge of the selection (raised-cosine fall-off outside the band)")
        self.feather_t_spin = QDoubleSpinBox()
        self.feather_t_spin.setObjectName("featherT")
        self.feather_t_spin.setRange(0.0, 2000.0)
        self.feather_t_spin.setDecimals(0)
        self.feather_t_spin.setSingleStep(5.0)
        self.feather_t_spin.setSuffix(" ms")
        self.feather_t_spin.setKeyboardTracking(False)
        self.feather_t_spin.setToolTip("Soft time edge of the selection (fade in/out of the edit)")
        self.feather_f_spin.valueChanged.connect(lambda _v: self.apply_feather())
        self.feather_t_spin.valueChanged.connect(lambda _v: self.apply_feather())
        g.addWidget(self.narrow_button, 0, 0)
        g.addWidget(self.wider_button, 0, 1)
        g.addWidget(QLabel("Edge step"), 1, 0)
        g.addWidget(self.band_step, 1, 1)
        g.addWidget(QLabel("Freq. feather"), 2, 0)
        g.addWidget(self.feather_f_spin, 2, 1)
        g.addWidget(QLabel("Time feather"), 3, 0)
        g.addWidget(self.feather_t_spin, 3, 1)
        return w

    def _build_amount_widget(self) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(QLabel("Amount"))
        self.amount_spin = QDoubleSpinBox()
        self.amount_spin.setObjectName("resonanceAmount")
        self.amount_spin.setRange(GAIN_MIN_DB, GAIN_MAX_DB)
        self.amount_spin.setDecimals(1)
        self.amount_spin.setSingleStep(GAIN_STEP_DB)
        self.amount_spin.setSuffix(" dB")
        self.amount_spin.setKeyboardTracking(False)
        self.amount_spin.setCorrectionMode(QAbstractSpinBox.CorrectToNearestValue)
        self.amount_spin.setToolTip("Same amount as in 'Reduce selected sound' (−36 … +36 dB)")
        self.amount_spin.valueChanged.connect(self._on_amount)
        h.addWidget(self.amount_spin)
        h.addStretch(1)
        return w

    def _build_finder(self) -> QGroupBox:
        box = QGroupBox("Find resonance candidates")
        v = QVBoxLayout(box)
        self.range_label = QLabel()
        self.range_label.setObjectName("finderRange")
        self.range_label.setWordWrap(True)
        v.addWidget(self.range_label)
        form = QFormLayout()
        self.min_prom_spin = QDoubleSpinBox()
        self.min_prom_spin.setObjectName("minProminence")
        self.min_prom_spin.setRange(1.0, 40.0)
        self.min_prom_spin.setValue(6.0)
        self.min_prom_spin.setSingleStep(1.0)
        self.min_prom_spin.setDecimals(1)
        self.min_prom_spin.setSuffix(" dB")
        self.min_prom_spin.setToolTip("How far a peak must rise above the local baseline to be listed")
        self.max_width_spin = QDoubleSpinBox()
        self.max_width_spin.setObjectName("maxWidth")
        self.max_width_spin.setRange(5.0, 2000.0)
        self.max_width_spin.setValue(250.0)
        self.max_width_spin.setSingleStep(10.0)
        self.max_width_spin.setDecimals(0)
        self.max_width_spin.setSuffix(" Hz")
        self.max_width_spin.setToolTip("Wider peaks are ignored (they are tonal balance, not a ringing resonance)")
        self.merge_check = QCheckBox("Show nearby peaks as one")
        self.merge_check.setChecked(True)
        self.merge_check.setToolTip("One ringing resonance often shows several peaks a few bins apart; "
                                    "list them as one row (the strongest) covering all of them")
        form.addRow("Min. prominence", self.min_prom_spin)
        form.addRow("Max. width", self.max_width_spin)
        v.addLayout(form)
        v.addWidget(self.merge_check)
        self.find_button = make_button("Find candidates", "focus", "findCandidates")
        self.find_button.clicked.connect(self.find_candidates)
        v.addWidget(self.find_button)
        self.finder_status = QLabel()
        self.finder_status.setObjectName("finderStatus")
        self.finder_status.setWordWrap(True)
        v.addWidget(self.finder_status)
        self.results = QTableWidget(0, 5)
        self.results.setObjectName("candidates")
        self.results.setHorizontalHeaderLabels(["Frequency", "Prominence", "Width", "Persistence", ""])
        self.results.verticalHeader().setVisible(False)
        self.results.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.results.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.results.setSelectionMode(QAbstractItemView.SingleSelection)
        hh = self.results.horizontalHeader()
        for i in range(4):
            hh.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.Stretch)
        self.results.setMinimumHeight(140)
        self.results.setMinimumWidth(120)
        self.results.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        v.addWidget(self.results)
        v.addWidget(note_label(EXPLANATION))
        return box

    def _connect(self) -> None:
        c = self.controller
        for sig in (c.sessionReplaced, c.selectionChanged, c.previewChanged, c.historyChanged, c.structureChanged,
                    c.propsChanged, c.playbackChanged):
            sig.connect(self.refresh)
        c.busyChanged.connect(lambda *_: self.refresh())
        c.tools.changed.connect(self.refresh)
        c.view.changed.connect(self._on_view_changed)
        c.sessionReplaced.connect(self.clear_results)

    # ------------------------------------------------------------------ fallbacks / reasons
    def _need_session(self) -> str | None:
        return None if self.controller.session is not None else "Open an audio file first"

    def _need_selection(self) -> str | None:
        r = self._need_session()
        if r:
            return r
        return None if self.controller.session.selection is not None else "Make a selection first"

    def _preview_reason(self) -> str | None:
        c = self.controller
        if c.session is None:
            return "Open an audio file first"
        r = edit_reason(c, "preview")
        # the step sets −6 dB itself, so a 0 dB amount is no reason to disable it
        return None if r and r.startswith("0 dB") else r

    def _apply_action_id(self) -> str:
        s = self.controller.session
        return "module.apply" if s is not None and s.preview is not None else "edit.apply_gain"

    def _open_fallback(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open audio", "", "Audio files (*.wav *.flac *.aif *.aiff *.caf *.w64 *.rf64);;All files (*)")
        if path:
            self.controller.open_audio(path)

    def _fit_selection_fallback(self) -> None:
        s = self.controller.session
        if s is None or s.selection is None:
            return
        b = s.selection.bounds(s.project.duration, s.project.nyquist)
        if b is not None:
            self.controller.view.fit_box(*b)

    # ------------------------------------------------------------------ step 5 / 9 controls
    def adjust_band(self, direction: int) -> None:
        """Widen (+1) or narrow (−1) the selection's frequency edges by the step (selection only, never audio)."""
        s = self.controller.session
        if s is None or s.selection is None:
            return
        df = direction * float(self.band_step.value())
        self.controller.safe(s.set_selection, s.selection.expanded(0.0, df))

    def apply_feather(self) -> None:
        if self._syncing:
            return
        s = self.controller.session
        if s is None or s.selection is None:
            return
        sel = s.selection.with_feather(self.feather_t_spin.value() / 1000.0, self.feather_f_spin.value(), True)
        if sel != s.selection:
            self.controller.safe(s.set_selection, sel)

    def _on_amount(self, v: float) -> None:
        if not self._syncing:
            g = clamp_gain(v)
            if g != self.controller.tools.gain_db:
                self.controller.tools.set(gain_db=g)

    # ------------------------------------------------------------------ state
    def current_step(self) -> int:
        c = self.controller
        s = c.session
        if s is None:
            return 1
        if s.preview is not None:
            return 7
        if s.selection is None:
            return 3
        if has_audio_edit(s):
            return 10
        return 4

    def analysis_range(self) -> AnalysisRange | None:
        c = self.controller
        s = c.session
        if s is None:
            return None
        dur, nyq = s.project.duration, s.project.nyquist
        if s.selection is not None:
            b = s.selection.bounds(dur, nyq)
            if b is None:
                return None
            t0, t1, f0, f1 = b
            rng = AnalysisRange(t0, t1, max(40.0, f0), min(nyq, f1), "selection")
        else:
            v = c.view
            rng = AnalysisRange(max(0.0, v.t0), min(dur, v.t1), max(40.0, v.f_lo), min(nyq, v.f_hi), "view")
        if rng.t1 - rng.t0 > MAX_ANALYSIS_S:
            rng.t1 = rng.t0 + MAX_ANALYSIS_S
            rng.capped = True
        if rng.f_hi <= rng.f_lo:
            rng.f_lo, rng.f_hi = 40.0, nyq
        return rng

    def finder_reason(self) -> str | None:
        c = self.controller
        if c.session is None:
            return "Open an audio file first"
        if c.busy:
            return "Busy – wait for the current operation to finish"
        rng = self.analysis_range()
        if rng is None:
            return "The selection is empty"
        n = int((rng.t1 - rng.t0) * c.session.sample_rate)
        if choose_config(n) is None:
            return "The range is too short to analyse (it needs at least ~25 ms); select a longer stretch"
        return None

    def _on_view_changed(self, what: str) -> None:
        if what == "viewport" and self.controller.session is not None and self.controller.session.selection is None:
            self._update_range_label()

    def _update_range_label(self) -> None:
        rng = self.analysis_range()
        self.range_label.setText("Will analyse: " + (rng.describe() if rng else "nothing (open audio first)"))

    def refresh(self) -> None:
        c = self.controller
        s = c.session
        self._update_range_label()
        r = self.finder_reason()
        self.find_button.setEnabled(r is None)
        self.find_button.setToolTip("Analyse the range and list narrow, persistent peaks (does not change audio)"
                                    + (f"<br><span style='color:#f0a050'>Unavailable: {r}</span>" if r else ""))
        has_sel = s is not None and s.selection is not None
        for w in (self.narrow_button, self.wider_button, self.feather_f_spin, self.feather_t_spin, self.band_step):
            w.setEnabled(has_sel and not c.busy)
        tip = "" if has_sel else "Unavailable: make a selection first"
        for w in (self.narrow_button, self.wider_button):
            w.setToolTip(tip or ("Move both frequency edges " + ("inwards" if w is self.narrow_button else "outwards")
                                 + " by the step (changes the selection only)"))
        self._syncing = True
        try:
            if has_sel:
                self.feather_f_spin.setValue(s.selection.feather_f)
                self.feather_t_spin.setValue(s.selection.feather_t * 1000.0)
            g = clamp_gain(c.tools.gain_db)
            if abs(self.amount_spin.value() - g) > 1e-9:
                self.amount_spin.setValue(g)
        finally:
            self._syncing = False
        self.amount_spin.setEnabled(s is not None)
        cur = self.current_step()
        for n, frame in self.step_frames.items():
            frame.setStyleSheet(
                "QFrame#resonanceStep { border: 1px solid #4fb3ff; border-radius: 5px; background: #1d2a38; }"
                if n == cur else "QFrame#resonanceStep { border: 1px solid #323a47; border-radius: 5px; }")
        self.binder.refresh()
        for row in self.rows:
            self._update_row_buttons(row)

    # ------------------------------------------------------------------ finder
    def find_candidates(self) -> bool:
        """Start the analysis job. Returns False (with a message) if it cannot start."""
        c = self.controller
        r = self.finder_reason()
        if r:
            self.finder_status.setText(r)
            return False
        s = c.session
        rng = self.analysis_range()
        sr = s.sample_rate
        a = max(0, int(math.floor(rng.t0 * sr)))
        b = min(s.project.n_frames, int(math.ceil(rng.t1 * sr)))
        cfg = choose_config(b - a)
        pad = cfg.n_fft
        start, end = max(0, a - pad), min(s.project.n_frames, b + pad)
        # Audible composite (all audible layers at their gains), rendered on the GUI thread so the
        # worker analyses a consistent snapshot.
        mix = s.project.render_mix(start, end)
        lt0, lt1 = (a - start) / sr, (b - start) / sr
        min_prom = float(self.min_prom_spin.value())
        max_w = float(self.max_width_spin.value())
        f_lo, f_hi = rng.f_lo, rng.f_hi

        def job(cancel):
            return suggest_resonances(mix, sr, lt0, lt1, f_lo=f_lo, f_hi=f_hi, cfg=cfg,
                                      min_prominence_db=min_prom, max_width_hz=max_w, max_candidates=12)

        offset = start / sr  # candidate times are relative to the analysed snippet

        def done(cands):
            self.result_offset = offset
            self._show_results(cands, rng, cfg, min_prom)

        def failed(e: BaseException):
            self.finder_status.setText(f"Could not analyse this range: {e}")

        self.finder_status.setText(f"Analysing {rng.describe()} …")
        started = c.run_job(FINDER_JOB_LABEL, job, done, on_error=failed)
        if not started:
            self.finder_status.setText("Busy – try again when the current operation has finished")
        return started

    def _show_results(self, cands: list[ResonanceCandidate], rng: AnalysisRange, cfg: STFTConfig,
                      min_prom: float) -> None:
        sr = self.controller.session.sample_rate if self.controller.session else 48000
        self.result_range = rng
        self.result_config = cfg
        if self.merge_check.isChecked():
            rows = merge_nearby(cands, max(4 * cfg.bin_spacing(sr), 15.0))
        else:
            rows = [CandidateRow(c, c.frequency_hz, c.frequency_hz) for c in cands]
        self.clear_results(keep_status=True)
        self.rows = rows
        for row in rows:
            self._append_row(row)
        where = "in the selection" if rng.source == "selection" else "in the visible range"
        span = _fmt_range(rng.t0, rng.t1, rng.f_lo, rng.f_hi)
        if rows:
            self.finder_status.setText(
                f"{len(rows)} candidate{'s' if len(rows) != 1 else ''} {where} ({span}), analysis resolution "
                f"{cfg.bin_spacing(sr):.1f} Hz. Accept selects one for auditioning; nothing has been changed.")
        else:
            self.finder_status.setText(
                f"No narrow persistent peaks ≥ {min_prom:.1f} dB above the local baseline {where} ({span}). "
                "Try a lower minimum prominence or a longer range.")
        self.refresh()

    def _append_row(self, row: CandidateRow) -> None:
        t = self.results
        i = t.rowCount()
        t.insertRow(i)
        c = row.cand
        freq = f"{c.frequency_hz:.0f} Hz"
        if row.merged > 1:
            freq += f" ({row.merged} peaks, {row.f_lo:.0f}–{row.f_hi:.0f})"
        items = [freq, f"+{c.prominence_db:.1f} dB", f"~{c.width_hz:.0f} Hz", f"{c.persistence * 100:.0f} %"]
        for col, text in enumerate(items):
            it = QTableWidgetItem(text)
            it.setToolTip(c.describe() + (f"; {row.merged} nearby peaks shown as one" if row.merged > 1 else ""))
            t.setItem(i, col, it)
        cell = QWidget()
        h = QHBoxLayout(cell)
        h.setContentsMargins(2, 1, 2, 1)
        h.setSpacing(3)
        acc = QPushButton("Accept")
        rej = QPushButton("Reject")
        acc.setToolTip("Select this band where it rings so you can audition it (does not change audio)")
        rej.setToolTip("Remove this candidate from the list")
        acc.clicked.connect(lambda _=False, r=row: self.accept_candidate(self.rows.index(r)))
        rej.clicked.connect(lambda _=False, r=row: self.reject_candidate(self.rows.index(r)))
        h.addWidget(acc)
        h.addWidget(rej)
        t.setCellWidget(i, 4, cell)
        row.widgets = {"accept": acc, "reject": rej}
        self._update_row_buttons(row)

    def _update_row_buttons(self, row: CandidateRow) -> None:
        acc = row.widgets.get("accept")
        if acc is None:
            return
        ok = self.controller.session is not None and not self.controller.busy
        acc.setEnabled(ok)
        acc.setText("Selected ✓" if row.accepted else "Accept")

    def candidate_band(self, row: CandidateRow) -> tuple[float, float, float]:
        """(f_lo, f_hi, feather_hz) of the selection an Accept creates.

        ``f ± w`` with ``w = max(half the measured width, 2 processing bins)`` (the processing STFT smears a
        steady tone over about ±2 bins), extended to cover merged nearby peaks; the feather is one processing
        bin so the edges are soft but the band stays narrow."""
        s = self.controller.session
        edit_bin = s.edit_config.bin_spacing(s.sample_rate)
        w = max(row.cand.width_hz / 2.0, 2.0 * edit_bin)
        lo = max(0.0, min(row.f_lo, row.cand.frequency_hz) - w)
        hi = min(s.project.nyquist, max(row.f_hi, row.cand.frequency_hz) + w)
        return lo, hi, edit_bin

    def candidate_span(self, row: CandidateRow) -> tuple[float, float]:
        """Time span an Accept selects: where the peak actually rings (from the finder), or the whole
        analysed range when it rings for (almost) all of it."""
        rng = self.result_range
        t0, t1 = rng.t0, rng.t1
        c = row.cand
        if c.t_start is not None and c.t_end is not None:
            off = getattr(self, "result_offset", 0.0)
            a, b = max(t0, c.t_start + off), min(t1, c.t_end + off)
            if b > a and (b - a) < 0.9 * (t1 - t0):
                return a, b
        return t0, t1

    def accept_candidate(self, index: int) -> bool:
        """Replace the selection with the candidate's band over the analysed time span. Audio is not changed."""
        c = self.controller
        s = c.session
        if s is None or self.result_range is None or not (0 <= index < len(self.rows)):
            return False
        row = self.rows[index]
        lo, hi, feather = self.candidate_band(row)
        t0, t1 = self.candidate_span(row)
        sel = Selection.of(RectShape(t0, t1, lo, hi), feather_t=c.tools.feather_t, feather_f=feather,
                           channels=c.tools.channel_scope)
        c.safe(s.set_selection, sel)
        for r in self.rows:
            r.accepted = r is row
            self._update_row_buttons(r)
        self.results.selectRow(index)
        self.finder_status.setText(
            f"Selected {lo:.0f}–{hi:.0f} Hz over {_fmt_range(t0, t1)} (feather {feather:.0f} Hz). "
            "Now audition it (step 4) – nothing has been removed.")
        return True

    def reject_candidate(self, index: int) -> None:
        if 0 <= index < len(self.rows):
            self.rows.pop(index)
            self.results.removeRow(index)

    def clear_results(self, keep_status: bool = False) -> None:
        self.rows = []
        self.results.setRowCount(0)
        if not keep_status:
            self.finder_status.clear()
            self.result_range = None


__all__ = ["ResonancePanel", "AnalysisRange", "CandidateRow", "choose_config", "merge_nearby", "EXPLANATION"]
