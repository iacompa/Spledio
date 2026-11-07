"""Built-in tutorials (Help ▸ Tutorials…).

Six short, concrete walkthroughs. Command names and shortcuts in the step
text are read from the action registry at display time (``{action.id}``
tokens), so they always match the menus - including shortcuts the user has
remapped. Steps that correspond to one command have a "Do this step" button
that triggers the very same QAction. The tutorial window is not modal, so
you can draw selections on the spectrogram while following it.
"""

from __future__ import annotations

import html
import re
import weakref
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from spledio.ui.panels.edit_panel import ActionBinder, note_label


@dataclass(frozen=True)
class TutorialStep:
    text: str  # may contain {action.id} tokens
    action: str | None = None  # "Do this step" runs this registry action
    activate: bool = False  # checkable action (tool / mode): switch it on rather than toggling


@dataclass(frozen=True)
class Tutorial:
    id: str
    title: str
    summary: str
    steps: tuple[TutorialStep, ...]
    limits: str = ""


S = TutorialStep

TUTORIALS: tuple[Tutorial, ...] = (
    Tutorial(
        "quick_level", "Turn any part up or down (quickest)",
        "Draw a box around a sound and change its level with − / + (1 dB per click) or the arrow keys. "
        "Every part you change stays outlined, so you can come back to it, change it again or remove it.",
        (
            S("Open a recording with {file.open_audio}.", "file.open_audio"),
            S("Choose {tool.rect} and drag a box around the sound you want to change (left–right = time, "
              "bottom–top = pitch).", "tool.rect", True),
            S("A small level bar appears next to the box. Click − to make that part quieter or + to make it "
              "louder: 1 dB per click, hold the button to keep going, or drag the slider. Nothing outside the box "
              "changes and no time is cut."),
            S("The keyboard does the same while the spectrogram has focus: {area.quieter} / {area.louder} = 1 dB, "
              "{area.quieter_big} / {area.louder_big} = 6 dB, {area.silence} = silence.", "area.quieter"),
            S("Press {transport.play_pause} (or ▶ on the level bar) to listen – changes are heard while it plays."),
            S("Draw another box to change another part. Each part keeps its own level (\"Area 1 · −6 dB\").",
              "tool.rect", True),
            S("Click an outlined part later to pick it again. {edit.delete_spectral} (or ✕ on the bar) removes "
              "its change – the audio there comes back exactly.", None),
            S("Many parts on screen? {view.show_areas} hides the outlines (display only). The Adjusted Areas "
              "panel lists them all.", "view.show_areas"),
        ),
        "Below −48 dB the next step is silence. Area changes are undoable ({edit.undo}); quick repeated clicks "
        "on one part count as one undo step.",
    ),
    Tutorial(
        "resonance", "Reduce a ringing resonance",
        "A resonance is a narrow frequency that keeps ringing longer than the rest of the sound. "
        "You select that band and turn it down moderately – you do not delete it.",
        (
            S("Open the recording with {file.open_audio}. The source file is never modified.", "file.open_audio"),
            S("Find the thin, bright horizontal line that rings on after the notes, and zoom in on it with "
              "{view.zoom_in}.", "view.zoom_in"),
            S("Choose {tool.freq} (whole duration) or {tool.rect} (only where it rings) and drag tightly around "
              "the line.", "tool.rect", True),
            S("Press {audition.selected}: you should hear mostly the ring. If you hear the melody, the band is too "
              "wide.", "audition.selected"),
            S("In the Reduce selected sound panel keep the moderate −6 dB default and press {module.preview}. "
              "Nothing is changed yet.", "module.preview"),
            S("While it plays, use {audition.ab} to compare, then {audition.removed} to hear exactly what the "
              "reduction takes away.", "audition.ab"),
            S("If it sounds right, press {module.apply} (one undo step); otherwise {module.cancel} and adjust the "
              "band or the amount.", "module.apply"),
        ),
        "The Resonance Workflow panel can list candidate frequencies. It only selects them for you to audition; "
        "a musical note can look exactly like a resonance, and nothing is reduced automatically.",
    ),
    Tutorial(
        "whistle", "Remove a narrow whistle",
        "A whistle or tone (e.g. a 15.7 kHz line, a feedback squeal) is a steady thin line, sometimes with fainter "
        "copies at 2×, 3× its frequency.",
        (
            S("Zoom in on the line with {view.zoom_in} so you can see where it starts and stops.", "view.zoom_in"),
            S("Choose {tool.rect} and drag a box that covers the line for exactly the time it sounds.", "tool.rect",
              True),
            S("For its harmonics switch to {select.mode_add} and drag a box over each copy.", "select.mode_add", True),
            S("Check with {audition.selected} that you hear only the whistle.", "audition.selected"),
            S("Remove it with {edit.delete_spectral} (or {edit.mute}). The recording keeps its full length.",
              "edit.delete_spectral"),
            S("Listen to the same stretch with {audition.all_freqs}. If you hear a hole, undo and use a partial "
              "reduction (e.g. −12 dB) in the Reduce selected sound panel instead.", "audition.all_freqs"),
            S("Use {audition.removed} to confirm nothing else was taken out.", "audition.removed"),
        ),
        "Wanted sound in the same time-frequency cells is removed too: the edit cannot tell the whistle from "
        "anything else inside the box.",
    ),
    Tutorial(
        "extract", "Extract a component to a layer",
        "Move one part of the sound (a note, a bird call, a hum) to its own layer so you can treat, mute or export "
        "it separately.",
        (
            S("Select the component with {tool.rect} or {tool.freq}.", "tool.rect", True),
            S("Audition it with {audition.selected} – what you hear is exactly what will move.", "audition.selected"),
            S("Use {edit.extract}. A new time-aligned layer appears in the Layers panel; the original layer keeps "
              "the remainder.", "edit.extract"),
            S("The two layers add up to the original exactly, so the mix sounds unchanged until you change one "
              "of them."),
            S("Use {layer.solo_active} or {layer.mute_active} (or the S/M buttons in the Layers panel) to hear each "
              "part on its own.", "layer.solo_active"),
            S("Need the original untouched? {edit.copy_to_layer} copies instead – the copy is summed with the "
              "original, so overlapping content gets louder (up to +6 dB).", "edit.copy_to_layer"),
        ),
        "Extraction is a time-frequency mask, not source separation: everything inside the selection moves, "
        "including other sounds that overlap it.",
    ),
    Tutorial(
        "click", "Reduce a click without shortening audio",
        "A click is a thin vertical line across many frequencies. You reduce it in place; the timeline never gets "
        "shorter.",
        (
            S("Find the click and zoom in with {view.zoom_in} until it is several pixels wide.", "view.zoom_in"),
            S("Select only its duration with {tool.time} (all frequencies), or {tool.rect} if it covers only part "
              "of the spectrum. Keep the selection as short as possible.", "tool.time", True),
            S("Press {audition.all_freqs} to make sure you caught the click and not the note around it.",
              "audition.all_freqs"),
            S("Set the amount to about −12 dB in the Reduce selected sound panel and press {module.preview}.",
              "module.preview"),
            S("Compare with {audition.ab}. If the click is still audible, cancel, set a deeper amount and preview "
              "again.", "audition.ab"),
            S("Apply with {module.apply}. Spectral edits change levels only: the recording keeps its exact length "
              "and every later event stays where it was.", "module.apply"),
        ),
        "There is no automatic de-click module yet. This reduces everything inside the selected region, "
        "including any music under the click, so keep the region small.",
    ),
    Tutorial(
        "difference", "Hear the difference signal",
        "The difference (original minus processed) tells you exactly what an edit removes – the most reliable way "
        "to catch damage to the wanted sound.",
        (
            S("Make a selection and press {module.preview} (or apply an edit).", "module.preview"),
            S("Press {audition.removed}. You hear what is being removed, time-aligned with the original.",
              "audition.removed"),
            S("If you hear parts of the wanted sound, the selection overlaps it: narrow the band or use a smaller "
              "reduction."),
            S("Press {audition.remainder} to hear the layer with the selected content removed completely.",
              "audition.remainder"),
            S("Use {audition.ab} to switch between before and after at the same playback position.", "audition.ab"),
        ),
        "Without a pending preview the difference refers to the most recent applied edit.",
    ),
    Tutorial(
        "export", "Export safely",
        "Export renders the edited mix to a new audio file; your project and the original recording stay as they "
        "are.",
        (
            S("Save the project with {file.save} so you can come back to the edits later.", "file.save"),
            S("Choose {file.export}. The original source file is protected: Export refuses to overwrite it.",
              "file.export"),
            S("32-bit float keeps everything exactly, including peaks above 0 dBFS – nothing is clipped or "
              "normalised."),
            S("For 16/24-bit, if the mix goes above 0 dBFS you choose what happens: stop with an error (the "
              "default), lower the whole level, limit, or clip – never silently."),
            S("Integer formats get TPDF dither by default when samples must be rounded; an unedited file at its own "
              "bit depth is written unchanged, and float export is never dithered."),
            S("The file is written to a temporary file first and then moved into place, so a failed export never "
              "leaves a half-written file or damages an existing one."),
        ),
        "Listen to the exported file in another player before you delete anything.",
    ),
)

# Names used when an action id is not registered (keeps the text readable).
FALLBACK_NAMES = {
    "file.open_audio": "File ▸ Open Audio…", "file.export": "File ▸ Export Audio…", "file.save": "File ▸ Save Project",
    "view.zoom_in": "Zoom In", "tool.freq": "the Frequency range tool", "tool.rect": "the Rectangle tool",
    "tool.time": "the Time range tool", "select.mode_add": "the Add selection mode",
    "audition.selected": "Hear Selected Frequencies", "audition.all_freqs": "Hear All Frequencies",
    "audition.remainder": "Hear Remainder", "audition.removed": "Hear What Is Being Removed",
    "audition.ab": "A/B Compare", "module.preview": "Preview", "module.apply": "Apply", "module.cancel": "Cancel",
    "edit.delete_spectral": "Delete Spectral Content", "edit.mute": "Mute Region",
    "edit.extract": "Extract to New Layer", "edit.copy_to_layer": "Copy to New Layer",
    "layer.solo_active": "Solo Active Layer", "layer.mute_active": "Mute Active Layer",
}

_TOKEN = re.compile(r"\{([a-z_]+\.[a-z0-9_]+)\}")


def action_ref(actions, aid: str) -> str:
    """HTML reference to a command: bold menu path + label, plus its current shortcut."""
    specs = getattr(actions, "specs", {}) or {}
    spec = specs.get(aid)
    if spec is None:
        return f"<b>{html.escape(FALLBACK_NAMES.get(aid, aid))}</b>"
    path = " ▸ ".join(p for p in spec.menu.split("/") if p)
    name = f"{path} ▸ {spec.label}" if path else spec.label
    act = getattr(actions, "actions", {}).get(aid)
    seqs = act.shortcuts() if act is not None else []
    sc = next((s.toString(QKeySequence.NativeText) for s in seqs if not s.isEmpty()), "")
    return f"<b>{html.escape(name)}</b>" + (f" ({html.escape(sc)})" if sc else "")


def render_step_text(actions, text: str) -> str:
    out, pos = [], 0
    for m in _TOKEN.finditer(text):
        out.append(html.escape(text[pos:m.start()]))
        out.append(action_ref(actions, m.group(1)))
        pos = m.end()
    out.append(html.escape(text[pos:]))
    return "".join(out)


def tutorial_action_ids() -> set[str]:
    ids = set()
    for t in TUTORIALS:
        for st in t.steps:
            ids.update(_TOKEN.findall(st.text))
            if st.action:
                ids.add(st.action)
    return ids


_open_dialog: "weakref.ref[TutorialsDialog] | None" = None


class TutorialsDialog(QDialog):
    """Tutorial browser. Accepts ``(parent, controller, actions)``, ``(parent, actions)`` or ``(actions, parent)``."""

    def __init__(self, parent=None, controller=None, actions=None):
        # tolerate the argument orders used by different callers
        if hasattr(parent, "specs"):
            parent, actions = (controller if isinstance(controller, QWidget) else None), parent
            controller = None
        elif actions is None and hasattr(controller, "specs"):
            actions, controller = controller, None
        super().__init__(parent)
        self.controller = controller
        self.actions = actions
        self.setWindowTitle("Tutorials")
        self.setObjectName("TutorialsDialog")
        self.setModal(False)
        self.resize(780, 540)
        self.binder: ActionBinder | None = None
        self.step_buttons: list[QPushButton] = []
        self.step_labels: list[QLabel] = []

        lay = QVBoxLayout(self)
        split = QSplitter(Qt.Horizontal)
        self.list = QListWidget()
        self.list.setObjectName("tutorialList")
        for t in TUTORIALS:
            self.list.addItem(t.title)
        self.list.setMaximumWidth(260)
        split.addWidget(self.list)
        self.body_scroll = QScrollArea()
        self.body_scroll.setWidgetResizable(True)
        self.body_scroll.setFrameShape(QFrame.NoFrame)
        split.addWidget(self.body_scroll)
        split.setStretchFactor(1, 1)
        lay.addWidget(split, 1)
        lay.addWidget(note_label("“Do this step” runs the same command as the menu item or shortcut named in the "
                                 "step. This window can stay open while you work."))
        box = QDialogButtonBox(QDialogButtonBox.Close)
        box.rejected.connect(self.close)
        lay.addWidget(box)
        self.list.currentRowChanged.connect(self.show_tutorial)
        self.list.setCurrentRow(0)

    def show_tutorial(self, index: int) -> None:
        if not (0 <= index < len(TUTORIALS)):
            return
        t = TUTORIALS[index]
        old_binder = self.binder
        self.binder = ActionBinder(self.actions, self)
        if old_binder is not None:
            old_binder.deleteLater()  # its action connections go with it
        self.step_buttons, self.step_labels = [], []
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(10, 4, 10, 10)
        title = QLabel(f"<h3>{html.escape(t.title)}</h3>")
        title.setObjectName("tutorialTitle")
        v.addWidget(title)
        summary = QLabel(html.escape(t.summary))
        summary.setWordWrap(True)
        v.addWidget(summary)
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        for i, st in enumerate(t.steps):
            num = QLabel(f"<b>{i + 1}.</b>")
            num.setAlignment(Qt.AlignTop | Qt.AlignRight)
            text = QLabel(render_step_text(self.actions, st.text))
            text.setWordWrap(True)
            text.setTextFormat(Qt.RichText)
            grid.addWidget(num, i, 0)
            grid.addWidget(text, i, 1)
            self.step_labels.append(text)
            if st.action:
                btn = QPushButton("Do this step")
                btn.setObjectName(f"tutorialStep{i + 1}")
                self.binder.bind(btn, st.action, activate=st.activate,
                                 tooltip=f"Runs {re.sub('<[^>]+>', '', action_ref(self.actions, st.action))}")
                grid.addWidget(btn, i, 2, Qt.AlignTop)
                self.step_buttons.append(btn)
        grid.setColumnStretch(1, 1)
        v.addLayout(grid)
        if t.limits:
            lim = QLabel(f"<b>Limits:</b> {html.escape(t.limits)}")
            lim.setWordWrap(True)
            lim.setStyleSheet("color: #f0a050;")
            v.addWidget(lim)
        v.addStretch(1)
        old = self.body_scroll.takeWidget()
        if old is not None:
            old.deleteLater()
        self.body_scroll.setWidget(w)

    def button_for_action(self, aid: str) -> QPushButton | None:
        for b in self.step_buttons:
            if self.binder.action_id_for(b) == aid:
                return b
        return None

    def refresh(self) -> None:
        if self.binder is not None:
            self.binder.refresh()

    def showEvent(self, ev) -> None:
        self.refresh()
        super().showEvent(ev)

    def exec(self) -> int:  # noqa: A003 - deliberately non-blocking
        """Show the tutorials *without* blocking, so the user can work in the editor while following them.

        If a tutorial window is already open it is raised instead of opening a second one."""
        global _open_dialog
        existing = _open_dialog() if _open_dialog is not None else None
        if existing is not None and existing is not self and existing.isVisible():
            existing.raise_()
            existing.activateWindow()
            self.deleteLater()
            return 0
        _open_dialog = weakref.ref(self)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.show()
        self.raise_()
        self.activateWindow()
        return 0


__all__ = ["TutorialsDialog", "TUTORIALS", "Tutorial", "TutorialStep", "action_ref", "render_step_text",
           "tutorial_action_ids"]
