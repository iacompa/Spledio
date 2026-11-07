"""Centralised action registry.

Every toolbar button, menu item and keyboard shortcut is a QAction created
from one :class:`ActionSpec`. The same handler runs whichever way the user
triggers it. ``enabled_when`` returns ``None`` when the action is available
or a human-readable reason when it is not; the reason is shown in the
tooltip and status tip of the disabled action.

``docs/ACTIONS.md`` is generated from this registry
(``python scripts/gen_action_docs.py``) so the guide cannot drift.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QWidget


@dataclass
class ActionSpec:
    id: str
    label: str
    tooltip: str
    handler: Callable[[], None] | Callable[[bool], None]
    menu: str  # menu path, e.g. "File" or "Edit"
    shortcut: str | tuple[str, ...] = ""
    icon: str = ""  # icon id in spledio.ui.icons (text fallback = label)
    enabled_when: Callable[[], str | None] | None = None
    checkable: bool = False
    checked: Callable[[], bool] | None = None
    changes_audio: bool = False
    undoable: bool = False
    acts_on: str = ""
    dependencies: str = "core"
    tests: str = ""
    status: str = "implemented"  # implemented | experimental | unavailable
    toolbar: str = ""  # toolbar id, if shown on a toolbar
    text_safe: bool = True  # False = must not fire while a text field has focus


@dataclass
class ActionRegistry:
    specs: dict[str, ActionSpec] = field(default_factory=dict)
    actions: dict[str, QAction] = field(default_factory=dict)

    def add(self, spec: ActionSpec) -> ActionSpec:
        if spec.id in self.specs:
            raise ValueError(f"duplicate action id {spec.id}")
        self.specs[spec.id] = spec
        return spec

    def build(self, parent: QWidget, icon_provider: Callable[[str], object] | None = None) -> None:
        for spec in self.specs.values():
            act = QAction(spec.label, parent)
            act.setObjectName(f"action_{spec.id}")
            if spec.shortcut:
                seqs = [spec.shortcut] if isinstance(spec.shortcut, str) else list(spec.shortcut)
                act.setShortcuts([QKeySequence(s) for s in seqs])
            if icon_provider and spec.icon:
                ic = icon_provider(spec.icon)
                if ic is not None:
                    act.setIcon(ic)
            act.setCheckable(spec.checkable)
            act.setToolTip(self._tooltip(spec))
            act.setStatusTip(spec.tooltip)
            act.setData(spec.id)
            if spec.checkable:
                act.toggled.connect(lambda on, s=spec: self._run(s, on))
            else:
                act.triggered.connect(lambda _=False, s=spec: self._run(s))
            self.actions[spec.id] = act

    def _run(self, spec: ActionSpec, on: bool | None = None) -> None:
        if self._updating:
            return
        reason = spec.enabled_when() if spec.enabled_when else None
        if reason:
            return
        if spec.checkable:
            spec.handler(bool(on))  # type: ignore[arg-type]
        else:
            spec.handler()  # type: ignore[call-arg]

    _updating = False

    @staticmethod
    def _tooltip(spec: ActionSpec, reason: str | None = None) -> str:
        sc = spec.shortcut if isinstance(spec.shortcut, str) else " / ".join(spec.shortcut)
        lines = [f"<b>{spec.label}</b>" + (f" &nbsp;<span style='color:#9aa'>({QKeySequence(sc).toString(QKeySequence.NativeText)})</span>" if sc else "")]
        lines.append(spec.tooltip)
        tags = []
        tags.append("changes audio" if spec.changes_audio else "does not change audio")
        if spec.undoable:
            tags.append("undoable")
        if spec.status != "implemented":
            tags.append(spec.status)
        lines.append(f"<i style='color:#9aa'>{', '.join(tags)}</i>")
        if reason:
            lines.append(f"<span style='color:#f0a050'>Unavailable: {reason}</span>")
        return "<br>".join(lines)

    def refresh(self) -> None:
        """Re-evaluate enabled / checked state of every action."""
        self._updating = True
        try:
            for aid, spec in self.specs.items():
                act = self.actions.get(aid)
                if act is None:
                    continue
                reason = spec.enabled_when() if spec.enabled_when else None
                act.setEnabled(reason is None)
                act.setToolTip(self._tooltip(spec, reason))
                act.setStatusTip(spec.tooltip if reason is None else f"{spec.label} unavailable: {reason}")
                if spec.checkable and spec.checked is not None:
                    want = bool(spec.checked())
                    if act.isChecked() != want:
                        act.setChecked(want)
        finally:
            self._updating = False

    def __getitem__(self, aid: str) -> QAction:
        return self.actions[aid]

    def by_menu(self, menu: str) -> list[ActionSpec]:
        return [s for s in self.specs.values() if s.menu == menu]
