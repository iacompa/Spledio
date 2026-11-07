"""Main window: menus, toolbars, docks, status bar, and the action registry."""

from __future__ import annotations

import math
import os

from PySide6.QtCore import QByteArray, QSettings, Qt, QTimer, Slot
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QDockWidget,
    QFileDialog,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QAbstractSpinBox,
    QPlainTextEdit,
    QProgressBar,
    QScrollArea,
    QSlider,
    QTabBar,
    QToolBar,
    QToolButton,
    QWidget,
)

from spledio import __version__
from spledio.audio.io import IMPORT_FILE_PATTERNS
from spledio.branding import APP_NAME, APP_ORG, APP_TAGLINE, APP_WORKING_TITLE, PROJECT_EXTENSION
from spledio.engine.session import EditError, channel_scope_label
from spledio.persistence.project_file import AssetMismatchError, MissingMediaError, ProjectFormatError
from spledio.ui.actions import ActionRegistry, ActionSpec
from spledio.ui.controller import EditorController
from spledio.ui.state import TOOLS

CANVAS_KEY_ACTIONS = ("area.louder", "area.quieter", "area.louder_big", "area.quieter_big", "area.silence",
                      "area.next", "area.prev")

AUDIO_FILTER = f"Audio files ({IMPORT_FILE_PATTERNS});;All files (*)"
PROJECT_FILTER = f"{APP_NAME} project (*{PROJECT_EXTENSION})"


def _icon(name):
    try:
        from spledio.ui.icons import icon

        return icon(name)
    except Exception:  # icons are optional; text fallback
        return None


def _component_versions() -> str:
    """The main components and their versions, read from the running app (not hard-coded)."""
    import platform

    parts = []

    def add(name, get, lic):
        try:
            v = get()
        except Exception:  # a missing component must not break the About box
            v = ""
        parts.append(f"{name} {v} ({lic})" if v else f"{name} ({lic})")

    from PySide6 import __version__ as pyside_version
    from PySide6.QtCore import qVersion

    add("Qt", qVersion, "LGPL-3.0")
    add("PySide6", lambda: pyside_version, "LGPL-3.0")
    add("Python", platform.python_version, "PSF-2.0")
    add("NumPy", lambda: __import__("numpy").__version__, "BSD-3-Clause")
    add("SciPy", lambda: __import__("scipy").__version__, "BSD-3-Clause")
    add("libsndfile", lambda: __import__("soundfile").__libsndfile_version__, "LGPL-2.1")
    add("PortAudio", lambda: __import__("sounddevice").get_portaudio_version()[1].split(",")[0]
        .replace("PortAudio", "").strip().lstrip("V"), "MIT")
    return ", ".join(parts[:-1]) + " and " + parts[-1]


class MainWindow(QMainWindow):
    def __init__(self, controller: EditorController | None = None):
        super().__init__()
        self.ctl = controller or EditorController(self)
        self.settings = QSettings(APP_ORG, APP_NAME)
        self.setWindowTitle(APP_NAME)
        self.resize(1360, 820)
        self.setMinimumSize(900, 560)
        self.setDockOptions(QMainWindow.AnimatedDocks | QMainWindow.AllowTabbedDocks | QMainWindow.AllowNestedDocks)
        self.actions = ActionRegistry()
        self._focus_saved: QByteArray | None = None
        self._register_actions()
        self.actions.build(self, _icon)
        self._apply_saved_shortcuts()
        self._build_central()
        self._build_menus()
        self._build_toolbars()
        self._build_docks()
        self._build_status()
        self._connect()
        self._restore_layout()
        self.refresh_actions()
        self.setAcceptDrops(True)
        try:
            from spledio.ui.icons import app_icon

            ic = app_icon()
            if ic is not None:
                self.setWindowIcon(ic)
        except Exception:
            pass

    # ================================================================ actions
    def _s(self):
        return self.ctl.session

    def _need_session(self):
        return None if self.ctl.session is not None else "Open an audio file first"

    def _need_idle(self):
        r = self._need_session()
        if r:
            return r
        return "Busy: wait for the current operation (Esc cancels)" if self.ctl.busy else None

    def _need_sel(self):
        r = self._need_idle()
        if r:
            return r
        return None if self.ctl.session.selection is not None else "Make a selection first"

    def _need_editable_sel(self):
        r = self._need_sel()
        if r:
            return r
        lay = self.ctl.session.active_layer
        if lay is None:
            return "No layer"
        return f"Layer '{lay.name}' is locked" if lay.locked else None

    def _need_area_target(self):
        """Area keys: need a selection (or picked area) on an unlocked layer. Not blocked while a job
        runs – presses made during a render are queued by the controller."""
        s = self.ctl.session
        if s is None:
            return "Open an audio file first"
        a = s.current_area()
        if a is None and s.selection is None:
            return "Select something first (drag a box on the spectrogram) or click an adjusted area"
        lay = s.project.layer(a.layer_id) if a is not None else s.active_layer
        if lay is None:
            return "No layer"
        return f"Layer '{lay.name}' is locked" if lay.locked else None

    def _need_picked_area(self):
        r = self._need_idle()
        if r:
            return r
        return None if self.ctl.session.current_area() is not None else "Click an adjusted area first"

    def _need_any_area(self):
        r = self._need_idle()
        if r:
            return r
        return None if self.ctl.session.visible_areas() else "There are no adjusted areas yet"

    def _cycle_area(self, step: int):
        s = self.ctl.session
        areas = s.visible_areas() if s else []
        if not areas:
            return
        cur = s.current_area()
        i = areas.index(cur) if cur in areas else (-1 if step > 0 else 0)
        self.ctl.select_area(areas[(i + step) % len(areas)].id)

    def _need_preview(self):
        r = self._need_idle()
        if r:
            return r
        return None if self.ctl.session.preview is not None else "Press Preview first"

    def _need_undo(self):
        r = self._need_idle()
        return r or (None if self.ctl.session.history.can_undo() else "Nothing to undo")

    def _need_redo(self):
        r = self._need_idle()
        return r or (None if self.ctl.session.history.can_redo() else "Nothing to redo")

    def _need_edit_history(self):
        r = self._need_idle()
        if r:
            return r
        if self.ctl.session.preview is not None:
            return None
        return None if any(c.changes_audio for c in self.ctl.session.history.undo_stack) else "Apply or preview an edit first"

    def _register_actions(self):
        A = self.actions.add
        c = self.ctl
        # ---- File
        A(ActionSpec("file.open_audio", "Open Audio…", "Open a WAV, FLAC, AIFF, MP3 or OGG file (the source file is never modified)",
                     self.open_audio_dialog, "File", "Ctrl+O", "open", acts_on="new project", toolbar="file"))
        A(ActionSpec("file.open_project", "Open Project…", f"Open a saved {APP_NAME} project", self.open_project_dialog,
                     "File", "Ctrl+Shift+O", "open", acts_on="project file"))
        A(ActionSpec("file.save", "Save Project", "Save the editable project (layers, edits, selection), not a flattened WAV",
                     self.save_project, "File", "Ctrl+S", "save", self._need_idle, acts_on="project", toolbar="file"))
        A(ActionSpec("file.save_as", "Save Project As…", "Save the project to a new file", lambda: self.save_project(ask=True),
                     "File", "Ctrl+Shift+S", "save", self._need_idle, acts_on="project"))
        A(ActionSpec("file.save_portable", "Save Portable Project…",
                     "Save a copy that embeds the source audio files, so it opens on another computer",
                     lambda: self.save_project(ask=True, portable=True), "File", "", "save", self._need_idle, acts_on="project"))
        A(ActionSpec("file.export", "Export Audio…", "Render the current edited audio to WAV/FLAC (choose bit depth, dither, clipping policy)",
                     self.export_dialog, "File", "Ctrl+E", "export", self._need_idle, acts_on="rendered mix / layers / selection",
                     toolbar="file"))
        A(ActionSpec("file.quit", "Quit", f"Quit {APP_NAME}", self.close, "File", "Ctrl+Q"))
        # ---- Edit
        A(ActionSpec("edit.undo", "Undo", "Undo the last audio/layer operation (restores exact samples)", c.undo, "Edit",
                     "Ctrl+Z", "undo", self._need_undo, changes_audio=True, acts_on="project", toolbar="edit"))
        A(ActionSpec("edit.redo", "Redo", "Redo the next operation", c.redo, "Edit", ("Ctrl+Shift+Z", "Ctrl+Y"), "redo",
                     self._need_redo, changes_audio=True, acts_on="project", toolbar="edit"))
        A(ActionSpec("edit.apply_gain", "Apply Gain", "Change the selected spectral content by the gain set in the edit panel",
                     lambda: c.apply_gain(c.tools.gain_db), "Edit", "Ctrl+G", "gain", self._need_editable_sel,
                     changes_audio=True, undoable=True, acts_on="selection on active layer"))
        for aid, db in (("edit.gain_m3", -3), ("edit.gain_m6", -6), ("edit.gain_m12", -12), ("edit.gain_p3", 3), ("edit.gain_p6", 6)):
            A(ActionSpec(aid, f"Apply {db:+d} dB", f"Immediately apply {db:+d} dB to the selected content", (lambda d=db: c.apply_gain(d)),
                         "Edit/Quick gain", "", "", self._need_editable_sel, changes_audio=True, undoable=True,
                         acts_on="selection on active layer"))
        A(ActionSpec("edit.gain_reset", "Reset Gain to 0 dB", "Set the edit-panel gain value back to 0 dB (does not change audio)",
                     lambda: c.tools.set(gain_db=0.0), "Edit/Quick gain", "", "", None))
        A(ActionSpec("edit.mute", "Mute Region", "Set the selected spectral content to zero gain (reversible)", c.mute_selection,
                     "Edit", "Ctrl+M", "mute", self._need_editable_sel, changes_audio=True, undoable=True, acts_on="selection"))
        A(ActionSpec("edit.delete_spectral", "Delete / Remove Adjustment",
                     "On a picked adjusted area: remove its adjustment (the audio there comes back exactly). "
                     "On a new selection: silence the selected sound as a new adjusted area – duration and timing "
                     "are never changed, and you can bring it back later",
                     c.delete_or_remove, "Edit", ("Delete", "Backspace"), "delete", self._need_area_target,
                     changes_audio=True, undoable=True, acts_on="selection / picked area", text_safe=False))
        A(ActionSpec("edit.extract", "Extract to New Layer",
                     "Move the selected content to a new aligned layer at the same level; remainder + extract = original",
                     lambda: c.extract(False), "Edit", "Ctrl+J", "extract", self._need_editable_sel, changes_audio=True,
                     undoable=True, acts_on="selection → new layer"))
        A(ActionSpec("edit.copy_to_layer", "Copy to New Layer",
                     "Duplicate the selected content into a new layer (the summed level can increase)",
                     lambda: c.extract(True), "Edit", "Ctrl+Shift+J", "copy_layer", self._need_sel, changes_audio=True,
                     undoable=True, acts_on="selection → new layer"))
        A(ActionSpec("edit.delete_time", "Delete Time (shortens project)…",
                     "Remove the selection's time span from ALL layers. This shortens the recording",
                     self.delete_time, "Edit/Time", "Ctrl+Alt+Delete", "", self._need_sel, changes_audio=True, undoable=True,
                     acts_on="all layers"))
        A(ActionSpec("edit.crop_time", "Crop Time to Selection…", "Keep only the selection's time span in ALL layers",
                     self.crop_time, "Edit/Time", "", "", self._need_sel, changes_audio=True, undoable=True, acts_on="all layers"))
        A(ActionSpec("edit.insert_silence", "Insert Silence…", "Insert silence at the playhead in ALL layers (lengthens the project)",
                     self.insert_silence, "Edit/Time", "", "", self._need_idle, changes_audio=True, undoable=True, acts_on="all layers"))
        # ---- Adjusted areas (keys work while the spectrogram has focus)
        for aid, lab, keys, d, big, tip in (
            ("area.louder", "Louder (1 dB)", ("Up", "+", "="), +1, False, "Raise the level of the selection / picked area by 1 dB"),
            ("area.quieter", "Quieter (1 dB)", ("Down", "-"), -1, False,
             "Lower the level of the selection / picked area by 1 dB (below −48 dB: silence)"),
            ("area.louder_big", "Louder (6 dB)", ("Shift+Up",), +1, True, "Raise the level to the next 6 dB step"),
            ("area.quieter_big", "Quieter (6 dB)", ("Shift+Down",), -1, True, "Lower the level to the next 6 dB step"),
        ):
            A(ActionSpec(aid, lab, tip + ". Each adjusted area stays outlined so you can pick it again later",
                         (lambda d=d, b=big: c.adjust_area(d, b)), "Edit/Adjusted Areas", keys, "", self._need_area_target,
                         changes_audio=True, undoable=True, acts_on="selection / picked area"))
        A(ActionSpec("area.silence", "Silence", "Turn the selection / picked area all the way down (it can be brought back)",
                     lambda: c.adjust_area(target_db=float("-inf")), "Edit/Adjusted Areas", "0", "", self._need_area_target,
                     changes_audio=True, undoable=True, acts_on="selection / picked area"))
        A(ActionSpec("area.remove", "Remove Adjustment", "Undo the picked area's level change exactly (other areas stay)",
                     lambda: c.remove_area(), "Edit/Adjusted Areas", "", "", self._need_picked_area,
                     changes_audio=True, undoable=True, acts_on="picked area"))
        A(ActionSpec("area.remove_all", "Remove All Adjustments", "Remove every adjusted area (one undo step)",
                     c.remove_all_areas, "Edit/Adjusted Areas", "", "", self._need_any_area,
                     changes_audio=True, undoable=True, acts_on="all adjusted areas"))
        A(ActionSpec("area.next", "Pick Next Area", "Select the next adjusted area", lambda: self._cycle_area(+1),
                     "Edit/Adjusted Areas", "]", "", self._need_any_area))
        A(ActionSpec("area.prev", "Pick Previous Area", "Select the previous adjusted area", lambda: self._cycle_area(-1),
                     "Edit/Adjusted Areas", "[", "", self._need_any_area))
        A(ActionSpec("view.show_areas", "Show Adjusted Areas", "Outline every part you turned up, down or silenced, "
                     "with its level (display only). Turn off to hide them; the picked area stays outlined. "
                     "Also the Show / Hide buttons under the spectrogram",
                     lambda on: self.ctl.view.set_display(show_areas=on),
                     "View", "Shift+H", "", None, checkable=True, checked=lambda: self.ctl.view.show_areas))
        # ---- Module (preview / bypass / apply / cancel)
        A(ActionSpec("module.preview", "Preview", "Render the gain edit without applying it, then play it", lambda: c.preview_gain(c.tools.gain_db),
                     "Edit/Module", "P", "preview", self._need_editable_sel, acts_on="selection", text_safe=False))
        A(ActionSpec("module.bypass", "Bypass (A/B preview)", "Toggle between the original and the preview while playing",
                     c.toggle_bypass, "Edit/Module", "B", "bypass", self._need_preview, text_safe=False))
        from spledio.ui.panels.edit_panel import edit_reason, module_apply, module_cancel

        A(ActionSpec("module.apply", "Apply", "Commit exactly the previewed result as one undoable operation "
                     "(without a preview: apply the current gain amount)",
                     lambda: module_apply(c), "Edit/Module", ("Ctrl+Return", "Ctrl+Enter"), "apply",
                     lambda: edit_reason(c, "apply"), changes_audio=True, undoable=True, acts_on="selection"))
        A(ActionSpec("module.cancel", "Cancel Preview", "Discard the preview (or stop a preview that is still rendering); "
                     "audio is unchanged", lambda: module_cancel(c), "Edit/Module", "", "cancel",
                     lambda: edit_reason(c, "cancel")))
        # ---- Transport / audition
        A(ActionSpec("transport.play_pause", "Play / Pause", "Play the audible mix from the playhead, or pause",
                     c.play_pause, "Transport", "Space", "play", self._need_session, toolbar="transport", text_safe=False))
        A(ActionSpec("transport.stop", "Stop", "Stop playback (the playhead and view stay where they are)", c.stop,
                     "Transport", "", "stop", self._need_session, toolbar="transport"))
        A(ActionSpec("transport.to_start", "Return to Start", "Move the playhead to the project start", c.return_to_start,
                     "Transport", "Home", "to_start", self._need_session, toolbar="transport", text_safe=False))
        A(ActionSpec("transport.loop", "Loop", "Loop the selection's time span (or the whole project)", c.set_loop_enabled,
                     "Transport", "L", "loop", self._need_session, checkable=True, checked=lambda: c.loop_enabled,
                     toolbar="transport", text_safe=False))
        A(ActionSpec("audition.selected", "Hear Selected Frequencies", "Play only the selected spectral content (real mask)",
                     lambda: c.audition_mode("selected"), "Transport/Audition", "S", "hear_selected", self._need_sel,
                     toolbar="audition", text_safe=False))
        A(ActionSpec("audition.all_freqs", "Hear All Frequencies", "Play the selection's time range without frequency restriction",
                     lambda: c.audition_mode("all_freqs"), "Transport/Audition", "Shift+S", "hear_all", self._need_sel,
                     toolbar="audition", text_safe=False))
        A(ActionSpec("audition.remainder", "Hear Remainder", "Play the active layer in that span with the selection removed",
                     lambda: c.audition_mode("remainder"), "Transport/Audition", "R", "hear_remainder", self._need_sel,
                     toolbar="audition", text_safe=False))
        A(ActionSpec("audition.removed", "Hear What Is Being Removed",
                     "Play original minus processed for the preview (or the last edit), time-aligned",
                     lambda: c.audition_mode("difference"), "Transport/Audition", "D", "hear_removed", self._need_edit_history,
                     toolbar="audition", text_safe=False))
        A(ActionSpec("audition.ab", "A/B Compare", "Switch between original (A) and processed (B) without moving the playhead",
                     c.ab_toggle, "Transport/Audition", "A", "ab", self._need_edit_history, toolbar="audition", text_safe=False))
        # ---- Select
        s_ = lambda: self.ctl.session  # noqa: E731
        A(ActionSpec("select.all", "Select All", "Select the whole project (all time, all frequencies)",
                     lambda: s_().select_all(), "Select", "Ctrl+A", "", self._need_idle))
        A(ActionSpec("select.none", "Deselect", "Remove the selection only (audio unchanged)", lambda: s_().clear_selection(),
                     "Select", "Ctrl+D", "", self._need_sel))
        A(ActionSpec("select.reselect", "Reselect", "Restore the previous selection", lambda: s_().reselect(), "Select",
                     "Ctrl+Shift+D", "", self._need_idle))
        A(ActionSpec("select.invert", "Invert Selection", "Select everything that is not selected", lambda: s_().invert_selection(),
                     "Select", "Ctrl+I", "invert", self._need_idle))
        for aid, lab, dt, df in (("select.expand_time", "Expand in Time", 1, 0), ("select.contract_time", "Contract in Time", -1, 0),
                                 ("select.expand_freq", "Expand in Frequency", 0, 1), ("select.contract_freq", "Contract in Frequency", 0, -1)):
            A(ActionSpec(aid, lab, f"{lab} by the step set in the Selection panel", (lambda a=dt, b=df: self._expand(a, b)),
                         "Select/Modify", "", "", self._need_sel))
        A(ActionSpec("select.feather_toggle", "Feathering On", "Temporarily disable/enable feathering (keeps the values)",
                     self._feather_toggle, "Select/Modify", "Ctrl+Shift+F", "", self._need_sel, checkable=True,
                     checked=lambda: bool(self.ctl.session and self.ctl.session.selection and self.ctl.session.selection.feather_enabled)))
        for mode, key in (("replace", ""), ("add", ""), ("subtract", ""), ("intersect", "")):
            A(ActionSpec(f"select.mode_{mode}", f"Mode: {mode.title()}",
                         {"replace": "New selections replace the current one",
                          "add": "New selections are added (or hold Shift)",
                          "subtract": "New selections are removed from the current one (or hold Alt/Option)",
                          "intersect": "Keep only the overlap (or hold Shift+Alt)"}[mode],
                         (lambda on, m=mode: on and self.ctl.tools.set(selection_mode=m)), "Select/Mode", key, f"mode_{mode}",
                         None, checkable=True, checked=(lambda m=mode: self.ctl.tools.selection_mode == m)))
        # ---- View
        A(ActionSpec("view.zoom_in", "Zoom In (time)", "Zoom in around the playhead/centre", lambda: self._zoom(0.5), "View",
                     ("Ctrl+=", "Ctrl++"), "zoom_in", self._need_session, toolbar="view"))
        A(ActionSpec("view.zoom_out", "Zoom Out (time)", "Zoom out", lambda: self._zoom(2.0), "View", ("Ctrl+-",), "zoom_out",
                     self._need_session, toolbar="view"))
        A(ActionSpec("view.zoom_in_freq", "Zoom In (frequency)", "Zoom in vertically around the centre",
                     lambda: self.ctl.view.zoom_freq(0.5), "View", "Ctrl+Shift+Up", "", self._need_session))
        A(ActionSpec("view.zoom_out_freq", "Zoom Out (frequency)", "Zoom out vertically", lambda: self.ctl.view.zoom_freq(2.0),
                     "View", "Ctrl+Shift+Down", "", self._need_session))
        A(ActionSpec("view.fit_selection", "Fit Selection", "Fit the current time-frequency selection in the view",
                     self._fit_selection, "View", "Ctrl+Shift+E", "fit_selection", self._need_sel, toolbar="view"))
        A(ActionSpec("view.fit_project", "Fit Project", "Show the whole project (view only; the file is never cropped)",
                     lambda: self.ctl.view.fit_project(), "View", "Ctrl+0", "fit_project", self._need_session, toolbar="view"))
        A(ActionSpec("view.reset", "Reset View", "Default viewport and display settings", lambda: self.ctl.view.reset(), "View",
                     "", "reset_view", self._need_session))
        A(ActionSpec("view.focus", "Focus Editing", "Hide panels to maximise the spectrogram (toggle)", self._toggle_focus,
                     "View", "F11", "focus", None, checkable=True, checked=lambda: self._focus_saved is not None))
        for aid, attr, lab, key in (("view.overview", "show_overview", "Show Overview", ""), ("view.waveform", "show_waveform", "Show Waveform", ""),
                                    ("view.notes", "show_note_labels", "Musical Note Labels", ""),
                                    ("view.crosshair", "show_crosshair", "Crosshair", "")):
            A(ActionSpec(aid, lab, f"{lab} (display only)", (lambda on, a=attr: self.ctl.view.set_display(**{a: on})), "View",
                         key, "", None, checkable=True, checked=(lambda a=attr: getattr(self.ctl.view, a))))
        A(ActionSpec("view.log_scale", "Logarithmic Frequency Scale", "Log frequency axis (off = linear). Display only",
                     lambda on: self.ctl.view.set_display(freq_scale="log" if on else "linear"), "View", "Ctrl+L", "", None,
                     checkable=True, checked=lambda: self.ctl.view.freq_scale == "log"))
        # ---- Tools (exclusive)
        keys = {"time": "T", "freq": "F", "rect": "M", "ellipse": "E", "lasso": "Shift+L", "polygon": "Shift+P",
                "sel_brush": "Shift+B", "transform": "V", "reduce_brush": "Shift+R", "amplify_brush": "Shift+A",
                "transfer_brush": "Shift+T", "hand": "H", "zoom": "Z"}
        for tid, lab, group, tip in TOOLS:
            A(ActionSpec(f"tool.{tid}", lab, tip, (lambda on, t=tid: on and self.ctl.tools.set(tool=t)), "Tools",
                         keys.get(tid, ""), tid, None, checkable=True, checked=(lambda t=tid: self.ctl.tools.tool == t),
                         changes_audio=group == "edit", undoable=group == "edit", acts_on="active layer" if group == "edit" else "view/selection",
                         toolbar="tools", text_safe=False))
        # ---- Layers
        A(ActionSpec("layer.new", "New Empty Layer", "Add a silent, time-aligned layer", lambda: c.safe(s_().add_empty_layer),
                     "Layer", "Ctrl+Shift+N", "layer_new", self._need_idle, changes_audio=False, undoable=True))
        A(ActionSpec("layer.duplicate", "Duplicate Layer", "Duplicate the active layer (summed level increases)",
                     lambda: c.safe(s_().duplicate_layer, s_().active_layer_id), "Layer", "", "layer_dup", self._need_idle,
                     changes_audio=True, undoable=True))
        A(ActionSpec("layer.delete", "Delete Layer", "Delete the active layer (undoable)", lambda: c.safe(s_().delete_layer, s_().active_layer_id),
                     "Layer", "", "layer_delete", self._need_idle, changes_audio=True, undoable=True))
        A(ActionSpec("layer.up", "Move Layer Up", "Move the active layer up", lambda: self._move_layer(-1), "Layer", "", "up", self._need_idle,
                     undoable=True))
        A(ActionSpec("layer.down", "Move Layer Down", "Move the active layer down", lambda: self._move_layer(1), "Layer", "", "down",
                     self._need_idle, undoable=True))
        A(ActionSpec("layer.merge", "Merge All Layers", "Merge every layer into one (at current gains)",
                     lambda: c.safe(s_().merge_layers, [l.id for l in s_().project.layers]), "Layer", "", "merge",
                     lambda: self._need_idle() or (None if len(self.ctl.session.project.layers) > 1 else "Only one layer"),
                     changes_audio=True, undoable=True))
        A(ActionSpec("layer.mute_active", "Mute Active Layer", "Toggle mute of the active layer", lambda: self._toggle_layer("mute"),
                     "Layer", "Ctrl+Shift+M", "mute_layer", self._need_idle, changes_audio=True, undoable=True))
        A(ActionSpec("layer.solo_active", "Solo Active Layer", "Toggle solo of the active layer", lambda: self._toggle_layer("solo"),
                     "Layer", "Ctrl+Shift+L", "solo", self._need_idle, changes_audio=True, undoable=True))
        A(ActionSpec("layer.export", "Export Active Layer…", "Export the active layer only", lambda: self.export_dialog([s_().active_layer_id]),
                     "Layer", "", "export", self._need_idle))
        # ---- Help
        A(ActionSpec("help.commands", "Command Palette…", "Search and run any command", self.command_palette, "Help",
                     ("Ctrl+K", "Ctrl+Shift+P"), "commands"))
        A(ActionSpec("help.tutorials", "Tutorials…", "Short guided tutorials", self.tutorials, "Help", "F1", "help"))
        A(ActionSpec("help.shortcuts", "Keyboard Shortcuts…", "View and remap shortcuts", self.shortcuts_dialog, "Help", ""))
        A(ActionSpec("help.cancel_job", "Cancel Running Operation", "Cancel the current render/analysis job", self.ctl.cancel_job,
                     "Help", "", "", lambda: None if self.ctl.busy else "Nothing is running"))
        A(ActionSpec("help.licenses", "Licenses and Notices…",
                     f"{APP_NAME}'s license and the licenses of the components it includes", self.licenses_dialog,
                     "Help", ""))
        A(ActionSpec("help.about", f"About {APP_NAME}", "Version, license and privacy information", self.about, "Help", ""))

    # ---------------------------------------------------------- helpers
    def _zoom(self, f):
        v = self.ctl.view
        anchor = None
        if self.ctl.session is not None:
            pt = self.ctl.playback.position / self.ctl.session.sample_rate
            if v.t0 <= pt <= v.t1:
                anchor = pt
        v.zoom_time(f, anchor)

    def _fit_selection(self):
        s = self.ctl.session
        b = s.selection.bounds(s.project.duration, s.project.nyquist)
        if b:
            self.ctl.view.fit_box(*b)

    def _expand(self, dt_sign, df_sign):
        s = self.ctl.session
        step_s = getattr(self.ctl.tools, "expand_step_s", 0.01)
        step_hz = getattr(self.ctl.tools, "expand_step_hz", 50.0)
        s.set_selection(s.selection.expanded(dt_sign * step_s, df_sign * step_hz))

    def _feather_toggle(self, on):
        s = self.ctl.session
        if s and s.selection:
            s.set_selection(s.selection.with_feather(enabled=on))

    def _move_layer(self, d):
        s = self.ctl.session
        self.ctl.safe(s.move_layer, s.active_layer_id, s.project.index_of(s.active_layer_id) + d)

    def _toggle_layer(self, prop):
        s = self.ctl.session
        lay = s.active_layer
        self.ctl.safe(s.set_layer_props, lay.id, **{prop: not getattr(lay, prop)})

    # ============================================================== layout
    def _build_central(self):
        from spledio.ui.spectrogram_view import SpectrogramEditor

        from spledio.ui.view_bar import ViewBar

        self.editor = SpectrogramEditor(self.ctl, self)
        self.view_bar = ViewBar(self.ctl, self.actions, self.editor)  # zoom −/+ and Show/Hide areas, always in view
        self.editor.set_bottom_bar(self.view_bar)
        self.setCentralWidget(self.editor)
        # Arrow keys / + − / 0 / [ ] act on the spectrogram only while it has keyboard focus, so lists,
        # spin boxes and text fields keep their own arrow keys.
        for aid in CANVAS_KEY_ACTIONS:
            act = self.actions[aid]
            act.setShortcutContext(Qt.WidgetShortcut)
            self.editor.canvas.addAction(act)

    def _build_menus(self):
        mb = self.menuBar()
        menus: dict[str, object] = {}

        def menu_for(path: str):
            if path in menus:
                return menus[path]
            if "/" in path:
                parent, name = path.rsplit("/", 1)
                m = menu_for(parent).addMenu(name)
            else:
                m = mb.addMenu(path)
            menus[path] = m
            return m

        order = ["File", "Edit", "Select", "View", "Tools", "Transport", "Layer", "Help"]
        for top in order:
            menu_for(top)
        tool_group = QActionGroup(self)
        tool_group.setExclusive(True)
        mode_group = QActionGroup(self)
        mode_group.setExclusive(True)
        for spec in self.actions.specs.values():
            act = self.actions[spec.id]
            if spec.id.startswith("tool."):
                tool_group.addAction(act)
            if spec.id.startswith("select.mode_"):
                mode_group.addAction(act)
            m = menu_for(spec.menu)
            if spec.id in ("file.export", "file.quit", "edit.apply_gain", "select.invert", "view.reset", "help.licenses"):
                m.addSeparator()
            m.addAction(act)
        self.addActions(list(self.actions.actions.values()))  # shortcuts work even with menus hidden
        vm = menu_for("View")
        vm.addSeparator()
        self.panels_menu = vm.addMenu("Panels")

    def _toolbar(self, name, title, area=Qt.TopToolBarArea):
        tb = QToolBar(title, self)
        tb.setObjectName(f"tb_{name}")
        tb.setMovable(True)
        self.addToolBar(area, tb)
        return tb

    def _build_toolbars(self):
        for tbid, title in (("file", "File"), ("edit", "Edit"), ("transport", "Transport"), ("audition", "Audition"), ("view", "View")):
            tb = self._toolbar(tbid, title)
            tb.setToolButtonStyle(Qt.ToolButtonTextBesideIcon if tbid in ("audition",) else Qt.ToolButtonIconOnly)
            for spec in self.actions.specs.values():
                if spec.toolbar == tbid:
                    tb.addAction(self.actions[spec.id])
                    if self.actions[spec.id].icon().isNull():
                        w = tb.widgetForAction(self.actions[spec.id])
                        if isinstance(w, QToolButton):
                            w.setToolButtonStyle(Qt.ToolButtonTextOnly)
            if tbid == "transport":
                self.time_label = QLabel("0:00.000 / 0:00.000")
                self.time_label.setMinimumWidth(170)
                self.time_label.setToolTip("Playhead time / project duration")
                tb.addWidget(self.time_label)
                vol = QSlider(Qt.Horizontal)
                vol.setRange(0, 100)
                vol.setValue(100)
                vol.setFixedWidth(80)
                vol.setToolTip("Monitor volume (playback only – never affects exported audio)")
                vol.valueChanged.connect(lambda v: self.ctl.set_monitor_volume(v / 100.0))
                tb.addWidget(QLabel(" Vol"))
                tb.addWidget(vol)
                self.vol_slider = vol
        self.addToolBarBreak()
        self.tools_bar = self._toolbar("tools", "Tools", Qt.LeftToolBarArea)
        self.tools_bar.setToolButtonStyle(Qt.ToolButtonIconOnly)
        last_group = None
        for tid, lab, group, tip in TOOLS:
            if last_group and group != last_group:
                self.tools_bar.addSeparator()
            last_group = group
            act = self.actions[f"tool.{tid}"]
            self.tools_bar.addAction(act)
            if act.icon().isNull():
                w = self.tools_bar.widgetForAction(act)
                if isinstance(w, QToolButton):
                    w.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self._build_tool_options()

    def _build_tool_options(self):
        """Active-tool settings bar (brush size/hardness/strength, selection mode)."""
        from PySide6.QtWidgets import QComboBox, QDoubleSpinBox, QHBoxLayout

        tb = self._toolbar("tool_options", "Tool Options")
        self.tool_opts = tb
        t = self.ctl.tools
        self.tool_label = QLabel()
        self.tool_label.setMinimumWidth(130)
        tb.addWidget(self.tool_label)

        def spin(lo, hi, val, step, suffix, tip, setter, decimals=1):
            sp = QDoubleSpinBox()
            sp.setRange(lo, hi)
            sp.setDecimals(decimals)
            sp.setSingleStep(step)
            sp.setValue(val)
            sp.setSuffix(suffix)
            sp.setToolTip(tip)
            sp.setKeyboardTracking(False)
            sp.valueChanged.connect(setter)
            return sp

        self.opt_size = spin(2, 400, t.brush_size_px, 2, " px", "Brush diameter on screen (screen-size mode)",
                             lambda v: t.set(brush_size_px=v), 0)
        self.opt_mode = QComboBox()
        self.opt_mode.addItem("Screen size", "screen")
        self.opt_mode.addItem("Spectral size (s × Hz)", "spectral")
        self.opt_mode.setToolTip("Screen size: constant on screen while zooming. Spectral size: constant in seconds/Hz")
        self.opt_mode.currentIndexChanged.connect(lambda: t.set(brush_size_mode=self.opt_mode.currentData()))
        self.opt_w = spin(0.002, 10, t.brush_width_s, 0.01, " s", "Brush width in seconds (spectral-size mode)",
                          lambda v: t.set(brush_width_s=v), 3)
        self.opt_h = spin(1, 20000, t.brush_height_hz, 10, " Hz", "Brush height in Hz (spectral-size mode)",
                          lambda v: t.set(brush_height_hz=v), 0)
        self.opt_hard = spin(0, 1, t.brush_hardness, 0.05, "", "Hardness: fraction of the radius at full strength",
                             lambda v: t.set(brush_hardness=v), 2)
        self.opt_reduce = spin(-96, -0.5, t.reduce_db, 1, " dB", "Reduce brush strength (one stroke applies this once)",
                               lambda v: t.set(reduce_db=v))
        self.opt_amp = spin(0.5, 24, t.amplify_db, 1, " dB", "Amplify brush strength", lambda v: t.set(amplify_db=v))
        self.opt_shape = QComboBox()
        self.opt_shape.addItem("Round", False)
        self.opt_shape.addItem("Square", True)
        self.opt_shape.currentIndexChanged.connect(lambda: t.set(brush_square=self.opt_shape.currentData()))
        self._opt_widgets = {}
        for key, lab, w in (("size", "Size", self.opt_size), ("mode", "", self.opt_mode), ("w", "W", self.opt_w),
                            ("h", "H", self.opt_h), ("hard", "Hardness", self.opt_hard), ("shape", "", self.opt_shape),
                            ("reduce", "Strength", self.opt_reduce), ("amp", "Strength", self.opt_amp)):
            acts = []
            if lab:
                acts.append(tb.addWidget(QLabel(f" {lab}")))
            acts.append(tb.addWidget(w))
            self._opt_widgets[key] = acts
        tb.addSeparator()
        self.scope_label = QLabel()
        self.scope_label.setToolTip("Which channels selections and edits affect (change in the Selection panel)")
        tb.addWidget(self.scope_label)
        self.ctl.tools.changed.connect(self._sync_tool_options)
        self._sync_tool_options()

    def _sync_tool_options(self):
        t = self.ctl.tools
        label = next((lab for tid, lab, _g, _tip in TOOLS if tid == t.tool), t.tool)
        self.tool_label.setText(f"<b>{label}</b>")
        brush = t.tool in ("sel_brush", "reduce_brush", "amplify_brush", "transfer_brush")
        vis = {"size": brush and t.brush_size_mode == "screen", "mode": brush, "w": brush and t.brush_size_mode == "spectral",
               "h": brush and t.brush_size_mode == "spectral", "hard": brush, "shape": brush,
               "reduce": t.tool == "reduce_brush", "amp": t.tool == "amplify_brush"}
        for k, acts in self._opt_widgets.items():
            for a in acts:
                a.setVisible(vis[k])
        n = self.ctl.session.project.channels if self.ctl.session else 2
        self.scope_label.setText(f"Channels: {channel_scope_label(t.channel_scope, n)}")
        self.actions.refresh()

    def _dock(self, name, title, widget, area=Qt.RightDockWidgetArea, scroll=True):
        d = QDockWidget(title, self)
        d.setObjectName(f"dock_{name}")
        d.setFeatures(QDockWidget.DockWidgetClosable | QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        if scroll:
            sa = QScrollArea()
            sa.setWidgetResizable(True)
            sa.setWidget(widget)
            sa.setFrameShape(QScrollArea.NoFrame)
            d.setWidget(sa)
        else:
            d.setWidget(widget)
        self.addDockWidget(area, d)
        self.panels_menu.addAction(d.toggleViewAction())
        return d

    def _build_docks(self):
        self.docks = {}
        panels = [
            ("edit", "Reduce Selected Sound", "spledio.ui.panels.edit_panel", "EditPanel"),
            ("areas", "Adjusted Areas", "spledio.ui.panels.areas_panel", "AreasPanel"),
            ("layers", "Layers", "spledio.ui.panels.layers_panel", "LayersPanel"),
            ("selection", "Selection", "spledio.ui.panels.selection_panel", "SelectionPanel"),
            ("resonance", "Resonance Workflow", "spledio.ui.panels.resonance_panel", "ResonancePanel"),
            ("display", "Display", "spledio.ui.panels.display_panel", "DisplayPanel"),
            ("history", "History", "spledio.ui.panels.history_panel", "HistoryPanel"),
        ]
        import importlib

        for key, title, mod, cls in panels:
            try:
                W = getattr(importlib.import_module(mod), cls)
                w = W(self.ctl, self.actions)
            except Exception as e:  # a broken optional panel must not take the editor down
                w = QPlainTextEdit(f"{title} panel failed to load:\n{e}")
                w.setReadOnly(True)
            self.docks[key] = self._dock(key, title, w)
        self.tabifyDockWidget(self.docks["edit"], self.docks["areas"])
        self.tabifyDockWidget(self.docks["edit"], self.docks["resonance"])
        self.tabifyDockWidget(self.docks["selection"], self.docks["display"])
        self.tabifyDockWidget(self.docks["selection"], self.docks["history"])
        self.docks["areas"].raise_()
        self.docks["selection"].raise_()
        self.resizeDocks([self.docks["edit"]], [330], Qt.Horizontal)

    def _build_status(self):
        sb = self.statusBar()
        self.st_cursor = QLabel("")
        self.st_cursor.setMinimumWidth(300)
        self.st_sel = QLabel("")
        self.st_fmt = QLabel("")
        self.st_play = QLabel("")
        self.st_peak = QLabel("")
        self.st_busy = QProgressBar()
        self.st_busy.setMaximumWidth(120)
        self.st_busy.setRange(0, 0)  # indeterminate: we do not fake percentages
        self.st_busy.setVisible(False)
        self.st_busy_label = QLabel("")
        for w in (self.st_cursor, self.st_sel):
            sb.addWidget(w)
        for w in (self.st_busy_label, self.st_busy, self.st_peak, self.st_play, self.st_fmt):
            sb.addPermanentWidget(w)
        sb.showMessage("Open an audio file (Ctrl+O) or drop one here. Nothing is uploaded; everything runs locally.")

    # ============================================================ signals
    def _connect(self):
        c = self.ctl
        self.editor.cursorInfo.connect(self.st_cursor.setText)
        for sig in (c.sessionReplaced, c.selectionChanged, c.historyChanged, c.previewChanged, c.structureChanged,
                    c.propsChanged, c.playbackChanged, c.busyChanged, c.dirtyChanged, c.areasChanged, c.jobEnded):
            sig.connect(self.refresh_actions)
        c.view.changed.connect(lambda _w: self.actions.refresh())
        c.statusMessage.connect(lambda t, ms: self.statusBar().showMessage(t, ms))
        c.busyIndicator.connect(self._on_busy)
        c.warning.connect(self._on_warning)
        c.error.connect(lambda title, text: QMessageBox.critical(self, title, text))
        c.playheadChanged.connect(self._on_playhead)
        c.requestExportLayers.connect(self.export_dialog)
        c.dirtyChanged.connect(lambda _d: self._update_title())
        c.sessionReplaced.connect(self._update_title)
        self._clip_timer = QTimer(self)
        self._clip_timer.setInterval(500)
        self._clip_timer.timeout.connect(self._update_play_status)
        self._clip_timer.start()
        QApplication.instance().focusChanged.connect(self._on_focus_changed)

    @Slot(object, object)
    def _on_focus_changed(self, *_):
        try:
            self._update_text_safety()
        except RuntimeError:  # window or its actions already deleted (app shutting down)
            pass

    def _update_text_safety(self):
        """Single-key shortcuts must not fire while typing in a text/number field."""
        fw = QApplication.focusWidget()
        typing = isinstance(fw, (QLineEdit, QAbstractSpinBox, QPlainTextEdit))
        for spec in self.actions.specs.values():
            if spec.text_safe or spec.id in CANVAS_KEY_ACTIONS:
                continue
            act = self.actions.actions[spec.id]
            seqs = [s for s in act.shortcuts()]
            single = [s for s in seqs if s.count() == 1 and not (s[0].keyboardModifiers() & (Qt.ControlModifier | Qt.MetaModifier))]
            if not single:
                continue
            act.setShortcutContext(Qt.WidgetShortcut if typing else Qt.WindowShortcut)

    def refresh_actions(self, *_):
        self.actions.refresh()
        s = self.ctl.session
        if s is None:
            self.st_sel.setText("")
            self.st_fmt.setText("")
            return
        p = s.project
        ch = {1: "mono", 2: "stereo"}.get(p.channels, f"{p.channels} ch")
        self.st_fmt.setText(f"{p.sample_rate} Hz · {ch} · {p.storage_dtype} · {p.duration:.3f} s")
        lay = s.active_layer
        sel = s.selection_label() if s.selection is not None else "no selection"
        self.st_sel.setText(f"Editing: {lay.name if lay else '-'} · {sel} · {channel_scope_label(self.ctl.tools.channel_scope, p.channels)}")
        self._sync_tool_options_scope()

    def _sync_tool_options_scope(self):
        n = self.ctl.session.project.channels if self.ctl.session else 2
        self.scope_label.setText(f"Channels: {channel_scope_label(self.ctl.tools.channel_scope, n)}")

    def _on_busy(self, busy, label):
        self.st_busy.setVisible(busy)
        self.st_busy_label.setText(f"{label}… (Esc cancels)" if busy else "")
        QApplication.restoreOverrideCursor() if not busy else None

    def _on_warning(self, text):
        self.statusBar().showMessage("⚠ " + text, 10000)
        if "peaks" in text or "locked" in text or "device" in text.lower():
            QMessageBox.warning(self, APP_NAME, text)

    def _on_playhead(self, pos):
        s = self.ctl.session
        if s is None:
            return
        self.time_label.setText(f"{_fmt_time(pos / s.sample_rate)} / {_fmt_time(s.project.duration)}")

    def _update_play_status(self):
        st = self.ctl.playback.state()
        aud = self.ctl.audition.label if self.ctl.audition.mode != "mix" else "Mix"
        txt = ("▶ " if st.playing else "■ ") + aud
        if st.device_name:
            txt += f" · {st.device_name}"
        if st.error:
            txt += f" · ⚠ {st.error}"
        self.st_play.setText(txt)

    def _update_title(self, *_):
        s = self.ctl.session
        if s is None:
            self.setWindowTitle(APP_NAME)
            return
        name = os.path.basename(s.path) if s.path else f"{s.project.name} (unsaved project)"
        self.setWindowTitle(f"{'• ' if s.dirty else ''}{name} — {APP_NAME}")
        self.setWindowModified(s.dirty)

    # ============================================================ keyboard
    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key_Escape:
            if self.editor.cancel_interaction():
                return
            if self.ctl.busy:
                self.ctl.cancel_job()
                return
            if self.ctl.session and self.ctl.session.preview is not None:
                self.ctl.cancel_preview()
                return
            if self.ctl.session and self.ctl.session.selection is not None:
                self.ctl.session.clear_selection()
                return
        if ev.key() in (Qt.Key_Return, Qt.Key_Enter) and self.editor.finish_polygon():
            return
        super().keyPressEvent(ev)

    # ============================================================ files
    def _confirm_discard(self) -> bool:
        s = self.ctl.session
        if s is None or not s.dirty:
            return True
        r = QMessageBox.question(self, "Unsaved changes", "The current project has unsaved changes. Save them first?",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if r == QMessageBox.Save:
            return self.save_project()
        return r == QMessageBox.Discard

    def _last_dir(self):
        return self.settings.value("last_dir", os.path.expanduser("~"))

    def open_audio_dialog(self):
        if not self._confirm_discard():
            return
        p, _ = QFileDialog.getOpenFileName(self, "Open audio", self._last_dir(), AUDIO_FILTER)
        if p:
            self.open_path(p)

    def open_path(self, p: str):
        self.settings.setValue("last_dir", os.path.dirname(p))
        if p.lower().endswith(PROJECT_EXTENSION):
            self.open_project(p)
        else:
            self.ctl.open_audio(p, on_done=self._after_open)

    def _after_open(self):
        self.refresh_actions()
        self._update_title()
        self.editor.setFocus()

    def open_project_dialog(self):
        if not self._confirm_discard():
            return
        p, _ = QFileDialog.getOpenFileName(self, "Open project", self._last_dir(), PROJECT_FILTER)
        if p:
            self.open_project(p)

    def open_project(self, p: str, relink: dict | None = None, allow_mismatch: bool = False,
                     recovered_from: dict | None = None):
        from spledio.persistence.project_file import load_project

        def fn(cancel):
            return load_project(p, relink=relink, allow_checksum_mismatch=allow_mismatch)

        def on_error(e):
            if isinstance(e, MissingMediaError):
                relink = dict(relink_in)
                for a in e.missing:
                    QMessageBox.information(self, "Relink source audio",
                                            f"The source file for this project was not found:\n{a.get('path')}\n\nLocate it (it is checked by SHA-256).")
                    np_, _ = QFileDialog.getOpenFileName(self, f"Locate {os.path.basename(a.get('path', ''))}",
                                                         os.path.dirname(p), AUDIO_FILTER)
                    if not np_:
                        return
                    relink[a["id"]] = np_
                self.open_project(p, relink=relink, recovered_from=recovered_from)
            elif isinstance(e, AssetMismatchError):
                r = QMessageBox.warning(self, "Different file", f"{e}.\nUse it anyway? Edits may not line up with different audio.",
                                        QMessageBox.Yes | QMessageBox.No)
                if r == QMessageBox.Yes:
                    self.open_project(p, relink=relink_in, allow_mismatch=True, recovered_from=recovered_from)
            elif isinstance(e, ProjectFormatError):
                QMessageBox.critical(self, "Cannot open project", str(e))
            else:
                QMessageBox.critical(self, "Cannot open project", f"{type(e).__name__}: {e}")

        def on_done(sess):
            if recovered_from is not None:
                sess.mark_recovered(recovered_from.get("project_path"))
            self.ctl.set_session(sess)
            self._after_open()
            if recovered_from is not None:
                self.statusBar().showMessage("Recovered unsaved work – save it to keep it", 8000)

        relink_in = relink or {}
        self.ctl.run_job(f"Open {os.path.basename(p)}", fn, on_done, on_error)

    def save_project(self, ask: bool = False, portable: bool = False) -> bool:
        s = self.ctl.session
        if s is None:
            return False
        path = s.path
        if ask or not path:
            default = path or os.path.join(self._last_dir(), (s.project.name or "project") + PROJECT_EXTENSION)
            path, _ = QFileDialog.getSaveFileName(self, "Save portable project" if portable else "Save project", default, PROJECT_FILTER)
            if not path:
                return False
            if not path.endswith(PROJECT_EXTENSION):
                path += PROJECT_EXTENSION
        try:
            s.view_state = self.ctl.view.to_dict()
            s.save(path, portable=portable)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Save failed", f"The project was not saved (the previous file is intact):\n{e}")
            return False
        self.statusBar().showMessage(f"Saved {os.path.basename(path)}" + (" (portable, source audio embedded)" if portable else ""), 5000)
        self._update_title()
        try:
            from spledio.persistence.recovery import clear_recovery

            clear_recovery(s.project.id)
        except Exception:
            pass
        return True

    def export_dialog(self, layer_ids=None):
        s = self.ctl.session
        if s is None:
            return
        from spledio.ui.export_dialog import ExportDialog

        dlg = ExportDialog(s, self, layer_ids=layer_ids or None, default_dir=self._last_dir())
        if dlg.exec() != ExportDialog.Accepted:
            return
        req = dlg.request()
        self._run_export(req)

    def _run_export(self, req):
        from spledio.audio.export import ClippingError

        s = self.ctl.session

        def fn(cancel):
            if req["scope"] == "stems":
                root, ext = os.path.splitext(req["path"])
                out = []
                for l in s.project.layers:
                    safe = "".join(ch if ch.isalnum() or ch in "-_ " else "_" for ch in l.name)[:60]
                    out.append(s.export(f"{root}-{safe}{ext}", req["settings"], scope="layers", layer_ids=[l.id],
                                        time_range_s=req["time_range"]))
                return out
            return [s.export(req["path"], req["settings"], scope=req["scope"], layer_ids=req["layer_ids"],
                             time_range_s=req["time_range"])]

        def done(results):
            r = results[0]
            extra = []
            if getattr(r, "gain_applied_db", 0):
                extra.append(f"level reduced {r.gain_applied_db:+.2f} dB")
            if getattr(r, "limited_samples", 0):
                extra.append(f"{r.limited_samples} samples limited")
            if getattr(r, "clipped_samples", 0):
                extra.append(f"{r.clipped_samples} samples clipped (your choice)")
            names = ", ".join(os.path.basename(x.path) for x in results)
            self.statusBar().showMessage(f"Exported {names}" + (f" – {'; '.join(extra)}" if extra else ""), 10000)

        def on_error(e):
            if isinstance(e, ClippingError):
                r = QMessageBox.question(
                    self, "Peaks exceed full scale",
                    f"{e}\n\nInteger formats cannot store these peaks. Choose 'Reduce whole-file level', 'Basic peak limiter' "
                    "or 32-bit float in the export dialog. Open the export dialog again?")
                if r == QMessageBox.Yes:
                    self.export_dialog(req["layer_ids"])
            else:
                QMessageBox.critical(self, "Export failed", f"{type(e).__name__}: {e}")

        self.ctl.run_job("Export", fn, done, on_error)

    def delete_time(self):
        s = self.ctl.session
        b = s.selection.bounds(s.project.duration, s.project.nyquist)
        r = QMessageBox.warning(self, "Delete time",
                                f"Remove {b[0]:.3f}–{b[1]:.3f} s from ALL {len(s.project.layers)} layer(s)?\n\n"
                                "This SHORTENS the recording (unlike Delete Spectral Content). All layers are rippled together "
                                "to stay in sync. Undo restores it.", QMessageBox.Ok | QMessageBox.Cancel)
        if r == QMessageBox.Ok:
            self.ctl.safe(s.delete_time)

    def crop_time(self):
        s = self.ctl.session
        r = QMessageBox.warning(self, "Crop time", "Keep only the selected time span in ALL layers? This shortens the recording. Undo restores it.",
                                QMessageBox.Ok | QMessageBox.Cancel)
        if r == QMessageBox.Ok:
            self.ctl.safe(s.crop_time)

    def insert_silence(self):
        s = self.ctl.session
        secs, ok = QInputDialog.getDouble(self, "Insert silence", "Seconds of silence to insert at the playhead (all layers):",
                                          1.0, 0.001, 3600, 3)
        if ok:
            self.ctl.safe(s.insert_silence, self.ctl.playback.position / s.sample_rate, secs)

    # ============================================================ dialogs
    def command_palette(self):
        try:
            from spledio.ui.command_palette import open_command_palette

            open_command_palette(self, self.actions)
        except ImportError as e:
            QMessageBox.information(self, APP_NAME, f"Command palette unavailable: {e}")

    def tutorials(self):
        try:
            from spledio.ui.tutorials import TutorialsDialog

            TutorialsDialog(self, self.ctl, self.actions).exec()
        except ImportError as e:
            QMessageBox.information(self, APP_NAME, f"Tutorials unavailable: {e}")

    def shortcuts_dialog(self):
        try:
            from spledio.ui.shortcuts_dialog import ShortcutsDialog

            ShortcutsDialog(self.actions, self).exec()
        except ImportError as e:
            QMessageBox.information(self, APP_NAME, f"Shortcut editor unavailable: {e}")
        except TypeError:
            from spledio.ui.shortcuts_dialog import ShortcutsDialog

            ShortcutsDialog(self, self.actions).exec()

    def _apply_saved_shortcuts(self):
        try:
            from spledio.ui.shortcuts_dialog import load_custom_shortcuts

            load_custom_shortcuts(self.actions)
        except Exception:
            pass

    def licenses_dialog(self):
        from spledio.ui.licenses_dialog import LicensesDialog

        LicensesDialog(self).exec()

    def about_html(self) -> str:
        return (
            f"<h3>{APP_NAME} {__version__}</h3><p>{APP_TAGLINE} (working title: {APP_WORKING_TITLE}).</p>"
            "<p>Licensed under the GNU General Public License version 3 only (GPL-3.0-only).</p>"
            "<p>Privacy: all processing is local. No audio is uploaded, no analytics, no telemetry, no account.</p>"
            "<p>An independent implementation; not affiliated with or derived from any commercial spectral editor.</p>"
            f"<p>Built with {_component_versions()}.</p>"
            "<p>Audio file support uses libsndfile, which includes FLAC, Ogg, Vorbis, Opus, mpg123 and LAME.<br>"
            "mpg123: Copyright (c) 1995-2020 by Michael Hipp and others, "
            "free software under the terms of the LGPL v2.1.<br>"
            "This software uses LAME, the MP3 encoder: "
            "<a href=\"https://www.mp3dev.org/\">www.mp3dev.org</a>.<br>"
            "Text rendering: this software is based in part on the work of the FreeType Team "
            "(<a href=\"https://freetype.org/\">freetype.org</a>), through Qt.</p>"
            "<p>Licenses of all included components: <b>Help ▸ Licenses and Notices</b>.</p>")

    def about(self):
        QMessageBox.about(self, f"About {APP_NAME}", self.about_html())

    # ============================================================ layout persistence
    def _toggle_focus(self, on):
        if on and self._focus_saved is None:
            self._focus_saved = self.saveState()
            for d in self.docks.values():
                d.hide()
            for tb in self.findChildren(QToolBar):
                if tb.objectName() not in ("tb_transport", "tb_tools"):
                    tb.hide()
        elif not on and self._focus_saved is not None:
            self.restoreState(self._focus_saved)
            self._focus_saved = None
            QTimer.singleShot(0, self._hide_stale_tab_bars)
        self.actions.refresh()

    def _hide_stale_tab_bars(self) -> None:
        """Work around a Qt 6 bug: tabifyDockWidget and every restoreState leave the tab bars of the
        previous dock layout on screen, drawn over the panels at their old place (a loose
        "Resonance Workflow" tab over the Reduce panel's buttons). Hide every dock tab bar, then
        lay the window out again: Qt shows again exactly the bars it uses."""
        bars = [tb for tb in self.findChildren(QTabBar, options=Qt.FindChildOption.FindDirectChildrenOnly)
                if tb.isVisible()]
        if not bars:
            return
        for tb in bars:
            tb.hide()
        lay = self.layout()
        lay.activate()  # finish any pending relayout first: until then geometry() is empty
        r = lay.geometry()
        if r.isValid() and not r.isEmpty():
            lay.setGeometry(r)  # same size, so the panels keep their widths
        else:
            lay.invalidate()  # the relayout this asks for shows the bars Qt uses

    def showEvent(self, ev):
        super().showEvent(ev)
        if not getattr(self, "_shown_once", False):
            self._shown_once = True  # the saved layout was restored before the first show
            QTimer.singleShot(0, self._hide_stale_tab_bars)

    def _restore_layout(self):
        st = self.settings.value("window_state")
        geo = self.settings.value("window_geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        if st is not None:
            self.restoreState(st)

    def closeEvent(self, ev: QCloseEvent):
        if not self._confirm_discard():
            ev.ignore()
            return
        if self._focus_saved is not None:
            self.restoreState(self._focus_saved)
        self.settings.setValue("window_state", self.saveState())
        self.settings.setValue("window_geometry", self.saveGeometry())
        try:
            from spledio.persistence.recovery import clear_recovery

            if self.ctl.session is not None:
                clear_recovery(self.ctl.session.project.id)
        except Exception:
            pass
        self.ctl.shutdown()
        ev.accept()

    # drag & drop
    def dragEnterEvent(self, ev):
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()

    def dropEvent(self, ev):
        urls = [u.toLocalFile() for u in ev.mimeData().urls() if u.isLocalFile()]
        if urls and self._confirm_discard():
            self.open_path(urls[0])


def _fmt_time(t: float) -> str:
    m = int(t // 60)
    return f"{m}:{t - 60 * m:06.3f}"
