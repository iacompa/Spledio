"""UI module tests: 'Reduce selected sound' panel, guided resonance workflow,
icons, command palette, shortcut editor and tutorials.

All audio here is a SYNTHETIC TEST FIXTURE generated in memory (sines plus
seeded noise / a seeded noise-driven resonator). Nothing is recorded material.
Playback uses the deterministic FakeBackend (no audio hardware).
"""

from __future__ import annotations

import gc
import hashlib

import numpy as np
import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QWidget
from scipy.signal import iirpeak, lfilter

from spledio.engine.session import EditorSession
from spledio.playback.engine import FakeBackend
from spledio.selections.geometry import RectShape, time_range
from spledio.selections.selection import Selection
from spledio.ui import icons
from spledio.ui.actions import ActionRegistry, ActionSpec
from spledio.ui.controller import EditorController
from spledio.ui.panels.edit_panel import (
    BOOST_WARNING_TEXT,
    NO_SELECTION_TEXT,
    EditPanel,
    module_apply,
    module_cancel,
    module_preview,
)

SR = 48000
FIXTURE_NAME = "TEST FIXTURE two tones"

ICON_NAMES = (
    "open save export undo redo play pause stop to_start loop hear_selected hear_all hear_remainder hear_removed ab "
    "gain mute delete extract copy_layer preview bypass apply cancel zoom_in zoom_out fit_selection fit_project "
    "reset_view focus layer_new layer_dup layer_delete up down merge time freq rect ellipse lasso polygon sel_brush "
    "transform reduce_brush amplify_brush transfer_brush hand zoom mode_replace mode_add mode_subtract mode_intersect "
    "invert lock solo mute_layer polarity eye commands help"
).split()


# ------------------------------------------------------------------ fixtures (SYNTHETIC)
def two_tones(dur: float = 2.0) -> np.ndarray:
    """SYNTHETIC TEST FIXTURE: 440 Hz + 3 kHz, stereo (right = 0.9 x left)."""
    t = np.arange(int(dur * SR)) / SR
    x = 0.4 * np.sin(2 * np.pi * 440 * t) + 0.3 * np.sin(2 * np.pi * 3000 * t)
    return np.stack([x, 0.9 * x])


def planted_resonance(dur: float = 3.0, f0: float = 2370.0, seed: int = 3) -> np.ndarray:
    """SYNTHETIC TEST FIXTURE: white noise plus a narrow (Q=400) noise-driven resonance at ``f0``."""
    rng = np.random.default_rng(seed)
    n = int(dur * SR)
    noise = 0.05 * rng.standard_normal(n)
    b, a = iirpeak(f0, Q=400, fs=SR)
    ring = lfilter(b, a, rng.standard_normal(n))
    x = noise + ring
    return np.stack([x, 0.95 * x])


def tone_level(sig: np.ndarray, f: float, a: int, b: int) -> float:
    tt = np.arange(a, b) / SR
    return 2 * abs(np.mean(sig[a:b] * np.exp(-2j * np.pi * f * tt)))


def wait_idle(qtbot, ctl, timeout: int = 30000) -> None:
    qtbot.waitUntil(lambda: not ctl.busy, timeout=timeout)
    qtbot.wait(5)


@pytest.fixture
def ctl(qtbot):
    # Collect garbage left by earlier tests *here, on the GUI thread*: a controller is only freed by the
    # cyclic GC, and if that ran on a worker thread its QTimers would be destroyed from the wrong thread.
    gc.collect()
    c = EditorController(playback_backend=FakeBackend())
    yield c
    try:
        wait_idle(qtbot, c)
    finally:
        c.shutdown()
        del c
        gc.collect()


@pytest.fixture
def host(qtbot):
    w = QWidget()
    qtbot.addWidget(w)
    return w


def load(ctl, audio, name=FIXTURE_NAME) -> EditorSession:
    s = EditorSession.from_array(audio, SR, name=name)
    ctl.set_session(s)
    return s


def make_registry(c: EditorController, parent: QWidget) -> ActionRegistry:
    """Stub registry: the action ids the main window registers, wired to the real controller methods."""
    reg = ActionRegistry()
    calls: list[str] = []
    A = reg.add

    def need_session():
        return None if c.session is not None else "Open an audio file first"

    def need_sel():
        return need_session() or (None if c.session.selection is not None else "Make a selection first")

    def fit():
        s = c.session
        b = s.selection.bounds(s.project.duration, s.project.nyquist)
        c.view.fit_box(*b)

    A(ActionSpec("edit.apply_gain", "Apply Gain", "Apply the panel gain to the selection",
                 lambda: c.apply_gain(c.tools.gain_db), "Edit", "Ctrl+G", "gain", need_sel, changes_audio=True,
                 undoable=True))
    for aid, db in (("edit.gain_m3", -3), ("edit.gain_m6", -6), ("edit.gain_m12", -12), ("edit.gain_p3", 3),
                    ("edit.gain_p6", 6)):
        A(ActionSpec(aid, f"Apply {db:+d} dB", f"Immediately apply {db:+d} dB", (lambda d=db: c.apply_gain(d)),
                     "Edit/Quick gain", "", "", need_sel, changes_audio=True, undoable=True))
    A(ActionSpec("edit.gain_reset", "Reset Gain to 0 dB", "Set the panel gain to 0 dB", lambda: c.tools.set(gain_db=0.0),
                 "Edit/Quick gain"))
    A(ActionSpec("edit.mute", "Mute Region", "Mute the selection", c.mute_selection, "Edit", "Ctrl+M", "mute", need_sel,
                 changes_audio=True, undoable=True))
    A(ActionSpec("edit.delete_spectral", "Delete Spectral Content", "Remove the selected content", c.delete_spectral,
                 "Edit", "Delete", "delete", need_sel, changes_audio=True, undoable=True, text_safe=False))
    A(ActionSpec("edit.extract", "Extract to New Layer", "Move the selection to a new layer", lambda: c.extract(False),
                 "Edit", "Ctrl+J", "extract", need_sel, changes_audio=True, undoable=True))
    A(ActionSpec("edit.copy_to_layer", "Copy to New Layer", "Copy the selection to a new layer", lambda: c.extract(True),
                 "Edit", "Ctrl+Shift+J", "copy_layer", need_sel, changes_audio=True, undoable=True))
    A(ActionSpec("module.preview", "Preview", "Render the gain edit, then play it", lambda: module_preview(c),
                 "Edit/Module", "P", "preview", need_sel, text_safe=False))
    A(ActionSpec("module.bypass", "Bypass", "Original / preview", c.toggle_bypass, "Edit/Module", "B", "bypass",
                 text_safe=False))
    A(ActionSpec("module.apply", "Apply Preview", "Commit the preview", lambda: module_apply(c), "Edit/Module",
                 "Ctrl+Return", "apply", need_session, changes_audio=True, undoable=True))
    A(ActionSpec("module.cancel", "Cancel Preview", "Discard the preview", lambda: module_cancel(c), "Edit/Module", "",
                 "cancel"))
    for aid, label, mode, key in (("audition.selected", "Hear Selected Frequencies", "selected", "S"),
                                  ("audition.all_freqs", "Hear All Frequencies", "all_freqs", "Shift+S"),
                                  ("audition.remainder", "Hear Remainder", "remainder", "R"),
                                  ("audition.removed", "Hear What Is Being Removed", "difference", "D")):
        A(ActionSpec(aid, label, label, (lambda m=mode: c.audition_mode(m)), "Transport/Audition", key, "", need_session,
                     text_safe=False))
    A(ActionSpec("audition.ab", "A/B Compare", "Original / processed", c.ab_toggle, "Transport/Audition", "A", "ab",
                 need_session, text_safe=False))
    A(ActionSpec("transport.play_pause", "Play / Pause", "Play or pause", c.play_pause, "Transport", "Space", "play",
                 need_session, text_safe=False))
    A(ActionSpec("transport.loop", "Loop", "Loop playback", c.set_loop_enabled, "Transport", "L", "loop", need_session,
                 checkable=True, checked=lambda: c.loop_enabled, text_safe=False))
    for tid, label, key in (("freq", "Frequency range", "F"), ("rect", "Rectangle", "M"), ("time", "Time range", "T"),
                            ("reduce_brush", "Reduce brush", "Shift+R")):
        A(ActionSpec(f"tool.{tid}", label, f"{label} tool", (lambda on, t=tid: on and c.tools.set(tool=t)), "Tools", key,
                     tid, None, checkable=True, checked=(lambda t=tid: c.tools.tool == t), text_safe=False))
    A(ActionSpec("view.fit_selection", "Fit Selection", "Fit the selection", fit, "View", "Ctrl+Shift+E",
                 "fit_selection", need_sel))
    A(ActionSpec("view.zoom_in", "Zoom In (time)", "Zoom in", lambda: c.view.zoom_time(0.5), "View", "+", "zoom_in",
                 need_session))
    A(ActionSpec("file.open_audio", "Open Audio…", "Open a file", lambda: calls.append("open"), "File", "Ctrl+O", "open"))
    A(ActionSpec("file.export", "Export Audio…", "Export", lambda: calls.append("export"), "File", "Ctrl+E", "export",
                 need_session))
    reg.build(parent, icons.icon_provider)
    reg.calls = calls  # type: ignore[attr-defined]
    return reg


# ------------------------------------------------------------------ edit panel
def test_gain_slider_and_spin_stay_in_sync_and_validate(ctl, host, qtbot):
    load(ctl, two_tones())
    panel = EditPanel(ctl, make_registry(ctl, host))
    qtbot.addWidget(panel)
    # moderate default, never maximum removal
    assert ctl.tools.gain_db == -6.0
    assert panel.gain_spin.value() == -6.0 and panel.gain_slider.value() == -12
    assert panel.boost_label.isHidden()

    panel.gain_spin.setValue(-9.5)
    assert ctl.tools.gain_db == -9.5 and panel.gain_slider.value() == -19

    panel.gain_slider.setValue(30)  # 0.5 dB steps
    assert panel.gain_spin.value() == 15.0 and ctl.tools.gain_db == 15.0
    assert not panel.boost_label.isHidden() and panel.boost_label.text() == BOOST_WARNING_TEXT

    panel.gain_spin.setValue(80)  # out of range -> limited
    assert panel.gain_spin.value() == 36.0 and ctl.tools.gain_db == 36.0 and panel.gain_slider.value() == 72
    panel.gain_slider.setValue(-500)
    assert ctl.tools.gain_db == -36.0 and panel.gain_spin.value() == -36.0

    ctl.tools.set(gain_db=-3.5)  # external change (e.g. the resonance workflow)
    assert panel.gain_spin.value() == -3.5 and panel.gain_slider.value() == -7
    assert panel.boost_label.isHidden()

    # typing: a keystroke that would leave the range is rejected by the validator ("-48" -> "-4")
    le = panel.gain_spin.lineEdit()
    panel.gain_spin.setFocus()
    le.selectAll()
    qtbot.keyClicks(le, "-48")
    qtbot.keyClick(le, Qt.Key_Return)
    assert panel.gain_spin.value() == -4.0 and ctl.tools.gain_db == -4.0
    le.selectAll()
    qtbot.keyClicks(le, "-20")
    qtbot.keyClick(le, Qt.Key_Return)
    assert panel.gain_spin.value() == -20.0 and ctl.tools.gain_db == -20.0 and panel.gain_slider.value() == -40

    qtbot.mouseClick(panel.reset_button, Qt.LeftButton)
    assert ctl.tools.gain_db == 0.0 and panel.gain_slider.value() == 0
    # 0 dB: Preview is disabled and says why
    s = ctl.session
    s.set_selection(Selection.of(RectShape(0.5, 1.5, 2500, 3500)))
    assert not panel.preview_button.isEnabled()
    assert "0 dB changes nothing" in panel.preview_button.toolTip()


def test_scope_label_with_and_without_selection(ctl, host, qtbot):
    panel = EditPanel(ctl, make_registry(ctl, host))
    qtbot.addWidget(panel)
    assert "No audio open" in panel.scope_label.text()
    assert not panel.preview_button.isEnabled() and "Open an audio file" in panel.preview_button.toolTip()

    s = load(ctl, two_tones())
    assert panel.scope_label.text() == NO_SELECTION_TEXT
    for b in (panel.preview_button, panel.mute_button, panel.extract_button, *panel.quick_buttons.values(),
              panel.audition_buttons["selected"]):
        assert not b.isEnabled()
        assert "Make a selection first" in b.toolTip()

    s.set_selection(Selection.of(RectShape(0.5, 1.5, 2500, 3500)))
    assert panel.scope_label.text() == (
        f"Acts on: Layer '{FIXTURE_NAME}' · 0.50–1.50 s, 2500–3500 Hz · L+R linked")
    assert panel.preview_button.isEnabled() and panel.apply_button.isEnabled()
    assert not panel.cancel_button.isEnabled() and "no preview" in panel.cancel_button.toolTip()
    assert not panel.bypass_button.isEnabled() and "Press Preview first" in panel.bypass_button.toolTip()

    ctl.set_channel_scope((0,))
    assert panel.scope_label.text().endswith("· L only")
    ctl.set_channel_scope(None)

    ctl.safe(s.set_layer_props, s.active_layer_id, locked=True)
    qtbot.wait(5)
    assert "locked" in panel.scope_label.text()
    assert not panel.mute_button.isEnabled() and "is locked" in panel.mute_button.toolTip()
    assert panel.copy_button.isEnabled()  # copying reads only
    assert panel.audition_buttons["selected"].isEnabled()


def test_preview_then_apply_is_one_undo_step_with_exact_reduction(ctl, host, qtbot):
    s = load(ctl, two_tones())
    panel = EditPanel(ctl, make_registry(ctl, host))
    qtbot.addWidget(panel)
    orig = s.active_layer.audio.copy()
    s.set_selection(Selection.of(RectShape(0.5, 1.5, 2500, 3500)))

    qtbot.mouseClick(panel.preview_button, Qt.LeftButton)
    assert ctl.busy and panel.status_label.text() == "Preview pending…"
    wait_idle(qtbot, ctl)
    pv = s.preview
    assert pv is not None and pv.gain_db == -6.0
    assert panel.status_label.text().startswith("Preview ready – not applied")
    assert ctl.audition.mode == "preview"  # it played the preview region
    assert np.array_equal(s.active_layer.audio, orig)  # nothing applied yet
    assert panel.bypass_button.isEnabled() and panel.cancel_button.isEnabled()

    qtbot.mouseClick(panel.bypass_button, Qt.LeftButton)  # original <-> preview, nothing changes
    assert ctl.audition.mode == "bypass" and panel.bypass_button.isChecked()
    assert np.array_equal(s.active_layer.audio, orig)

    assert panel.binder.action_id_for(panel.apply_button) == "module.apply"
    qtbot.mouseClick(panel.apply_button, Qt.LeftButton)
    wait_idle(qtbot, ctl)
    assert len(s.history.undo_stack) == 1
    assert s.preview is None
    assert panel.status_label.text().startswith("Applied")
    new = s.active_layer.audio
    # exactly the previewed samples were committed
    assert np.array_equal(new[:, pv.start:pv.end], pv.processed.astype(new.dtype))
    # 3 kHz reduced by 6 dB in the middle of the selection, 440 Hz untouched
    a, b = int(0.75 * SR), int(1.25 * SR)
    for ch in range(2):
        r3k = tone_level(new[ch].astype(np.float64), 3000, a, b) / tone_level(orig[ch].astype(np.float64), 3000, a, b)
        r440 = tone_level(new[ch].astype(np.float64), 440, a, b) / tone_level(orig[ch].astype(np.float64), 440, a, b)
        assert abs(r3k - 10 ** (-6 / 20)) < 0.01
        assert abs(r440 - 1.0) < 0.005
    # samples outside the processed region are bit-identical
    assert np.array_equal(new[:, :pv.start], orig[:, :pv.start])
    assert np.array_equal(new[:, pv.end:], orig[:, pv.end:])
    ctl.undo()
    qtbot.wait(5)
    assert np.array_equal(s.active_layer.audio, orig)
    assert "Undone" in panel.status_label.text()


def test_cancel_leaves_audio_identical(ctl, host, qtbot):
    s = load(ctl, two_tones())
    panel = EditPanel(ctl, make_registry(ctl, host))
    qtbot.addWidget(panel)
    orig = s.active_layer.audio.copy()
    s.set_selection(Selection.of(RectShape(0.5, 1.5, 2500, 3500)))
    panel.set_gain_db(-18.0)
    qtbot.mouseClick(panel.preview_button, Qt.LeftButton)
    wait_idle(qtbot, ctl)
    assert s.preview is not None
    qtbot.mouseClick(panel.cancel_button, Qt.LeftButton)
    qtbot.wait(5)
    assert s.preview is None
    assert np.array_equal(s.active_layer.audio, orig)
    assert s.history.undo_stack == []
    assert "Preview discarded" in panel.status_label.text()
    assert ctl.audition.mode == "mix"


def test_apply_without_preview_and_quick_buttons(ctl, host, qtbot):
    s = load(ctl, two_tones())
    panel = EditPanel(ctl, make_registry(ctl, host))
    qtbot.addWidget(panel)
    orig = s.active_layer.audio.astype(np.float64)
    s.set_selection(Selection.of(RectShape(0.5, 1.5, 2500, 3500)))
    panel.set_gain_db(-12.0)
    assert panel.binder.action_id_for(panel.apply_button) == "edit.apply_gain"
    qtbot.mouseClick(panel.apply_button, Qt.LeftButton)
    wait_idle(qtbot, ctl)
    assert len(s.history.undo_stack) == 1
    a, b = int(0.75 * SR), int(1.25 * SR)
    r = tone_level(s.active_layer.audio[0].astype(np.float64), 3000, a, b) / tone_level(orig[0], 3000, a, b)
    assert abs(r - 10 ** (-12 / 20)) < 0.01
    # quick button applies immediately: one more undo step, a further -3 dB
    qtbot.mouseClick(panel.quick_buttons[-3.0], Qt.LeftButton)
    wait_idle(qtbot, ctl)
    assert len(s.history.undo_stack) == 2
    r = tone_level(s.active_layer.audio[0].astype(np.float64), 3000, a, b) / tone_level(orig[0], 3000, a, b)
    assert abs(r - 10 ** (-15 / 20)) < 0.01
    assert "immediately" in panel.quick_buttons[-3.0].toolTip()
    assert "full length" in panel.delete_button.toolTip()
    assert "+6 dB" in panel.copy_button.toolTip()
    # compare buttons are available now that an edit exists
    assert panel.audition_buttons["removed"].isEnabled() and panel.audition_buttons["ab"].isEnabled()
    qtbot.mouseClick(panel.audition_buttons["removed"], Qt.LeftButton)
    wait_idle(qtbot, ctl)
    assert ctl.audition.mode == "difference"


def test_busy_disables_with_reason(ctl, host, qtbot):
    s = load(ctl, two_tones())
    panel = EditPanel(ctl, make_registry(ctl, host))
    qtbot.addWidget(panel)
    s.set_selection(Selection.of(RectShape(0.5, 1.5, 2500, 3500)))
    qtbot.mouseClick(panel.preview_button, Qt.LeftButton)
    assert ctl.busy
    assert not panel.mute_button.isEnabled() and "Busy" in panel.mute_button.toolTip()
    assert panel.cancel_button.isEnabled()  # can stop a pending preview
    wait_idle(qtbot, ctl)
    assert panel.mute_button.isEnabled()


# ------------------------------------------------------------------ resonance workflow
def test_resonance_finder_finds_planted_resonance_and_accept_only_selects(ctl, host, qtbot):
    from spledio.ui.panels.resonance_panel import ResonancePanel

    s = load(ctl, planted_resonance(), name="TEST FIXTURE planted resonance")
    panel = ResonancePanel(ctl, make_registry(ctl, host))
    qtbot.addWidget(panel)
    orig = s.active_layer.audio.copy()

    # no selection: the visible view range is analysed, and the panel says so
    assert "visible view range" in panel.range_label.text()
    s.set_selection(Selection.of(time_range(0.5, 2.5)))
    assert "selection's time span" in panel.range_label.text()

    assert panel.find_candidates()
    wait_idle(qtbot, ctl)
    assert panel.rows, panel.finder_status.text()
    assert "in the selection" in panel.finder_status.text()
    top = panel.rows[0].cand
    bin_hz = SR / panel.result_config.n_fft
    assert abs(top.frequency_hz - 2370.0) <= bin_hz
    assert top.prominence_db > 10 and top.persistence > 0.8
    assert panel.results.rowCount() == len(panel.rows)

    qtbot.mouseClick(panel.rows[0].widgets["accept"], Qt.LeftButton)
    sel = s.selection
    shape = sel.ops[0].shape
    assert isinstance(shape, RectShape) and len(sel.ops) == 1
    assert shape.f0 < 2370.0 < shape.f1 and shape.f1 - shape.f0 < 200
    assert abs(shape.t0 - 0.5) < 1e-6 and abs(shape.t1 - 2.5) < 1e-6
    assert sel.feather_f > 0
    # accepting never edits audio
    assert np.array_equal(s.active_layer.audio, orig)
    assert s.history.undo_stack == [] and s.preview is None

    n = len(panel.rows)
    panel.results.setCurrentCell(0, 0)
    qtbot.mouseClick(panel.rows[-1].widgets["reject"], Qt.LeftButton)
    assert len(panel.rows) == n - 1 and panel.results.rowCount() == n - 1

    # visible-range analysis when nothing is selected
    s.clear_selection()
    ctl.view.set_time_range(0.0, 3.0)
    assert panel.find_candidates()
    wait_idle(qtbot, ctl)
    assert "in the visible range" in panel.finder_status.text()
    assert any(abs(r.cand.frequency_hz - 2370.0) <= bin_hz for r in panel.rows)


def test_resonance_steps_run_real_actions(ctl, host, qtbot):
    from spledio.ui.panels.resonance_panel import ResonancePanel

    reg = make_registry(ctl, host)
    s = load(ctl, two_tones())
    panel = ResonancePanel(ctl, reg)
    qtbot.addWidget(panel)
    assert panel.current_step() == 3
    btn_freq, btn_rect, btn_brush = panel.step_buttons[3]
    ctl.tools.set(tool="hand")
    qtbot.mouseClick(btn_freq, Qt.LeftButton)
    assert ctl.tools.tool == "freq"
    qtbot.mouseClick(btn_brush, Qt.LeftButton)
    assert ctl.tools.tool == "reduce_brush"
    qtbot.mouseClick(btn_rect, Qt.LeftButton)
    assert ctl.tools.tool == "rect"
    # step 4 needs a selection
    assert not panel.step_buttons[4][0].isEnabled()
    s.set_selection(Selection.of(RectShape(0.5, 1.5, 2800, 3200)))
    assert panel.current_step() == 4
    qtbot.mouseClick(panel.step_buttons[4][0], Qt.LeftButton)
    wait_idle(qtbot, ctl)
    assert ctl.audition.mode == "selected"
    # step 5: feather / bounds change the selection only
    panel.feather_f_spin.setValue(20.0)
    assert s.selection.feather_f == 20.0
    panel.adjust_band(+1)
    assert s.selection.ops[0].shape.f0 == 2790.0 and s.selection.ops[0].shape.f1 == 3210.0
    # step 6 sets the moderate amount and previews
    ctl.tools.set(gain_db=-1.0)
    qtbot.mouseClick(panel.step_buttons[6][0], Qt.LeftButton)
    wait_idle(qtbot, ctl)
    assert ctl.tools.gain_db == -6.0 and s.preview is not None and s.preview.gain_db == -6.0
    assert panel.current_step() == 7
    assert panel.amount_spin.value() == -6.0
    # step 7 A/B and step 8 difference use the pending preview
    qtbot.mouseClick(panel.step_buttons[7][0], Qt.LeftButton)
    assert ctl.audition.mode == "ab_a"
    qtbot.mouseClick(panel.step_buttons[8][0], Qt.LeftButton)
    wait_idle(qtbot, ctl)
    assert ctl.audition.mode == "difference"
    # step 9: refine and apply (commits the preview: one undo step)
    qtbot.mouseClick(panel.step_buttons[9][1], Qt.LeftButton)
    wait_idle(qtbot, ctl)
    assert len(s.history.undo_stack) == 1 and panel.current_step() == 10
    qtbot.mouseClick(panel.step_buttons[10][0], Qt.LeftButton)
    assert reg.calls == ["export"]
    qtbot.mouseClick(panel.step_buttons[1][0], Qt.LeftButton)
    assert reg.calls == ["export", "open"]


# ------------------------------------------------------------------ icons
def test_every_icon_name_is_drawn_and_distinct(qapp):
    seen = {}
    for name in ICON_NAMES:
        ic = icons.icon(name)
        assert ic is not None and not ic.isNull(), name
        pm = ic.pixmap(32, 32)
        assert not pm.isNull()
        img = pm.toImage()
        digest = hashlib.sha1(bytes(img.constBits())).hexdigest()
        assert digest not in seen, f"{name} looks identical to {seen.get(digest)}"
        seen[digest] = name
        # transparent background: the corner pixel is fully transparent
        assert img.pixelColor(0, 0).alpha() == 0
    assert icons.icon("does_not_exist") is None
    assert icons.icon("open") is icons.icon("open")  # cached
    assert set(ICON_NAMES) <= set(icons.ICON_NAMES)
    ai = icons.app_icon()
    assert ai is not None  # empty QIcon if the artwork is missing, never an exception


# ------------------------------------------------------------------ command palette
def test_command_palette_filters_and_triggers(ctl, host, qtbot):
    from spledio.ui.command_palette import CommandPalette, match_score

    reg = make_registry(ctl, host)
    pal = CommandPalette(reg)
    qtbot.addWidget(pal)
    total = len(pal.visible_ids())
    assert total == len(reg.specs)
    # nothing open: commands that need audio are listed greyed with the reason
    item = pal.list.topLevelItem(pal.visible_ids().index("view.zoom_in"))
    assert "unavailable: Open an audio file first" in item.text(0)

    qtbot.keyClicks(pal.filter_edit, "freq")
    ids = pal.visible_ids()
    assert "tool.freq" in ids and len(ids) < total
    assert ids[0] == "tool.freq"
    ctl.tools.set(tool="hand")
    qtbot.keyClick(pal.filter_edit, Qt.Key_Return)
    assert pal.last_run == "tool.freq" and ctl.tools.tool == "freq"

    # fuzzy subsequence and multi-word matching
    pal2 = CommandPalette(reg)
    qtbot.addWidget(pal2)
    pal2.set_filter("hr sel")
    assert pal2.visible_ids()[0] == "audition.selected"
    pal2.set_filter("zzzz")
    assert pal2.visible_ids() == []
    entry = next(e for e in pal2.entries if e.id == "edit.mute")
    assert match_score("mute", entry) > match_score("mte", entry)

    # a disabled command is not run
    load(ctl, two_tones())
    pal3 = CommandPalette(reg)
    qtbot.addWidget(pal3)
    pal3.set_filter("fit selection")
    assert pal3.select_id("view.fit_selection")
    assert pal3.run_current() is False and "Make a selection first" in pal3.info.text()
    # arrow keys move through the list while typing
    pal3.set_filter("hear")
    first = pal3.current_id()
    qtbot.keyClick(pal3.filter_edit, Qt.Key_Down)
    assert pal3.current_id() != first
    # Esc closes without running anything
    from PySide6.QtWidgets import QDialog

    pal3.show()
    qtbot.keyClick(pal3.filter_edit, Qt.Key_Escape)
    assert pal3.result() == QDialog.Rejected and not pal3.isVisible() and pal3.last_run is None


# ------------------------------------------------------------------ shortcuts
def test_shortcuts_dialog_conflicts_persistence_and_reset(ctl, host, qtbot, tmp_path):
    from spledio.ui.shortcuts_dialog import ShortcutsDialog, default_shortcuts, load_custom_shortcuts

    settings = QSettings(str(tmp_path / "shortcuts.ini"), QSettings.IniFormat)
    reg = make_registry(ctl, host)
    dlg = ShortcutsDialog(reg, None, settings)
    qtbot.addWidget(dlg)
    assert default_shortcuts(reg)["edit.mute"] == ("Ctrl+M",)

    ok, msg = dlg.assign("edit.mute", "Space")
    assert not ok and "Play / Pause" in msg
    assert [s.toString() for s in reg["edit.mute"].shortcuts()] == ["Ctrl+M"]

    # editing in the table: the conflicting key is refused and the editor reverts
    ed = dlg.editors["edit.mute"]
    ed.setKeySequence(QKeySequence("Ctrl+E"))
    assert "Export Audio" in dlg.message.text()
    assert ed.keySequence().toString() == "Ctrl+M"

    ok, msg = dlg.assign("edit.mute", "Ctrl+Alt+M")
    assert ok and not msg.startswith("Warning")
    assert [s.toString() for s in reg["edit.mute"].shortcuts()] == ["Ctrl+Alt+M"]
    assert reg.specs["edit.mute"].shortcut == "Ctrl+Alt+M"  # tooltips/palette follow
    assert default_shortcuts(reg)["edit.mute"] == ("Ctrl+M",)  # the default is remembered

    # a plain key on an audio-changing command is allowed but warned about
    ok, msg = dlg.assign("edit.extract", "X")
    assert ok and msg.startswith("Warning")
    assert reg.specs["edit.extract"].text_safe is False

    # persisted: a fresh registry picks the overrides up at startup
    host2 = QWidget()
    qtbot.addWidget(host2)
    reg2 = make_registry(ctl, host2)
    assert load_custom_shortcuts(reg2, settings) == []
    assert [s.toString() for s in reg2["edit.mute"].shortcuts()] == ["Ctrl+Alt+M"]
    assert [s.toString() for s in reg2["edit.extract"].shortcuts()] == ["X"]

    dlg.reset_all_to_defaults()
    assert [s.toString() for s in reg["edit.mute"].shortcuts()] == ["Ctrl+M"]
    assert reg.specs["edit.extract"].text_safe is True
    settings.beginGroup("shortcuts")
    assert settings.childKeys() == []
    settings.endGroup()

    # a saved override that now collides with another command is skipped, not applied
    settings.setValue("shortcuts/edit.mute", '["Space"]')
    host3 = QWidget()
    qtbot.addWidget(host3)
    reg3 = make_registry(ctl, host3)
    warnings = load_custom_shortcuts(reg3, settings)
    assert warnings and "Mute Region" in warnings[0]
    assert [s.toString() for s in reg3["edit.mute"].shortcuts()] == ["Ctrl+M"]


# ------------------------------------------------------------------ tutorials
def test_tutorials_have_concrete_steps_and_working_buttons(ctl, host, qtbot):
    from spledio.ui.tutorials import TUTORIALS, TutorialsDialog, render_step_text

    reg = make_registry(ctl, host)
    dlg = TutorialsDialog(None, ctl, reg)
    qtbot.addWidget(dlg)
    titles = [t.title for t in TUTORIALS]
    assert titles == ["Turn any part up or down (quickest)", "Reduce a ringing resonance", "Remove a narrow whistle",
                      "Extract a component to a layer", "Reduce a click without shortening audio",
                      "Hear the difference signal", "Export safely"]
    for t in TUTORIALS:
        assert 5 <= len(t.steps) <= 8, t.title
    # command names and shortcuts come from the registry
    resonance = next(t for t in TUTORIALS if t.id == "resonance")
    text = render_step_text(reg, resonance.steps[3].text)
    assert "Hear Selected Frequencies" in text and "(S)" in text
    click = next(t for t in TUTORIALS if t.id == "click")
    assert "no automatic de-click" in click.limits

    dlg.list.setCurrentRow(1)
    btn = dlg.button_for_action("tool.rect")
    assert btn is not None and btn.isEnabled()
    ctl.tools.set(tool="hand")
    qtbot.mouseClick(btn, Qt.LeftButton)
    assert ctl.tools.tool == "rect"
    # a step whose command needs audio is disabled with the reason
    assert not dlg.button_for_action("audition.selected").isEnabled()
    assert "Open an audio file first" in dlg.button_for_action("audition.selected").toolTip()

    # a step whose command is not registered in this window is disabled, not faked
    dlg.list.setCurrentRow(6)
    save_btn = dlg.button_for_action("file.save")
    assert save_btn is not None and not save_btn.isEnabled()
    assert "not available" in save_btn.toolTip()
    exp = dlg.button_for_action("file.export")
    load(ctl, two_tones())
    dlg.refresh()
    qtbot.mouseClick(exp, Qt.LeftButton)
    assert reg.calls == ["export"]

    # exec() does not block: the tutorials stay open beside the editor
    assert dlg.exec() == 0 and dlg.isVisible()
    dlg.close()
