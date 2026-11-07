"""Reversible command history with a bounded, disk-spilling patch store.

* Every audio edit stores a :class:`LayerPatch` (layer, start, before, after).
  Undo writes *before*, redo writes *after*: exact sample restoration.
* Patch arrays live in :class:`PatchStore`, which keeps recent patches in
  RAM up to a byte budget and spills older ones to ``.npy`` files in a
  private temporary directory (loaded with ``allow_pickle=False``).
* View changes never enter this history. Selection changes enter it only when
  the session's ``record_selection_history`` policy is on.
* A command is pushed only after it has been fully applied; a failure while
  applying rolls back the parts already applied, so no half-applied
  operation can remain.
"""

from __future__ import annotations

import itertools
import os
import shutil
import tempfile
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:  # pragma: no cover
    from spledio.domain.project import Layer, Project


class PatchStore:
    def __init__(self, ram_budget_bytes: int = 768 * 2**20, directory: str | None = None):
        self._budget = ram_budget_bytes
        self._ram: OrderedDict[int, np.ndarray] = OrderedDict()
        self._disk: dict[int, str] = {}
        self._bytes = 0
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        self._dir: str | None = directory  # private temp dir is created lazily on first spill
        self._owns_dir = directory is None

    @property
    def directory(self) -> str:
        if self._dir is None:
            self._dir = tempfile.mkdtemp(prefix="spledio-patches-")
        return self._dir

    def put(self, arr: np.ndarray) -> int:
        a = np.ascontiguousarray(arr)
        a.setflags(write=False)
        with self._lock:
            h = next(self._ids)
            self._ram[h] = a
            self._bytes += a.nbytes
            self._spill_locked()
        return h

    def get(self, h: int) -> np.ndarray:
        with self._lock:
            if h in self._ram:
                self._ram.move_to_end(h)
                return self._ram[h]
            path = self._disk.get(h)
        if path is None:
            raise KeyError(f"patch {h} was released")
        return np.load(path, allow_pickle=False)

    def release(self, h: int) -> None:
        with self._lock:
            a = self._ram.pop(h, None)
            if a is not None:
                self._bytes -= a.nbytes
            path = self._disk.pop(h, None)
        if path:
            try:
                os.remove(path)
            except OSError:
                pass

    def _spill_locked(self) -> None:
        while self._bytes > self._budget and len(self._ram) > 1:
            h, a = self._ram.popitem(last=False)
            path = os.path.join(self.directory, f"p{h}.npy")
            np.save(path, a, allow_pickle=False)
            self._disk[h] = path
            self._bytes -= a.nbytes

    @property
    def ram_bytes(self) -> int:
        return self._bytes

    @property
    def disk_count(self) -> int:
        return len(self._disk)

    def close(self) -> None:
        with self._lock:
            self._ram.clear()
            self._disk.clear()
            self._bytes = 0
        if self._owns_dir and self._dir is not None:
            shutil.rmtree(self._dir, ignore_errors=True)
            self._dir = None


@dataclass
class LayerPatch:
    layer_id: str
    start: int
    before: int  # PatchStore handle
    after: int
    channels: tuple[int, ...]
    length: int

    @property
    def end(self) -> int:
        return self.start + self.length


@dataclass
class Change:
    """What a command touched (used for cache invalidation / UI refresh)."""

    audio: list[tuple[str, int, int]] = field(default_factory=list)  # (layer_id, start, end)
    structure: bool = False
    props: list[str] = field(default_factory=list)  # layer ids with changed properties

    def merge(self, other: "Change") -> "Change":
        return Change(self.audio + other.audio, self.structure or other.structure, self.props + other.props)


class Command:
    kind = "command"
    changes_audio = True

    def __init__(self, label: str, params: dict | None = None):
        self.label = label
        self.params = dict(params or {})
        self.timestamp = time.time()

    def redo(self, project: "Project", store: PatchStore) -> Change:  # pragma: no cover - interface
        raise NotImplementedError

    def undo(self, project: "Project", store: PatchStore) -> Change:  # pragma: no cover - interface
        raise NotImplementedError

    def release(self, store: PatchStore) -> None:
        pass

    def summary(self) -> dict:
        return {"kind": self.kind, "label": self.label, "params": self.params, "timestamp": self.timestamp}


def make_patch(store: PatchStore, layer: "Layer", start: int, new: np.ndarray, channels: tuple[int, ...]) -> LayerPatch:
    """Record before/after for writing ``new`` (channels, n) into ``layer`` at ``start``."""
    n = new.shape[1]
    before = layer.audio[:, start : start + n].copy()
    after = before.copy()
    for c in channels:
        after[c] = new[c].astype(layer.audio.dtype)
    return LayerPatch(layer.id, start, store.put(before), store.put(after), tuple(channels), n)


def write_patch(project: "Project", store: PatchStore, p: LayerPatch, use_after: bool) -> tuple[str, int, int]:
    layer = project.layer(p.layer_id)
    data = store.get(p.after if use_after else p.before)
    layer.audio[:, p.start : p.end] = data
    layer.bump()
    return (p.layer_id, p.start, p.end)


class PatchCommand(Command):
    kind = "patch"

    def __init__(self, label: str, patches: list[LayerPatch], params: dict | None = None):
        super().__init__(label, params)
        self.patches = patches

    def redo(self, project, store):
        return Change(audio=[write_patch(project, store, p, True) for p in self.patches])

    def undo(self, project, store):
        return Change(audio=[write_patch(project, store, p, False) for p in reversed(self.patches)])

    def release(self, store):
        for p in self.patches:
            store.release(p.before)
            store.release(p.after)


class LayerPropsCommand(Command):
    kind = "layer_props"
    changes_audio = False  # changes the *mix*, not layer samples

    def __init__(self, label: str, layer_id: str, before: dict, after: dict):
        super().__init__(label, {"layer_id": layer_id, "before": before, "after": after})
        self.layer_id, self.before, self.after = layer_id, before, after

    def _apply(self, project, values):
        layer = project.layer(self.layer_id)
        for k, v in values.items():
            setattr(layer, k, v)
        return Change(props=[self.layer_id])

    def redo(self, project, store):
        return self._apply(project, self.after)

    def undo(self, project, store):
        return self._apply(project, self.before)


class AddLayerCommand(Command):
    """Insert a layer (its audio is kept in the patch store while undone)."""

    kind = "add_layer"

    def __init__(self, label: str, layer: "Layer", index: int, store: PatchStore, params: dict | None = None):
        super().__init__(label, params)
        self.layer = layer
        self.index = index
        self._audio_handle: int | None = None
        self._store = store

    def redo(self, project, store):
        if self._audio_handle is not None:
            self.layer.audio = np.array(store.get(self._audio_handle))
            store.release(self._audio_handle)
            self._audio_handle = None
        project.layers.insert(min(self.index, len(project.layers)), self.layer)
        self.layer.bump()
        return Change(audio=[(self.layer.id, 0, project.n_frames)], structure=True)

    def undo(self, project, store):
        project.layers.remove(self.layer)
        self._audio_handle = store.put(self.layer.audio)
        self.layer.audio = np.zeros((0, 0), dtype=self.layer.audio.dtype)
        return Change(audio=[(self.layer.id, 0, project.n_frames)], structure=True)

    def release(self, store):
        if self._audio_handle is not None:
            store.release(self._audio_handle)
            self._audio_handle = None


class RemoveLayerCommand(Command):
    kind = "remove_layer"

    def __init__(self, label: str, layer: "Layer", index: int):
        super().__init__(label, {"layer_id": layer.id})
        self.layer = layer
        self.index = index
        self._handle: int | None = None

    def redo(self, project, store):
        project.layers.remove(self.layer)
        self._handle = store.put(self.layer.audio)
        self.layer.audio = np.zeros((0, 0), dtype=self._dtype(project))
        return Change(audio=[(self.layer.id, 0, project.n_frames)], structure=True)

    def undo(self, project, store):
        self.layer.audio = np.array(store.get(self._handle))
        store.release(self._handle)
        self._handle = None
        project.layers.insert(min(self.index, len(project.layers)), self.layer)
        self.layer.bump()
        return Change(audio=[(self.layer.id, 0, project.n_frames)], structure=True)

    @staticmethod
    def _dtype(project):
        return np.dtype(project.storage_dtype)

    def release(self, store):
        if self._handle is not None:
            store.release(self._handle)
            self._handle = None


class MoveLayerCommand(Command):
    kind = "move_layer"
    changes_audio = False

    def __init__(self, label: str, layer_id: str, old_index: int, new_index: int):
        super().__init__(label, {"layer_id": layer_id, "from": old_index, "to": new_index})
        self.layer_id, self.old, self.new = layer_id, old_index, new_index

    def _move(self, project, to):
        layer = project.layer(self.layer_id)
        project.layers.remove(layer)
        project.layers.insert(to, layer)
        return Change(structure=True)

    def redo(self, project, store):
        return self._move(project, self.new)

    def undo(self, project, store):
        return self._move(project, self.old)


class SelectionCommand(Command):
    """Selection change recorded only under the 'include selection in history' policy."""

    kind = "selection"
    changes_audio = False

    def __init__(self, label: str, session, before, after):
        super().__init__(label)
        self._session, self.before, self.after = session, before, after

    def redo(self, project, store):
        self._session._set_selection_raw(self.after)
        return Change()

    def undo(self, project, store):
        self._session._set_selection_raw(self.before)
        return Change()


class TimelineCommand(Command):
    """Duration-changing operation (delete / insert / crop time) on ALL layers.

    Stores every layer's complete before/after audio in the patch store, so it
    is memory-heavy but exact. Markers are not shifted (documented)."""

    kind = "timeline"

    def __init__(self, label: str, project: "Project", store: PatchStore, new_audio: dict[str, np.ndarray],
                 new_frames: int, params: dict | None = None):
        super().__init__(label, params)
        self.old_frames = project.n_frames
        self.new_frames = new_frames
        self.before = {l.id: store.put(l.audio) for l in project.layers}
        self.after = {lid: store.put(a) for lid, a in new_audio.items()}

    def _apply(self, project, handles, frames, store):
        for l in project.layers:
            l.audio = np.array(store.get(handles[l.id]))
            l.bump()
        project.n_frames = frames
        return Change(audio=[(l.id, 0, frames) for l in project.layers], structure=True)

    def redo(self, project, store):
        return self._apply(project, self.after, self.new_frames, store)

    def undo(self, project, store):
        return self._apply(project, self.before, self.old_frames, store)

    def release(self, store):
        for h in list(self.before.values()) + list(self.after.values()):
            store.release(h)


class CompositeCommand(Command):
    kind = "composite"

    def __init__(self, label: str, children: list[Command], params: dict | None = None):
        super().__init__(label, params)
        self.children = children

    @property
    def changes_audio(self):  # type: ignore[override]
        return any(c.changes_audio for c in self.children)

    def redo(self, project, store):
        ch = Change()
        done = []
        try:
            for c in self.children:
                ch = ch.merge(c.redo(project, store))
                done.append(c)
        except Exception:
            for c in reversed(done):
                c.undo(project, store)
            raise
        return ch

    def undo(self, project, store):
        ch = Change()
        for c in reversed(self.children):
            ch = ch.merge(c.undo(project, store))
        return ch

    def release(self, store):
        for c in self.children:
            c.release(store)


class History:
    def __init__(self, store: PatchStore | None = None, limit: int = 200):
        self.store = store or PatchStore()
        self.limit = limit
        self.undo_stack: list[Command] = []
        self.redo_stack: list[Command] = []
        self.applied_count = 0  # total commands ever applied (dirty tracking)
        self._saved_marker: Command | None | str = None

    def push_applied(self, cmd: Command) -> None:
        """Register a command whose ``redo`` effect is already in the project."""
        self.undo_stack.append(cmd)
        for c in self.redo_stack:
            c.release(self.store)
        self.redo_stack.clear()
        while len(self.undo_stack) > self.limit:
            old = self.undo_stack.pop(0)
            old.release(self.store)
        self.applied_count += 1

    def execute(self, project, cmd: Command) -> Change:
        ch = cmd.redo(project, self.store)  # CompositeCommand rolls back on failure
        self.push_applied(cmd)
        return ch

    def can_undo(self) -> bool:
        return bool(self.undo_stack)

    def can_redo(self) -> bool:
        return bool(self.redo_stack)

    def undo(self, project) -> tuple[Command, Change] | None:
        if not self.undo_stack:
            return None
        cmd = self.undo_stack.pop()
        ch = cmd.undo(project, self.store)
        self.redo_stack.append(cmd)
        return cmd, ch

    def redo(self, project) -> tuple[Command, Change] | None:
        if not self.redo_stack:
            return None
        cmd = self.redo_stack.pop()
        ch = cmd.redo(project, self.store)
        self.undo_stack.append(cmd)
        return cmd, ch

    def mark_saved(self) -> None:
        self._saved_marker = self.undo_stack[-1] if self.undo_stack else "empty"

    def saved_index(self) -> int | None:
        """Number of applied commands at the last save (0 = initial state), or
        None when the saved state is not on the current undo/redo path."""
        m = self._saved_marker
        if isinstance(m, str) and m == "empty":
            return 0
        for i, c in enumerate(self.undo_stack):
            if c is m:
                return i + 1
        for j, c in enumerate(reversed(self.redo_stack)):
            if c is m:
                return len(self.undo_stack) + j + 1
        return None

    def is_at_saved(self) -> bool:
        cur = self.undo_stack[-1] if self.undo_stack else "empty"
        return cur is self._saved_marker or (cur == "empty" and self._saved_marker == "empty")

    def labels(self) -> tuple[list[str], list[str]]:
        return [c.label for c in self.undo_stack], [c.label for c in reversed(self.redo_stack)]

    def clear(self) -> None:
        for c in self.undo_stack + self.redo_stack:
            c.release(self.store)
        self.undo_stack.clear()
        self.redo_stack.clear()

    def close(self) -> None:
        self.clear()
        self.store.close()
