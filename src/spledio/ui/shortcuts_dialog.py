"""Keyboard shortcut editor with conflict detection and persistence.

* ``default_shortcuts(actions)`` – the shortcuts declared by the action specs,
  captured once per registry (before any user override is applied).
* ``load_custom_shortcuts(actions)`` – apply the overrides saved in QSettings
  (organisation/application from ``spledio.branding``) at startup. Overrides
  that would collide with another command are skipped and reported.
* ``ShortcutsDialog`` – table of all commands with an editable shortcut each.
  A shortcut already used by another command is refused (Qt would otherwise
  make both ambiguous and neither would fire). Assigning a key without
  Ctrl/Alt/Cmd to a command that changes audio is allowed but warned about.
  "Reset all to defaults" restores the spec shortcuts and clears the saved
  overrides.

Overrides are stored as JSON lists of portable key strings under the
``shortcuts/<action id>`` keys; only commands that differ from their default
are stored.
"""

from __future__ import annotations

import json

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QKeySequenceEdit,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from spledio.branding import APP_NAME, APP_ORG

SETTINGS_GROUP = "shortcuts"
_DEFAULTS_ATTR = "_spledio_default_shortcuts"
_TEXT_SAFE_ATTR = "_spledio_default_text_safe"


# ------------------------------------------------------------------ helpers
def settings_store(settings: QSettings | None = None) -> QSettings:
    return settings if settings is not None else QSettings(APP_ORG, APP_NAME)


def normalize(seq: str | QKeySequence) -> str:
    """Portable text of the first chord sequence ('' for none)."""
    ks = seq if isinstance(seq, QKeySequence) else QKeySequence(seq or "")
    return ks.toString(QKeySequence.PortableText)


def native(seq: str) -> str:
    return QKeySequence(seq).toString(QKeySequence.NativeText) if seq else ""


def _spec_seqs(spec) -> tuple[str, ...]:
    raw = [spec.shortcut] if isinstance(spec.shortcut, str) else list(spec.shortcut)
    return tuple(n for n in (normalize(s) for s in raw if s) if n)


def default_shortcuts(actions) -> dict[str, tuple[str, ...]]:
    """{action id: default shortcuts (portable text)} captured from the specs the first time it is called."""
    cached = getattr(actions, _DEFAULTS_ATTR, None)
    if cached is None:
        cached = {aid: _spec_seqs(spec) for aid, spec in actions.specs.items()}
        setattr(actions, _DEFAULTS_ATTR, cached)
        setattr(actions, _TEXT_SAFE_ATTR, {aid: spec.text_safe for aid, spec in actions.specs.items()})
    # specs added after the first capture
    for aid, spec in actions.specs.items():
        if aid not in cached:
            cached[aid] = _spec_seqs(spec)
            getattr(actions, _TEXT_SAFE_ATTR)[aid] = spec.text_safe
    return dict(cached)


def current_shortcuts(actions, aid: str) -> tuple[str, ...]:
    act = actions.actions.get(aid)
    if act is not None:
        return tuple(n for n in (normalize(s) for s in act.shortcuts()) if n)
    spec = actions.specs.get(aid)
    return _spec_seqs(spec) if spec is not None else ()


def has_modifier(seq: str) -> bool:
    """True if the (first chord of the) shortcut uses Ctrl, Alt/Option or Cmd/Meta, or is a function key."""
    ks = QKeySequence(seq)
    if ks.isEmpty():
        return True
    combo = ks[0]
    mods = combo.keyboardModifiers()
    if mods & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier):
        return True
    key = combo.key()
    return Qt.Key_F1 <= key <= Qt.Key_F35


def find_conflict(actions, seq: str, exclude: str | None = None) -> str | None:
    """Id of another action already using ``seq`` (portable text), else None."""
    n = normalize(seq)
    if not n:
        return None
    for aid in actions.specs:
        if aid == exclude:
            continue
        if n in current_shortcuts(actions, aid):
            return aid
    return None


def apply_shortcuts(actions, aid: str, seqs: tuple[str, ...]) -> None:
    """Set the QAction shortcuts and keep the spec (tooltips, palette, docs) in step."""
    default_shortcuts(actions)  # make sure the defaults are captured before the spec changes
    spec = actions.specs[aid]
    act = actions.actions.get(aid)
    if act is not None:
        act.setShortcuts([QKeySequence(s) for s in seqs])
    spec.shortcut = seqs[0] if len(seqs) == 1 else (tuple(seqs) if seqs else "")
    # A key without Ctrl/Alt/Cmd must not fire while the user types into a text field: the main window
    # switches such actions to widget-only shortcuts while a text/number field has focus (text_safe=False).
    defaults_ts = getattr(actions, _TEXT_SAFE_ATTR, {}).get(aid, spec.text_safe)
    spec.text_safe = defaults_ts and all(has_modifier(s) for s in seqs)


def _store(settings: QSettings, aid: str, seqs: tuple[str, ...] | None) -> None:
    settings.beginGroup(SETTINGS_GROUP)
    try:
        if seqs is None:
            settings.remove(aid)
        else:
            settings.setValue(aid, json.dumps(list(seqs)))
    finally:
        settings.endGroup()
    settings.sync()


def saved_overrides(settings: QSettings | None = None) -> dict[str, tuple[str, ...]]:
    st = settings_store(settings)
    out: dict[str, tuple[str, ...]] = {}
    st.beginGroup(SETTINGS_GROUP)
    try:
        for key in st.childKeys():
            raw = st.value(key)
            try:
                vals = json.loads(raw) if isinstance(raw, str) else list(raw or [])
                if not isinstance(vals, list):
                    continue
                seqs = tuple(n for n in (normalize(str(v)) for v in vals) if n)
            except (ValueError, TypeError):
                continue
            out[key] = seqs
    finally:
        st.endGroup()
    return out


def load_custom_shortcuts(actions, settings: QSettings | None = None) -> list[str]:
    """Apply saved overrides to the registry's actions. Returns warnings for skipped overrides."""
    defaults = default_shortcuts(actions)
    overrides = {aid: seqs for aid, seqs in saved_overrides(settings).items() if aid in actions.specs}
    warnings: list[str] = []
    final = {aid: overrides.get(aid, current_shortcuts(actions, aid)) for aid in actions.specs}
    # drop overrides that collide with another command (repeat: dropping one can expose another)
    for _ in range(len(overrides) + 1):
        owner: dict[str, str] = {}
        bad: set[str] = set()
        for aid, seqs in final.items():
            for s in seqs:
                other = owner.get(s)
                if other is not None and other != aid:
                    for x in (aid, other):
                        if x in overrides:
                            bad.add(x)
                owner.setdefault(s, aid)
        if not bad:
            break
        for aid in bad:
            label = actions.specs[aid].label
            warnings.append(f"Saved shortcut for “{label}” ({', '.join(native(s) for s in overrides[aid])}) "
                            "conflicts with another command and was not applied")
            overrides.pop(aid, None)
            final[aid] = defaults.get(aid, ())
    for aid, seqs in overrides.items():
        apply_shortcuts(actions, aid, seqs)
    return warnings


# ------------------------------------------------------------------ dialog
class ShortcutsDialog(QDialog):
    """Edit keyboard shortcuts for every command."""

    COLS = ("Command", "Menu", "Shortcut", "Also", "Default", "")

    def __init__(self, actions, parent=None, settings: QSettings | None = None):
        super().__init__(parent)
        self.actions = actions
        self.settings = settings_store(settings)
        self.defaults = default_shortcuts(actions)
        self.setWindowTitle("Keyboard shortcuts")
        self.setObjectName("ShortcutsDialog")
        self.resize(820, 560)
        self.editors: dict[str, QKeySequenceEdit] = {}
        self._rows: dict[str, int] = {}
        self._loading = False

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Click a shortcut field and press the new key combination. A combination already used "
                             "by another command is refused. Changes are saved immediately."))
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filter commands…")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.textChanged.connect(self._apply_filter)
        lay.addWidget(self.filter_edit)
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setObjectName("shortcutsTable")
        self.table.setHorizontalHeaderLabels(list(self.COLS))
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        for i in range(1, len(self.COLS)):
            hh.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        lay.addWidget(self.table, 1)
        self.message = QLabel()
        self.message.setObjectName("shortcutMessage")
        self.message.setWordWrap(True)
        lay.addWidget(self.message)
        bottom = QHBoxLayout()
        self.reset_all_button = QPushButton("Reset all to defaults")
        self.reset_all_button.clicked.connect(self._on_reset_all_clicked)
        bottom.addWidget(self.reset_all_button)
        bottom.addStretch(1)
        box = QDialogButtonBox(QDialogButtonBox.Close)
        box.rejected.connect(self.reject)
        box.accepted.connect(self.accept)
        bottom.addWidget(box)
        lay.addLayout(bottom)
        self._populate()

    # ------------------------------------------------------------------ table
    def _populate(self) -> None:
        self._loading = True
        try:
            specs = sorted(self.actions.specs.values(), key=lambda s: (s.menu.lower(), s.label.lower()))
            self.table.setRowCount(len(specs))
            for row, spec in enumerate(specs):
                self._rows[spec.id] = row
                name = QTableWidgetItem(spec.label)
                tip = spec.tooltip + ("\nChanges audio" if spec.changes_audio else "")
                name.setToolTip(tip)
                name.setData(Qt.UserRole, spec.id)
                self.table.setItem(row, 0, name)
                self.table.setItem(row, 1, QTableWidgetItem(spec.menu.replace("/", " ▸ ")))
                ed = QKeySequenceEdit()
                ed.setObjectName(f"shortcut_{spec.id}")
                try:
                    ed.setMaximumSequenceLength(1)
                except AttributeError:  # Qt < 6.5
                    pass
                ed.setClearButtonEnabled(True)
                ed.setToolTip(f"Shortcut for “{spec.label}”. Press a key combination; clear it with the ✕ button.")
                ed.keySequenceChanged.connect(lambda seq, aid=spec.id: self._on_edited(aid, seq))
                self.editors[spec.id] = ed
                self.table.setCellWidget(row, 2, ed)
                self.table.setItem(row, 3, QTableWidgetItem())
                self.table.setItem(row, 4, QTableWidgetItem(", ".join(native(s) for s in self.defaults.get(spec.id, ()))
                                                            or "—"))
                reset = QPushButton("Default")
                reset.setToolTip("Restore this command's default shortcut")
                reset.clicked.connect(lambda _=False, aid=spec.id: self.reset_to_default(aid))
                self.table.setCellWidget(row, 5, reset)
                self._show(spec.id)
        finally:
            self._loading = False

    def _show(self, aid: str) -> None:
        seqs = current_shortcuts(self.actions, aid)
        ed = self.editors[aid]
        ed.blockSignals(True)
        ed.setKeySequence(QKeySequence(seqs[0]) if seqs else QKeySequence())
        ed.blockSignals(False)
        row = self._rows[aid]
        self.table.item(row, 3).setText(", ".join(native(s) for s in seqs[1:]))
        custom = seqs != self.defaults.get(aid, ())
        f = self.table.item(row, 0).font()
        f.setItalic(custom)
        self.table.item(row, 0).setFont(f)

    def _apply_filter(self, text: str) -> None:
        q = text.lower().strip()
        for aid, row in self._rows.items():
            spec = self.actions.specs[aid]
            hay = f"{spec.label} {spec.menu} {aid} {' '.join(current_shortcuts(self.actions, aid))}".lower()
            self.table.setRowHidden(row, bool(q) and q not in hay)

    def _on_edited(self, aid: str, seq: QKeySequence) -> None:
        if self._loading:
            return
        ok, msg = self.assign(aid, normalize(seq))
        self._message(msg, error=not ok)

    def _message(self, text: str, error: bool = False) -> None:
        color = "#ff6b6b" if error else ("#f0a050" if text.startswith("Warning") else "#9aa4b2")
        self.message.setStyleSheet(f"color: {color};")
        self.message.setText(text)

    # ------------------------------------------------------------------ API
    def assign(self, aid: str, seq: str) -> tuple[bool, str]:
        """Make ``seq`` the primary shortcut of ``aid`` ('' clears all of its shortcuts).

        Returns (accepted, message). A duplicate is refused and the editor reverts."""
        spec = self.actions.specs[aid]
        n = normalize(seq)
        cur = current_shortcuts(self.actions, aid)
        if n and n == (cur[0] if cur else ""):
            return True, ""
        if n:
            other = find_conflict(self.actions, n, exclude=aid)
            if other is not None:
                self._show(aid)
                return False, (f"“{native(n)}” is already used by “{self.actions.specs[other].label}”. "
                               "Choose another combination or change that command first.")
            new = (n,) + tuple(s for s in cur[1:] if s != n)
        else:
            new = ()
        apply_shortcuts(self.actions, aid, new)
        _store(self.settings, aid, None if new == self.defaults.get(aid, ()) else new)
        self._show(aid)
        if not n:
            return True, f"Removed the shortcut of “{spec.label}”."
        msg = f"“{spec.label}” is now {native(n)}."
        if spec.changes_audio and not has_modifier(n):
            msg = (f"Warning: {native(n)} has no Ctrl/Alt/Cmd modifier and “{spec.label}” changes audio – it can "
                   "fire by accident. It is ignored while you type in a text or number field, and Undo reverts it.")
        return True, msg

    def reset_to_default(self, aid: str) -> None:
        d = self.defaults.get(aid, ())
        for s in d:
            other = find_conflict(self.actions, s, exclude=aid)
            if other is not None:
                self._message(f"Cannot restore {native(s)} for “{self.actions.specs[aid].label}”: it is now used by "
                              f"“{self.actions.specs[other].label}”.", error=True)
                return
        apply_shortcuts(self.actions, aid, d)
        _store(self.settings, aid, None)
        self._show(aid)
        self._message(f"“{self.actions.specs[aid].label}” uses its default shortcut again.")

    def reset_all_to_defaults(self) -> None:
        for aid in self.actions.specs:
            apply_shortcuts(self.actions, aid, self.defaults.get(aid, ()))
        self.settings.beginGroup(SETTINGS_GROUP)
        self.settings.remove("")
        self.settings.endGroup()
        self.settings.sync()
        for aid in self.actions.specs:
            self._show(aid)
        self._message("All shortcuts are back to their defaults.")

    def _on_reset_all_clicked(self) -> None:
        r = QMessageBox.question(self, "Reset shortcuts", "Restore every shortcut to its default?",
                                 QMessageBox.Reset | QMessageBox.Cancel, QMessageBox.Cancel)
        if r == QMessageBox.Reset:
            self.reset_all_to_defaults()


__all__ = [
    "ShortcutsDialog",
    "load_custom_shortcuts",
    "default_shortcuts",
    "saved_overrides",
    "find_conflict",
    "has_modifier",
    "normalize",
]
