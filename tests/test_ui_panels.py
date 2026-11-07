"""Dock panel tests (Layers, Selection, Display, History) against the real
EditorController / EditorSession with a fake playback backend.

The audio is a SYNTHETIC stereo test signal generated in memory.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget

from spledio.dsp.stft import STFTConfig
from spledio.engine.session import EditorSession
from spledio.playback.engine import FakeBackend
from spledio.selections.geometry import INF_F, EllipseShape, RectShape
from spledio.selections.selection import Selection, SelectionOp
from spledio.ui.actions import ActionRegistry, ActionSpec
from spledio.ui.controller import EditorController
from spledio.ui.panels.display_panel import DisplayPanel
from spledio.ui.panels.history_panel import HistoryPanel
from spledio.ui.panels.layers_panel import LayersPanel
from spledio.ui.panels.selection_panel import SelectionPanel

SR = 48000


def synthetic_stereo(seconds: float = 1.0) -> np.ndarray:
    """SYNTHETIC: different tones per channel."""
    t = np.arange(int(seconds * SR)) / SR
    left = 0.3 * np.sin(2 * np.pi * 440 * t) + 0.05 * np.sin(2 * np.pi * 3000 * t)
    right = 0.25 * np.sin(2 * np.pi * 660 * t)
    return np.stack([left, right])


@pytest.fixture
def ctl(qtbot):
    c = EditorController(playback_backend=FakeBackend())
    c.set_session(EditorSession.from_array(synthetic_stereo(), SR, name="Tone"))
    yield c
    c.shutdown()


@pytest.fixture
def host(qtbot):
    w = QWidget()
    qtbot.addWidget(w)
    return w


MODES = ("replace", "add", "subtract", "intersect")


@pytest.fixture
def actions(ctl, host):
    """Registry with stub specs (real handlers) for every id the panels bind; records calls."""
    calls: list[str] = []
    reg = ActionRegistry()
    s = lambda: ctl.session  # noqa: E731
    t = ctl.tools

    def grow(dt=0.0, df=0.0):
        s().set_selection(s().selection.expanded(dt=dt, df=df))

    def move(delta):
        idx = s().project.index_of(s().active_layer_id)
        ctl.safe(s().move_layer, s().active_layer_id, idx + delta)

    handlers = {
        "select.all": lambda: s().select_all(),
        "select.none": lambda: s().clear_selection(),
        "select.reselect": lambda: s().reselect(),
        "select.invert": lambda: s().invert_selection(),
        "select.expand_time": lambda: grow(dt=t.expand_step_s),
        "select.contract_time": lambda: grow(dt=-t.expand_step_s),
        "select.expand_freq": lambda: grow(df=t.expand_step_hz),
        "select.contract_freq": lambda: grow(df=-t.expand_step_hz),
        "layer.new": lambda: ctl.safe(s().add_empty_layer),
        "layer.duplicate": lambda: ctl.safe(s().duplicate_layer, s().active_layer_id),
        "layer.delete": lambda: ctl.safe(s().delete_layer, s().active_layer_id),
        "layer.up": lambda: move(-1),
        "layer.down": lambda: move(+1),
        "view.fit_project": lambda: ctl.view.fit_project(),
        "view.reset": lambda: ctl.view.reset(),
        "edit.undo": ctl.undo,
        "edit.redo": ctl.redo,
    }
    for aid, fn in handlers.items():
        reg.add(ActionSpec(id=aid, label=aid, tooltip=f"stub {aid}", menu="Test",
                           handler=lambda fn=fn, aid=aid: (calls.append(aid), fn())))
    for mode in MODES:
        def on_mode(on, mode=mode):
            calls.append(f"select.mode_{mode}")
            if on:
                t.set(selection_mode=mode)
        reg.add(ActionSpec(id=f"select.mode_{mode}", label=mode, tooltip=mode, menu="Test", handler=on_mode,
                           checkable=True))
    reg.build(host)
    reg.calls = calls  # type: ignore[attr-defined]
    return reg


def undo_len(ctl) -> int:
    return len(ctl.session.history.undo_stack)


def click(qtbot, widget):
    qtbot.mouseClick(widget, Qt.MouseButton.LeftButton)


def activate(combo, data):
    idx = combo.findData(data)
    assert idx >= 0, data
    combo.setCurrentIndex(idx)
    combo.activated.emit(idx)


# ---------------------------------------------------------------- no session
def test_panels_disabled_without_session(qtbot):
    c = EditorController(playback_backend=FakeBackend())
    try:
        reg = ActionRegistry()
        panels = [P(c, reg) for P in (LayersPanel, SelectionPanel, DisplayPanel, HistoryPanel)]
        for p in panels:
            qtbot.addWidget(p)
            assert p.hint.isVisibleTo(p)
            assert "Open an audio file to begin" in p.hint.text()
            assert not p.body.isEnabled()
        c.set_session(EditorSession.from_array(synthetic_stereo(0.5), SR))
        for p in panels:
            assert not p.hint.isVisibleTo(p)
            assert p.body.isEnabled()
    finally:
        c.shutdown()


# ---------------------------------------------------------------- layers
def test_layer_toggles_and_gain_one_undo_entry_each(ctl, actions, qtbot):
    s = ctl.session
    s.add_empty_layer("Second")
    p = LayersPanel(ctl, actions)
    qtbot.addWidget(p)
    layer = s.project.layers[0]
    row = p.row(layer.id)
    audio_before = layer.audio.copy()
    for attr, prop in (("btn_mute", "mute"), ("btn_solo", "solo"), ("btn_lock", "locked"),
                       ("btn_polarity", "polarity_inverted"), ("btn_visible", "visible")):
        before, n = getattr(layer, prop), undo_len(ctl)
        click(qtbot, getattr(row, attr))
        assert getattr(layer, prop) is (not before), prop
        assert undo_len(ctl) == n + 1, prop
        assert getattr(row, attr).isChecked() == getattr(layer, prop)

    n = undo_len(ctl)
    row.gain.setFocus()
    row.gain.selectAll()
    qtbot.keyClicks(row.gain, "-6.5")
    qtbot.keyClick(row.gain, Qt.Key.Key_Return)
    assert layer.gain_db == pytest.approx(-6.5)
    assert undo_len(ctl) == n + 1
    row.gain.editingFinished.emit()  # focus leaving the field again must not add a second entry
    assert undo_len(ctl) == n + 1
    assert np.array_equal(layer.audio, audio_before)  # layer properties never touch the samples

    # undo while the field still has focus: it must show the restored value and
    # leaving the field afterwards must not silently re-apply the undone gain
    ctl.undo()
    assert layer.gain_db == 0.0
    assert row.gain.value() == 0.0
    n = undo_len(ctl)
    row.gain.editingFinished.emit()
    assert layer.gain_db == 0.0 and undo_len(ctl) == n

    # uncommitted typing is never overwritten by an unrelated refresh
    row.gain.selectAll()
    qtbot.keyClicks(row.gain, "3")
    assert row.gain.is_typing()
    ctl.session.set_layer_props(s.project.layers[1].id, mute=True)  # props refresh of all rows
    assert row.gain.text().startswith("3")


def test_solo_mute_rule_is_explained(ctl, actions, qtbot):
    p = LayersPanel(ctl, actions)
    qtbot.addWidget(p)
    row = p.row(ctl.session.project.layers[0].id)
    assert "Solo does not override mute" in row.btn_solo.toolTip()
    assert "Solo does not override mute" in row.btn_mute.toolTip()
    assert "never affects" in row.btn_visible.toolTip()


def test_layer_rename(ctl, actions, qtbot):
    s = ctl.session
    p = LayersPanel(ctl, actions)
    qtbot.addWidget(p)
    layer = s.project.layers[0]
    row = p.row(layer.id)
    n = undo_len(ctl)
    qtbot.mouseDClick(row.name_label, Qt.MouseButton.LeftButton)
    assert row.renaming
    row.name_edit.setText("Lead vocal")
    qtbot.keyClick(row.name_edit, Qt.Key.Key_Return)
    assert layer.name == "Lead vocal"
    assert undo_len(ctl) == n + 1
    assert not row.renaming
    assert row.name_label.full_text().startswith("Lead vocal")
    assert "Lead vocal" in p.banner.text()
    # Esc cancels without an undo entry
    row.begin_rename()
    row.name_edit.setText("zzz")
    qtbot.keyClick(row.name_edit, Qt.Key.Key_Escape)
    assert layer.name == "Lead vocal" and undo_len(ctl) == n + 1 and not row.renaming


def test_active_layer_switching_and_banner(ctl, actions, qtbot):
    s = ctl.session
    s.add_empty_layer("Second")
    first, second = s.project.layers
    p = LayersPanel(ctl, actions)
    qtbot.addWidget(p)
    p.show()
    assert s.active_layer_id == second.id
    assert p.row(second.id).active and not p.row(first.id).active
    assert "Second" in p.banner.text() and "L+R linked" in p.banner.text()

    p.list.setCurrentRow(0)
    assert s.active_layer_id == first.id
    assert p.row(first.id).active and p.row(first.id).badge.isVisibleTo(p)
    assert "Tone" in p.banner.text()

    # clicking on the row (its name label) makes it active too
    qtbot.mouseClick(p.row(second.id).name_label, Qt.MouseButton.LeftButton)
    assert s.active_layer_id == second.id
    # switching the active layer is not an undoable edit
    assert undo_len(ctl) == 1


def test_filter_hides_rows(ctl, actions, qtbot):
    s = ctl.session
    s.add_empty_layer("Drums")
    s.add_empty_layer("Bass")
    p = LayersPanel(ctl, actions)
    qtbot.addWidget(p)
    drums = s.project.layers[1].id
    p.filter_edit.setText("dR")
    assert p.visible_layer_ids() == [drums]
    assert p.item(s.project.layers[0].id).isHidden()
    assert "1 of 3" in p.count_label.text()
    p.filter_edit.clear()
    assert len(p.visible_layer_ids()) == 3


def test_merge_two_selected_layers(ctl, actions, qtbot):
    s = ctl.session
    tone = s.project.layers[0]
    ctl.safe(s.duplicate_layer, tone.id)
    a, b = s.project.layers
    s.set_layer_props(b.id, gain_db=-6.0)
    s.set_layer_props(a.id, mute=True)  # merge ignores mute/solo
    expected = (a.audio.astype(np.float64) + 10 ** (-6 / 20) * b.audio.astype(np.float64)).astype(np.float32)
    p = LayersPanel(ctl, actions)
    qtbot.addWidget(p)
    assert not p.btn_merge.isEnabled()
    assert "ignores mute/solo" in p.btn_merge.toolTip()
    p.item(a.id).setSelected(True)
    p.item(b.id).setSelected(True)
    assert p.selected_layer_ids() == [a.id, b.id]
    assert p.btn_merge.isEnabled()
    n = undo_len(ctl)
    click(qtbot, p.btn_merge)
    assert len(s.project.layers) == 1
    merged = s.project.layers[0]
    assert merged.name == "Merged"
    np.testing.assert_allclose(merged.audio, expected, atol=1e-7)
    assert undo_len(ctl) == n + 1
    assert p.list.count() == 1
    ctl.undo()
    assert [l.id for l in s.project.layers] == [a.id, b.id]
    assert p.list.count() == 2


def test_layer_buttons_use_registry_actions(ctl, actions, qtbot):
    s = ctl.session
    p = LayersPanel(ctl, actions)
    qtbot.addWidget(p)
    assert p.btn_new.bound and p.btn_up.bound
    click(qtbot, p.btn_new)
    assert "layer.new" in actions.calls and len(s.project.layers) == 2
    new_id = s.active_layer_id
    assert s.project.index_of(new_id) == 1
    click(qtbot, p.btn_up)
    assert "layer.up" in actions.calls and s.project.index_of(new_id) == 0
    assert not p.btn_up.isEnabled()  # now the top row
    click(qtbot, p.btn_duplicate)
    assert "layer.duplicate" in actions.calls and len(s.project.layers) == 3
    click(qtbot, p.btn_delete)
    assert "layer.delete" in actions.calls and len(s.project.layers) == 2
    assert p.list.count() == 2


def test_layer_buttons_fallback_without_actions(ctl, qtbot):
    s = ctl.session
    p = LayersPanel(ctl, ActionRegistry())
    qtbot.addWidget(p)
    assert not p.btn_new.bound
    click(qtbot, p.btn_new)
    assert len(s.project.layers) == 2
    click(qtbot, p.btn_up)
    assert s.project.layers[0].id == s.active_layer_id
    click(qtbot, p.btn_down)
    assert s.project.layers[1].id == s.active_layer_id
    click(qtbot, p.btn_delete)
    assert len(s.project.layers) == 1
    assert not p.btn_delete.isEnabled()  # the last layer cannot be deleted


def test_export_selected_emits_layer_ids(ctl, actions, qtbot):
    s = ctl.session
    s.add_empty_layer("Second")
    p = LayersPanel(ctl, actions)
    qtbot.addWidget(p)
    ids = [l.id for l in s.project.layers]
    for i in ids:
        p.item(i).setSelected(True)
    with qtbot.waitSignal(ctl.requestExportLayers, timeout=1000) as blocker:
        click(qtbot, p.btn_export)
    assert blocker.args == [ids]


def test_transfer_target_and_amount(ctl, actions, qtbot):
    s = ctl.session
    tone = s.project.layers[0]
    s.add_empty_layer("Target")
    p = LayersPanel(ctl, actions)
    qtbot.addWidget(p)
    data = [p.transfer_combo.itemData(i) for i in range(p.transfer_combo.count())]
    assert data == [None, tone.id]  # the active layer is not offered
    activate(p.transfer_combo, tone.id)
    assert ctl.tools.transfer_target == tone.id
    p.transfer_amount.setValue(40)
    assert ctl.tools.transfer_amount == pytest.approx(0.4)
    s.set_active_layer(tone.id)  # target would equal the source: cleared
    assert ctl.tools.transfer_target is None


# ---------------------------------------------------------------- selection
def test_selection_numeric_apply_creates_rect(ctl, actions, qtbot):
    s = ctl.session
    s.set_selection(Selection.of(EllipseShape(0.5, 1000.0, 0.1, 200.0), feather_t=0.02, feather_f=30.0,
                                 channels=(1,)))
    p = SelectionPanel(ctl, actions)
    qtbot.addWidget(p)
    assert "bounding box" in p.bounds_note.text().lower()
    assert p.t0.value() == pytest.approx(0.4) and p.f1.value() == pytest.approx(1200.0)
    p.t0.setValue(0.25)
    p.t1.setValue(0.75)
    p.f0.setValue(500.0)
    p.f1.setValue(2000.0)
    click(qtbot, p.btn_apply)
    sel = s.selection
    assert sel.ops == (SelectionOp("replace", RectShape(0.25, 0.75, 500.0, 2000.0)),)
    assert (sel.feather_t, sel.feather_f, sel.channels) == (0.02, 30.0, (1,))
    assert "Rectangle" in p.bounds_note.text()
    assert "0.250-0.750 s, 500-2000 Hz" in p.desc_label.text()

    # top of the range = all frequencies (a time range; exact sample path once frequency feather is 0)
    p.f0.setValue(0.0)
    p.f1.setValue(p.f1.maximum())
    click(qtbot, p.btn_apply)
    shape = s.selection.ops[0].shape
    assert shape.f1 == INF_F and shape.full_band
    assert "all frequencies" in p.desc_label.text()
    p.feather_f.setValue(0.0)
    assert s.selection.is_full_band_time_range()

    # invalid bounds change nothing
    before = s.selection
    p.t1.setValue(0.1)
    click(qtbot, p.btn_apply)
    assert s.selection == before
    assert "nothing was changed" in p.bounds_note.text()


def test_selection_apply_without_selection_uses_tool_defaults(ctl, actions, qtbot):
    s = ctl.session
    ctl.tools.set(feather_t=0.005, feather_f=12.0, channel_scope=(0,))
    p = SelectionPanel(ctl, actions)
    qtbot.addWidget(p)
    p.t0.setValue(0.1)
    p.t1.setValue(0.2)
    p.f0.setValue(100.0)
    p.f1.setValue(900.0)
    qtbot.keyClick(p.f1, Qt.Key.Key_Return)  # Enter applies too
    sel = s.selection
    assert sel.ops[0].shape == RectShape(0.1, 0.2, 100.0, 900.0)
    assert (sel.feather_t, sel.feather_f, sel.channels) == (0.005, 12.0, (0,))


def test_feather_change_keeps_geometry(ctl, actions, qtbot):
    s = ctl.session
    s.set_selection(Selection.of(RectShape(0.2, 0.6, 300.0, 3000.0)))
    ops = s.selection.ops
    p = SelectionPanel(ctl, actions)
    qtbot.addWidget(p)
    p.feather_t.setValue(20.0)
    assert s.selection.ops == ops and s.selection.feather_t == pytest.approx(0.02)
    assert ctl.tools.feather_t == pytest.approx(0.02)
    p.feather_f.setValue(80.0)
    assert s.selection.ops == ops and s.selection.feather_f == pytest.approx(80.0)
    assert ctl.tools.feather_f == pytest.approx(80.0)
    p.feather_enabled.setChecked(False)
    assert s.selection.ops == ops and not s.selection.feather_enabled
    assert s.selection.feather_t == pytest.approx(0.02)  # amounts kept while disabled
    assert s.selection.effective_feather == (0.0, 0.0)


def test_channel_scope_combo_updates_tools_and_selection(ctl, actions, qtbot):
    s = ctl.session
    s.set_selection(Selection.of(RectShape(0.2, 0.6, 300.0, 3000.0)))
    p = SelectionPanel(ctl, actions)
    lp = LayersPanel(ctl, actions)
    qtbot.addWidget(p)
    qtbot.addWidget(lp)
    texts = [p.scope_combo.itemText(i) for i in range(p.scope_combo.count())]
    assert texts == ["All channels (linked)", "Left only", "Right only"]
    assert not p.scope_warning.isVisibleTo(p)
    idx = p.scope_combo.findText("Left only")
    p.scope_combo.setCurrentIndex(idx)
    p.scope_combo.activated.emit(idx)
    assert ctl.tools.channel_scope == (0,)
    assert s.selection.channels == (0,)
    assert p.scope_warning.isVisibleTo(p)
    assert "only the Left channel" in p.scope_warning.text() and "stereo image" in p.scope_warning.text()
    assert "L only" in lp.banner.text()
    idx = p.scope_combo.findText("All channels (linked)")
    p.scope_combo.setCurrentIndex(idx)
    p.scope_combo.activated.emit(idx)
    assert ctl.tools.channel_scope is None and s.selection.channels is None
    assert not p.scope_warning.isVisibleTo(p)


def test_mono_offers_only_linked_scope(qtbot):
    c = EditorController(playback_backend=FakeBackend())
    try:
        c.set_session(EditorSession.from_array(synthetic_stereo(0.5)[:1], SR))
        p = SelectionPanel(c, ActionRegistry())
        qtbot.addWidget(p)
        assert p.scope_combo.count() == 1 and not p.scope_combo.isEnabled()
    finally:
        c.shutdown()


def test_mode_buttons_and_commands_use_actions(ctl, actions, qtbot):
    s = ctl.session
    p = SelectionPanel(ctl, actions)
    qtbot.addWidget(p)
    assert p.mode_buttons["replace"].isChecked()
    click(qtbot, p.mode_buttons["add"])
    assert "select.mode_add" in actions.calls and ctl.tools.selection_mode == "add"
    assert p.mode_buttons["add"].isChecked() and not p.mode_buttons["replace"].isChecked()
    ctl.tools.set(selection_mode="intersect")  # e.g. from the toolbar
    assert p.mode_buttons["intersect"].isChecked()
    assert "Shift+Alt" in p.mode_buttons["intersect"].toolTip()

    click(qtbot, p.btn_all)
    assert "select.all" in actions.calls and s.selection is not None
    everything = s.selection
    click(qtbot, p.btn_none)
    assert s.selection is None and "Nothing selected" in p.desc_label.text()
    click(qtbot, p.btn_reselect)
    assert s.selection == everything
    click(qtbot, p.btn_invert)
    assert s.selection.ops[-1].mode == "invert"
    assert {"select.none", "select.reselect", "select.invert"} <= set(actions.calls)


def test_mode_buttons_fallback_without_actions(ctl, qtbot):
    p = SelectionPanel(ctl, ActionRegistry())
    qtbot.addWidget(p)
    click(qtbot, p.mode_buttons["subtract"])
    assert ctl.tools.selection_mode == "subtract"


def test_expand_steps_stored_on_tools(ctl, actions, qtbot):
    s = ctl.session
    p = SelectionPanel(ctl, actions)
    qtbot.addWidget(p)
    assert ctl.tools.expand_step_s == pytest.approx(0.010) and ctl.tools.expand_step_hz == pytest.approx(50.0)
    p.step_t.setValue(25.0)
    p.step_f.setValue(100.0)
    assert ctl.tools.expand_step_s == pytest.approx(0.025) and ctl.tools.expand_step_hz == pytest.approx(100.0)
    s.set_selection(Selection.of(RectShape(0.2, 0.6, 300.0, 3000.0)))
    click(qtbot, p.btn_exp_t)
    assert "select.expand_time" in actions.calls
    r = s.selection.ops[0].shape
    assert (r.t0, r.t1) == (pytest.approx(0.175), pytest.approx(0.625))
    # direct fallback when no action is registered
    q = SelectionPanel(ctl, ActionRegistry())
    qtbot.addWidget(q)
    assert q.step_t.value() == pytest.approx(25.0)  # shared through the tool state
    click(qtbot, q.btn_con_f)
    r = s.selection.ops[0].shape
    assert (r.f0, r.f1) == (pytest.approx(400.0), pytest.approx(2900.0))


def test_named_selections_save_and_load(ctl, actions, qtbot):
    s = ctl.session
    p = SelectionPanel(ctl, actions)
    qtbot.addWidget(p)
    assert not p.btn_save.isEnabled()
    sel = Selection.of(RectShape(0.2, 0.6, 300.0, 3000.0))
    s.set_selection(sel)
    assert p.btn_save.isEnabled()
    p.name_edit.setText("Chorus hit")
    click(qtbot, p.btn_save)
    assert "Chorus hit" in s.named_selections
    assert p.named_combo.currentData() == "Chorus hit"
    s.clear_selection()
    click(qtbot, p.btn_load)
    assert s.selection is not None and s.selection.ops == sel.ops


def test_processing_grid_hint(ctl, actions, qtbot):
    p = SelectionPanel(ctl, actions)
    qtbot.addWidget(p)
    assert p.grid_label.text() == "Processing grid: 21.3 ms × 11.7 Hz (hann 4096/1024)"


# ---------------------------------------------------------------- display
def test_display_changes_view_only(ctl, actions, qtbot):
    s = ctl.session
    s.add_empty_layer("Second")
    before = [(l.audio.copy(), l.audio.dtype, l.version) for l in s.project.layers]
    mix_version, n_undo, edit_cfg = s.project.mix_version, undo_len(ctl), s.edit_config
    v = ctl.view
    p = DisplayPanel(ctl, actions)
    qtbot.addWidget(p)
    assert "never change the audio" in p.body.findChildren(type(p.hint))[0].text()

    idx = p.cmap.findData("viridis-like")
    assert "colour-blind friendly" in p.cmap.itemText(idx)
    assert "colour-blind" not in p.cmap.itemText(p.cmap.findData("ember"))
    activate(p.cmap, "viridis-like")
    assert v.colormap == "viridis-like"
    p.min_db.setValue(-90.0)
    assert v.min_db == -90.0
    p.max_db.setValue(-95.0)  # below min: rejected
    assert v.max_db == 0.0 and p.max_db.value() == 0.0 and p.db_error.isVisibleTo(p)
    p.max_db.setValue(-10.0)
    assert v.max_db == -10.0 and not p.db_error.isVisibleTo(p)
    p.gamma.setValue(0.5)
    assert v.gamma == 0.5
    p.opacity.setValue(60)
    assert v.overlay_opacity == pytest.approx(0.6)
    activate(p.scale, "linear")
    assert v.freq_scale == "linear"
    for attr, cb in p.toggles.items():
        cb.setChecked(not getattr(v, attr))
        assert getattr(v, attr) == cb.isChecked()
    activate(p.d_fft, 8192)
    activate(p.d_win, "blackman")
    activate(p.d_ovl, 8)
    assert v.display_config == STFTConfig(8192, 1024, "blackman")
    assert "FFT" in p.d_fft.toolTip() or "frequency detail" in p.d_fft.toolTip()
    assert "frequency detail" in p.d_fft.toolTip() and "timing detail" in p.d_fft.toolTip()
    activate(p.chan, "ch1")
    assert v.channel_mode == "ch1"
    activate(p.layer_disp, "active")
    assert v.layer_display == "active"

    for layer, (audio, dtype, version) in zip(s.project.layers, before):
        assert layer.audio.dtype == dtype and layer.version == version
        assert np.array_equal(layer.audio, audio)
        assert layer.audio.tobytes() == audio.tobytes()
    assert s.project.mix_version == mix_version and undo_len(ctl) == n_undo
    assert s.edit_config == edit_cfg

    # external view changes are reflected without feedback
    v.reset()
    assert p.cmap.currentData() == "ember" and p.gamma.value() == 1.0


def test_display_viewport_numeric(ctl, actions, qtbot):
    v = ctl.view
    p = DisplayPanel(ctl, actions)
    qtbot.addWidget(p)
    assert p.v_t1.value() == pytest.approx(1.0) and p.v_f1.value() == pytest.approx(24000.0)
    p.v_t0.setValue(0.2)
    p.v_t1.setValue(0.5)
    p.v_f0.setValue(100.0)
    p.v_f1.setValue(4000.0)
    assert (v.t0, v.t1) == (0.0, 1.0)  # nothing applied before Go
    click(qtbot, p.btn_go)
    assert (v.t0, v.t1, v.f_lo, v.f_hi) == (pytest.approx(0.2), pytest.approx(0.5), pytest.approx(100.0),
                                           pytest.approx(4000.0))
    v.set_time_range(0.1, 0.3)  # e.g. zoom on the canvas
    assert p.v_t0.value() == pytest.approx(0.1) and p.v_t1.value() == pytest.approx(0.3)
    click(qtbot, p.btn_fit)
    assert "view.fit_project" in actions.calls and (v.t0, v.t1) == (0.0, pytest.approx(1.0))
    assert p.v_t1.value() == pytest.approx(1.0)


def test_processing_resolution_updates_edit_config(ctl, actions, qtbot):
    s = ctl.session
    display_cfg = ctl.view.display_config
    p = DisplayPanel(ctl, actions)
    sp = SelectionPanel(ctl, actions)
    qtbot.addWidget(p)
    qtbot.addWidget(sp)
    n = undo_len(ctl)
    activate(p.p_fft, 2048)
    assert s.edit_config == STFTConfig(2048, 512, "hann")
    assert s.project.processing_config == s.edit_config
    activate(p.p_ovl, 8)
    activate(p.p_win, "hamming")
    assert s.edit_config == STFTConfig(2048, 256, "hamming")
    assert ctl.view.display_config == display_cfg  # display analysis is independent
    assert undo_len(ctl) == n
    assert "hamming 2048/256" in p.p_grid.text()
    assert sp.grid_label.text() == "Processing grid: 5.3 ms × 23.4 Hz (hamming 2048/256)"


# ---------------------------------------------------------------- history
def _four_gain_steps(ctl):
    s = ctl.session
    lid = s.project.layers[0].id
    for g in (-1.0, -2.0, -3.0, -4.0):
        s.set_layer_props(lid, gain_db=g)
    return s.project.layers[0]


def _click_row(qtbot, p, row):
    rect = p.list.visualItemRect(p.list.item(row))
    qtbot.mouseClick(p.list.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())


def test_history_click_to_position(ctl, actions, qtbot):
    layer = _four_gain_steps(ctl)
    h = ctl.session.history
    p = HistoryPanel(ctl, actions)
    qtbot.addWidget(p)
    p.show()
    assert [k for k, _ in p.entries()] == ["initial", "applied", "applied", "applied", "applied"]
    assert p.list.currentRow() == 4

    _click_row(qtbot, p, 2)  # back to "after step 2": two undos
    assert len(h.undo_stack) == 2 and len(h.redo_stack) == 2
    assert layer.gain_db == -2.0
    assert [k for k, _ in p.entries()] == ["initial", "applied", "applied", "undone", "undone"]
    assert p.list.currentRow() == 2
    undone = p.list.item(4)
    assert undone.font().italic()

    _click_row(qtbot, p, 4)  # redo both
    assert len(h.undo_stack) == 4 and layer.gain_db == -4.0

    _click_row(qtbot, p, 0)  # initial state: undo everything
    assert len(h.undo_stack) == 0 and layer.gain_db == 0.0

    p.list.setCurrentRow(3)  # keyboard: Return jumps to the current row
    qtbot.keyClick(p.list, Qt.Key.Key_Return)
    assert len(h.undo_stack) == 3 and layer.gain_db == -3.0


def test_history_disabled_while_busy(ctl, actions, qtbot):
    _four_gain_steps(ctl)
    p = HistoryPanel(ctl, actions)
    qtbot.addWidget(p)
    ctl._busy = True
    ctl.busyChanged.emit(True, "test job")
    try:
        assert not p.list.isEnabled()
        p.go_to(0)
        assert len(ctl.session.history.undo_stack) == 4
    finally:
        ctl._busy = False
        ctl.busyChanged.emit(False, "")
    assert p.list.isEnabled()


def test_history_buttons_and_note(ctl, actions, qtbot):
    _four_gain_steps(ctl)
    p = HistoryPanel(ctl, actions)
    qtbot.addWidget(p)
    click(qtbot, p.btn_undo)
    assert "edit.undo" in actions.calls and len(ctl.session.history.undo_stack) == 3
    click(qtbot, p.btn_redo)
    assert "edit.redo" in actions.calls and len(ctl.session.history.undo_stack) == 4
    texts = " ".join(lab.text() for lab in p.body.findChildren(type(p.hint)))
    assert "Undo history is kept for this session" in texts


def test_history_record_selection_checkbox(ctl, actions, qtbot):
    s = ctl.session
    p = HistoryPanel(ctl, actions)
    qtbot.addWidget(p)
    assert not p.record_selection.isChecked()
    p.record_selection.setChecked(True)
    assert s.record_selection_history is True
    s.set_selection(Selection.of(RectShape(0.2, 0.6, 300.0, 3000.0)))
    assert p.entries()[-1][1].startswith("Selection:")
    ctl.set_session(EditorSession.from_array(synthetic_stereo(0.5), SR))
    assert not p.record_selection.isChecked()
    assert p.entries() == [("initial", "Initial state")]
