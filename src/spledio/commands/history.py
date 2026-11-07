"""Reversible command history with a bounded, disk-spilling patch store.

* Every audio edit stores a :class:`LayerPatch` (layer, region, samples).
  Undo puts back the samples from before the edit, redo the ones it wrote:
  exact sample restoration. Only the version that is not in the layer is
  kept; undo/redo swap it with the layer's (see :class:`LayerPatch`).
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
from typing import TYPE_CHECKING, Callable

import numpy as np

from spledio.ram import undo_ram_budget

if TYPE_CHECKING:  # pragma: no cover
    from spledio.domain.project import Layer, Project


class PatchStore:
    """Arrays by handle: in RAM up to a byte budget (default: sized to this machine's RAM, see
    :func:`spledio.ram.undo_ram_budget`), older ones spilled to disk. ``hot`` arrays (adjusted-area
    components, read on every change of their area) move to disk only after everything else."""

    def __init__(self, ram_budget_bytes: int | None = None, directory: str | None = None):
        self._budget = int(undo_ram_budget() if ram_budget_bytes is None else ram_budget_bytes)
        self._ram: OrderedDict[int, np.ndarray] = OrderedDict()
        self._hot: set[int] = set()
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

    def put(self, arr: np.ndarray, hot: bool = False) -> int:
        a = np.ascontiguousarray(arr)
        a.setflags(write=False)
        with self._lock:
            h = next(self._ids)
            self._ram[h] = a
            if hot:
                self._hot.add(h)
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

    def swap(self, h: int, audio: np.ndarray, channels: tuple[int, ...], start: int) -> None:
        """Exchange the samples of ``h`` (``(len(channels), n)``) with rows ``channels`` of
        ``audio[:, start:start+n]``, in place and block by block: no full-size copy is made,
        whether ``h`` is in RAM or on disk (there it is streamed). ``h`` keeps its handle, size
        and place (RAM or disk). If it fails, ``audio`` and ``h`` are as they were."""
        ch = tuple(channels)
        with self._lock:  # no spill or read of `h` while it is half swapped
            if h in self._ram:
                a = self._ram[h]
                if a.shape[0] == len(ch) and a.dtype == audio.dtype and a.ndim == 2:
                    try:
                        a.setflags(write=True)
                    except ValueError:
                        pass
                    else:
                        try:
                            _swap_blocks(a, audio, ch, start)
                        finally:
                            a.setflags(write=False)
                        return
            else:
                path = self._disk.get(h)
                if path is None:
                    raise KeyError(f"patch {h} was released")
                if _swap_file(path, audio, ch, start):
                    return
        # anything else (e.g. stored in another dtype): swap through one full copy
        held = self.get(h)
        cur = read_rows(audio, ch, start, start + held.shape[1])
        write_rows(audio, ch, start, held)
        try:
            self._replace(h, cur)
        except BaseException:
            write_rows(audio, ch, start, cur)
            raise

    def _replace(self, h: int, arr: np.ndarray) -> None:
        a = np.ascontiguousarray(arr)
        a.setflags(write=False)
        with self._lock:
            old = self._ram.get(h)
            if old is not None:
                self._ram[h] = a
                self._bytes += a.nbytes - old.nbytes
                return
            path = self._disk[h]
            try:
                with open(path + ".swap", "wb") as f:
                    np.save(f, a, allow_pickle=False)
                os.replace(path + ".swap", path)
            except BaseException:
                _remove(path + ".swap")
                raise

    def release(self, h: int) -> None:
        with self._lock:
            self._hot.discard(h)
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
            newest = next(reversed(self._ram))  # always stays in RAM
            h = next((k for k in self._ram if k not in self._hot and k != newest), None)
            if h is None:  # only hot arrays left besides the newest: oldest first
                h = next(iter(self._ram))
            a = self._ram[h]
            path = os.path.join(self.directory, f"p{h}.npy")
            np.save(path, a, allow_pickle=False)  # only then leave RAM: a failed write loses nothing
            del self._ram[h]
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
            self._hot.clear()
            self._disk.clear()
            self._bytes = 0
        if self._owns_dir and self._dir is not None:
            shutil.rmtree(self._dir, ignore_errors=True)
            self._dir = None


_SWAP_BLOCK = 1 << 18  # samples per row block when stored samples are swapped with a layer's


def _row_blocks(n_rows: int, n: int):
    for i in range(n_rows):
        for x in range(0, n, _SWAP_BLOCK):
            yield i, x, min(n, x + _SWAP_BLOCK)


def _swap_blocks(a: np.ndarray, audio: np.ndarray, ch: tuple[int, ...], start: int) -> None:
    """Swap ``a`` (len(ch), n) with rows ``ch`` of ``audio`` at ``start``, one block at a time.
    The swap is its own inverse: if it is interrupted, the blocks done so far are swapped back."""
    n = a.shape[1]
    tmp = np.empty(min(n, _SWAP_BLOCK), dtype=a.dtype)
    blocks = list(_row_blocks(len(ch), n))

    def views(blk):
        i, x, y = blk
        return tmp[: y - x], a[i, x:y], audio[ch[i], start + x : start + y]

    done, filled = 0, False
    try:
        for blk in blocks:
            t, held, row = views(blk)
            np.copyto(t, held)
            filled = True
            np.copyto(held, row)
            np.copyto(row, t)
            filled = False
            done += 1
    except BaseException:
        if filled:  # in the middle of a block: finish it if its stored part already has the layer's samples
            t, held, row = views(blocks[done])
            if not np.array_equal(held, t):
                np.copyto(row, t)
                done += 1
        for blk in reversed(blocks[:done]):
            t, held, row = views(blk)
            np.copyto(t, held)
            np.copyto(held, row)
            np.copyto(row, t)
        raise


def _npy_header(f) -> tuple[tuple[int, ...], bool, np.dtype] | None:
    """(shape, fortran_order, dtype) of an .npy file, ``f`` then at its first data byte; None for a
    header version this does not read."""
    fmt = np.lib.format
    version = fmt.read_magic(f)
    if version == (1, 0):
        shape, fortran, dtype = fmt.read_array_header_1_0(f)
    elif version == (2, 0):
        shape, fortran, dtype = fmt.read_array_header_2_0(f)
    else:
        return None
    return tuple(shape), bool(fortran), dtype


def _stream_rows(f, audio: np.ndarray, ch: tuple[int, ...], start: int, n: int) -> None:
    """Read (len(ch), n) C-order samples from ``f`` into rows ``ch`` of ``audio`` at ``start``."""
    buf = np.empty(min(n, _SWAP_BLOCK), dtype=audio.dtype)
    for i, x, y in _row_blocks(len(ch), n):
        v = buf[: y - x]
        mv = memoryview(v).cast("B")
        if f.readinto(mv) != mv.nbytes:
            raise OSError("stored undo samples are shorter than expected")
        audio[ch[i], start + x : start + y] = v


def _swap_file(path: str, audio: np.ndarray, ch: tuple[int, ...], start: int) -> bool:
    """Swap a spilled ``.npy`` with layer rows, streaming: the layer's rows go to a new file, the
    old file is read into the layer, and the new file replaces it. False (nothing changed) if the
    file is not a plain C-order array of the layer's dtype with one row per channel."""
    with open(path, "rb") as f:
        hdr = _npy_header(f)
    if hdr is None or len(hdr[0]) != 2 or hdr[0][0] != len(ch) or hdr[1] or hdr[2] != audio.dtype:
        return False
    shape = hdr[0]
    n = shape[1]
    fmt = np.lib.format
    new_path = path + ".swap"
    try:
        with open(new_path, "wb") as g:  # 1. the layer's current rows (the layer is not touched yet)
            fmt.write_array_header_1_0(g, {"descr": fmt.dtype_to_descr(audio.dtype), "fortran_order": False,
                                           "shape": shape})
            for i, x, y in _row_blocks(len(ch), n):
                g.write(memoryview(np.ascontiguousarray(audio[ch[i], start + x : start + y])).cast("B"))
    except BaseException:
        _remove(new_path)
        raise
    try:
        with open(path, "rb") as f:  # 2. the stored samples into the layer
            if _npy_header(f) != hdr:
                raise OSError("stored undo samples changed on disk")
            _stream_rows(f, audio, ch, start, n)
        os.replace(new_path, path)  # 3. the layer's old rows are now the stored ones
    except BaseException:
        with open(new_path, "rb") as g:  # put the layer's rows back
            _npy_header(g)
            _stream_rows(g, audio, ch, start, n)
        _remove(new_path)
        raise
    return True


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


@dataclass
class LayerPatch:
    """Exact samples of one region of one layer, before and after an edit – each kept once.

    The store holds only the version that is *not* in the layer right now
    (``held``): the samples from before the edit while it is applied, the
    samples it wrote while it is undone. Undo and redo swap the held samples
    with the layer's. History is linear (commands are undone and redone
    strictly in stack order, and only commands write layer audio), so at that
    moment the layer holds exactly the other version and the swap restores
    bit-exact samples, with half the memory of keeping both copies.

    Only rows ``channels`` are kept; the edit does not change the other rows.
    """

    layer_id: str
    start: int
    held: int  # PatchStore handle: rows `channels` of the version not in the layer, (len(channels), length)
    channels: tuple[int, ...]
    length: int
    applied: bool = False

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


def _all_rows(audio: np.ndarray, channels: tuple[int, ...]) -> bool:
    return tuple(channels) == tuple(range(audio.shape[0]))


def read_rows(audio: np.ndarray, channels: tuple[int, ...], start: int, end: int) -> np.ndarray:
    """A copy of rows ``channels`` of ``audio[:, start:end]``."""
    if _all_rows(audio, channels):
        return audio[:, start:end].copy()
    return audio[list(channels), start:end]  # fancy indexing copies


def write_rows(audio: np.ndarray, channels: tuple[int, ...], start: int, rows: np.ndarray) -> None:
    """Write ``rows`` (len(channels), n) into rows ``channels`` of ``audio`` at ``start``."""
    end = start + rows.shape[1]
    if _all_rows(audio, channels):
        audio[:, start:end] = rows
    else:
        for i, c in enumerate(channels):
            audio[c, start:end] = rows[i]


def make_patch(store: PatchStore, layer: "Layer", start: int, new: np.ndarray, channels: tuple[int, ...]) -> LayerPatch:
    """Patch that writes rows ``channels`` of ``new`` (channels, n) into ``layer`` at ``start``.

    Not applied yet (the command's ``redo`` applies it): the store holds the new
    samples, rounded once to the layer's dtype."""
    n = new.shape[1]
    ch = tuple(channels)
    after = np.empty((len(ch), n), dtype=layer.audio.dtype)
    for i, c in enumerate(ch):
        after[i] = new[c]
    return LayerPatch(layer.id, start, store.put(after), ch, n, applied=False)


def patch_in_place(store: PatchStore, layer: "Layer", start: int, length: int, channels: tuple[int, ...],
                   apply: Callable[[], None]) -> LayerPatch:
    """Applied patch for an edit that ``apply()`` makes directly in ``layer.audio``.

    Keeps a copy of the region's current rows ``channels`` (the only full-size
    copy: no separate array of new samples is built), then calls ``apply()``,
    which must change only those samples. If it fails, the samples are put back."""
    ch = tuple(channels)
    h = store.put(read_rows(layer.audio, ch, start, start + length))
    try:
        apply()
    except BaseException:
        write_rows(layer.audio, ch, start, store.get(h))
        store.release(h)
        raise
    layer.bump()
    return LayerPatch(layer.id, start, h, ch, length, applied=True)


def write_patch(project: "Project", store: PatchStore, p: LayerPatch, use_after: bool) -> tuple[str, int, int]:
    """Bring ``p`` to the applied (``use_after``) or the undone state: swap the held samples with the layer's."""
    if p.applied != use_after:
        layer = project.layer(p.layer_id)
        store.swap(p.held, layer.audio, p.channels, p.start)  # in place, block by block (RAM or disk)
        p.applied = use_after
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
            store.release(p.held)


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
            # The bottom of the stack is now the state *after* ``old``.
            if self._saved_marker is old:
                self._saved_marker = "empty"  # undoing everything returns to the saved state
            elif isinstance(self._saved_marker, str) and self._saved_marker == "empty":
                self._saved_marker = object()  # the saved state can no longer be reached
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
