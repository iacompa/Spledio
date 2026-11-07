"""EditorSession: the Qt-free facade over project, selection, DSP and history.

The UI, the tests and any future scripting use only this API. Every method
that changes audio goes through :class:`~spledio.commands.history.History`
so it is exactly undoable, and every audition/export render goes through
:meth:`Project.render_mix` / :func:`selected_component` so preview and
export share one processing path.
"""

from __future__ import annotations

import math
import os
import threading
import time
from dataclasses import dataclass, field, replace
from typing import Callable

import numpy as np

from spledio.commands.history import (
    AddLayerCommand,
    Change,
    Command,
    CompositeCommand,
    History,
    LayerPropsCommand,
    MoveLayerCommand,
    PatchCommand,
    RemoveLayerCommand,
    SelectionCommand,
    LayerPatch,
    TimelineCommand,
    make_patch,
    write_patch,
    patch_in_place,
    patch_versions,
    read_rows,
    write_rows,
)
from spledio.domain.project import MIX_BLOCK, Asset, BlockAudio, Layer, Project, new_id
from spledio.engine.areas import (
    MERGE_WINDOW_S,
    AdjustArea,
    AreaCommand,
    AreasStateCommand,
    area_contains,
    change_area_gain,
    factor,
    gain_text,
    step_gain,
)
from spledio.dsp.spectral import Component, add_scaled_in_place, apply_gain_to_region, gain_factor, selected_component
from spledio.dsp.stft import DEFAULT_EDIT_CONFIG, STFTConfig
from spledio.selections.geometry import INF_F, BrushShape, RectShape
from spledio.selections.selection import INTERSECT, Selection, SelectionOp, select_all


class EditError(Exception):
    """A user-facing reason why an edit cannot be performed (nothing changed)."""


@dataclass
class SessionEvent:
    kind: str  # 'audio' | 'structure' | 'props' | 'selection' | 'history' | 'dirty' | 'project' | 'preview'
    change: Change | None = None
    message: str = ""


@dataclass
class EditResult:
    command: Command
    warnings: list[str] = field(default_factory=list)
    peak_after: float | None = None


@dataclass
class EditPreview:
    """A rendered but uncommitted edit (Preview / Bypass / Apply / Cancel)."""

    layer_id: str
    layer_version: int
    start: int
    original: np.ndarray  # (C, n) float64 layer samples before
    processed: np.ndarray  # (C, n) float64 layer samples after
    gain_db: float
    selection: Selection
    config: STFTConfig
    label: str
    component: Component

    @property
    def end(self) -> int:
        return self.start + self.original.shape[1]


@dataclass
class AuditionBuffer:
    start: int
    data: np.ndarray  # (C, n) float32, ready for playback
    label: str

    @property
    def end(self) -> int:
        return self.start + self.data.shape[1]


def channel_scope_label(channels: tuple[int, ...] | None, n_channels: int) -> str:
    if channels is None or len(channels) == n_channels:
        return "all channels" if n_channels != 2 else "L+R linked"
    names = {0: "L", 1: "R"} if n_channels == 2 else {}
    return "+".join(names.get(c, f"ch{c + 1}") for c in channels) + " only"


def fmt_db(g: float) -> str:
    if math.isinf(g) and g < 0:
        return "mute"
    return f"{g:+.1f} dB"


class EditorSession:
    def __init__(self, project: Project, history: History | None = None):
        self.project = project
        self.history = history or History()
        self.selection: Selection | None = None
        self.last_selection: Selection | None = None
        self.named_selections: dict[str, Selection] = {}
        self.active_layer_id: str | None = project.layers[0].id if project.layers else None
        self.edit_config: STFTConfig = project.processing_config
        self.record_selection_history = False
        self.path: str | None = None  # project file path once saved/opened
        self.operation_log: list[dict] = []  # provenance, saved in project
        self.view_state: dict = {}
        self._listeners: list[Callable[[SessionEvent], None]] = []
        self._preview: EditPreview | None = None
        self._comp_cache: tuple | None = None
        self._lock = threading.RLock()  # serialises mutations (UI thread + worker renders)
        self._dirty = False
        self.asset_cache_dir: str | None = None  # extracted embedded assets (deleted on close)
        # adjusted areas (re-adjustable, removable gain regions; see engine/areas.py)
        self.areas: tuple[AdjustArea, ...] = ()
        self.active_area_id: str | None = None
        self._component_handles: set[int] = set()  # stored area components (freed once nothing refers to them)
        self.history.mark_saved()

    # ------------------------------------------------------------------ setup
    @classmethod
    def from_audio_file(cls, path: str) -> "EditorSession":
        from spledio.audio.io import file_sha256, load_audio

        audio, info = load_audio(path)
        asset = Asset(
            id=new_id("asset"),
            path=os.path.abspath(path),
            sha256=file_sha256(path),
            format=info.format,
            subtype=info.subtype,
            sample_rate=info.sample_rate,
            channels=info.channels,
            frames=audio.shape[1],
        )
        proj = Project(
            sample_rate=info.sample_rate,
            n_frames=audio.shape[1],
            channels=info.channels,
            storage_dtype=str(audio.dtype),
            name=os.path.splitext(os.path.basename(path))[0],
        )
        proj.assets[asset.id] = asset
        proj.layers.append(
            Layer(
                id=new_id("layer"),
                name=proj.name,
                audio=np.ascontiguousarray(audio),
                color=proj.next_color(),
                base={"type": "asset", "asset_id": asset.id},
                provenance=f"imported from {os.path.basename(path)}",
            )
        )
        s = cls(proj)
        s.operation_log.append({"op": "import", "file": os.path.basename(path), "sha256": asset.sha256})
        return s

    @classmethod
    def from_array(cls, audio: np.ndarray, sample_rate: int, name: str = "Generated", dtype="float32") -> "EditorSession":
        """Project from an in-memory signal (tests / signal generator). Marked as generated."""
        audio = np.ascontiguousarray(np.atleast_2d(audio), dtype=dtype)
        proj = Project(sample_rate=int(sample_rate), n_frames=audio.shape[1], channels=audio.shape[0], storage_dtype=str(audio.dtype), name=name)
        proj.layers.append(
            Layer(id=new_id("layer"), name=name, audio=audio, color=proj.next_color(),
                  base={"type": "generated"}, provenance="generated in-app (not imported)")
        )
        return cls(proj)

    def close(self) -> None:
        self.history.close()
        if self.asset_cache_dir:
            import shutil

            shutil.rmtree(self.asset_cache_dir, ignore_errors=True)
            self.asset_cache_dir = None

    # --------------------------------------------------------------- events
    def subscribe(self, fn: Callable[[SessionEvent], None]) -> None:
        self._listeners.append(fn)

    def unsubscribe(self, fn) -> None:
        if fn in self._listeners:
            self._listeners.remove(fn)

    def _emit(self, kind: str, change: Change | None = None, message: str = "") -> None:
        ev = SessionEvent(kind, change, message)
        for fn in list(self._listeners):
            fn(ev)

    def _after_change(self, change: Change, message: str = "") -> None:
        self._comp_cache = None
        if change.audio or change.props or change.structure:
            self.project.mix_version += 1
        if self._preview is not None:
            self._preview = None
            self._emit("preview")
        if self.active_layer_id is not None and not self.project.has_layer(self.active_layer_id):
            self.active_layer_id = self.project.layers[0].id if self.project.layers else None
        if change.structure:
            self._emit("structure", change, message)
        if change.audio:
            self._emit("audio", change, message)
        if change.props:
            self._emit("props", change, message)
        self._emit("history", change, message)
        self._set_dirty(not self.history.is_at_saved())

    def _set_dirty(self, d: bool) -> None:
        if d != self._dirty:
            self._dirty = d
            self._emit("dirty")

    @property
    def dirty(self) -> bool:
        return self._dirty

    def mark_saved(self, path: str | None = None) -> None:
        if path:
            self.path = path
        self.history.mark_saved()
        self._set_dirty(False)

    def mark_recovered(self, original_path: str | None) -> None:
        """A session restored from a crash-recovery copy: point it back at the
        user's original project (or nothing) and keep it dirty until saved."""
        self.path = original_path if original_path and os.path.exists(original_path) else None
        self.history._saved_marker = object()  # never equal to any history state
        self._set_dirty(True)

    # ----------------------------------------------------------- properties
    @property
    def sample_rate(self) -> int:
        return self.project.sample_rate

    @property
    def active_layer(self) -> Layer | None:
        if self.active_layer_id is None:
            return None
        try:
            return self.project.layer(self.active_layer_id)
        except KeyError:
            return None

    def set_active_layer(self, layer_id: str) -> None:
        self.project.layer(layer_id)
        if layer_id != self.active_layer_id:
            self.active_layer_id = layer_id
            self.cancel_preview()
            self._comp_cache = None
            self._emit("structure", Change(structure=True))

    def set_edit_config(self, cfg: STFTConfig) -> None:
        """Processing resolution used for FUTURE edits. Committed edits keep theirs."""
        cfg.validate()
        self.edit_config = cfg
        self.project.processing_config = cfg
        self._comp_cache = None
        self.cancel_preview()
        self._emit("selection")

    # ------------------------------------------------------------ selection
    def set_selection(self, sel: Selection | None) -> None:
        if sel is not None and sel.is_empty:
            sel = None
        before = self.selection
        if self.record_selection_history and before != sel:
            cmd = SelectionCommand("Selection: " + (sel.describe() if sel else "cleared"), self, before, sel)
            cmd.redo(self.project, self.history.store)
            self.history.push_applied(cmd)
            self._emit("history")
        else:
            self._set_selection_raw(sel)

    def _set_selection_raw(self, sel: Selection | None) -> None:
        if self.selection is not None:
            self.last_selection = self.selection
        self.selection = sel
        self._comp_cache = None
        if self._preview is not None:
            self._preview = None
            self._emit("preview")
        self._emit("selection")

    def clear_selection(self) -> None:
        self.set_selection(None)

    def select_all(self) -> None:
        self.set_selection(select_all(self.project.duration))

    def reselect(self) -> None:
        if self.last_selection is not None:
            self.set_selection(self.last_selection)

    def invert_selection(self) -> None:
        if self.selection is None:
            self.select_all()
            return
        self.set_selection(self.selection.inverted())

    def save_named_selection(self, name: str) -> None:
        if self.selection is None:
            raise EditError("Nothing is selected")
        self.named_selections[name] = Selection(**{**self.selection.__dict__, "name": name})
        self._emit("selection")

    def load_named_selection(self, name: str) -> None:
        self.set_selection(self.named_selections[name])

    def selection_or_error(self) -> Selection:
        if self.selection is None:
            raise EditError("Make a selection first (rectangle, time, frequency, lasso or brush)")
        return self.selection

    def selection_label(self, sel: Selection | None = None) -> str:
        sel = sel or self.selection
        if sel is None:
            return "Entire selected layer"
        return sel.label_range(self.project.duration, self.project.nyquist)

    # --------------------------------------------------------------- guards
    def _editable_layer(self) -> Layer:
        layer = self.active_layer
        if layer is None:
            raise EditError("There is no layer to edit")
        if layer.locked:
            raise EditError(f"Layer '{layer.name}' is locked; unlock it to edit")
        return layer

    # ------------------------------------------------------- core rendering
    def component(self, sel: Selection | None = None, layer: Layer | None = None, cancel=None) -> Component:
        """Selected component c = ISTFT(M·X) of a layer (cached per selection/layer version)."""
        sel = sel or self.selection_or_error()
        layer = layer or self.active_layer
        if layer is None:
            raise EditError("There is no layer")
        key = (sel, layer.id, layer.version, self.edit_config)
        with self._lock:
            if self._comp_cache is not None and self._comp_cache[0] == key:
                return self._comp_cache[1]
            comp = selected_component(layer.audio, sel, self.edit_config, self.sample_rate, cancel=cancel)
            if comp is None:
                raise self._no_cells_error()
            self._comp_cache = (key, comp)
            return comp

    def _no_cells_error(self) -> EditError:
        return EditError(
            "The selection covers no analysis cells at the current processing resolution "
            f"({self.edit_config.frame_spacing(self.sample_rate) * 1000:.1f} ms x "
            f"{self.edit_config.bin_spacing(self.sample_rate):.1f} Hz); make it larger"
        )

    def _storage_component(self, sel: Selection, layer: Layer) -> tuple[int, np.ndarray, tuple[int, ...]]:
        """(start, data, channels) of the selected component in the storage dtype (float32 unless the
        project stores float64), as kept by an adjusted area: the float64 component rounded once,
        computed without holding the float64 component of the whole span (unless it is cached)."""
        dt = np.dtype(np.float64 if self.project.storage_dtype == "float64" else np.float32)
        key = (sel, layer.id, layer.version, self.edit_config)
        if self._comp_cache is not None and self._comp_cache[0] == key:
            comp = self._comp_cache[1]
            return comp.start, comp.data.astype(dt), tuple(comp.channels)
        comp = selected_component(layer.audio, sel, self.edit_config, self.sample_rate, dtype=dt)
        if comp is None:
            raise self._no_cells_error()
        return comp.start, comp.data, tuple(comp.channels)

    def _label(self, verb: str, sel: Selection, layer: Layer, gain_db: float | None = None) -> str:
        parts = [verb if gain_db is None else f"{verb} {fmt_db(gain_db)}"]
        parts.append(self.selection_label(sel))
        parts.append(layer.name)
        parts.append(channel_scope_label(sel.channels, self.project.channels))
        return " · ".join(parts)

    def _params(self, sel: Selection, layer: Layer, comp: Component, **extra) -> dict:
        return {
            "selection": sel.to_dict(),
            "layer_id": layer.id,
            "channels": list(comp.channels),
            "config": self.edit_config.to_dict(),
            "method": comp.method,
            "region": [comp.start, comp.end],
            **extra,
        }

    def _check_clip(self, start: int, end: int, warnings: list[str]) -> float:
        # Peak of the float64 mix, block by block (no float64 copy of a long region; np.max
        # over the block peaks keeps a NaN, as the whole-region max did).
        mix = self.project.mix_view(start, end)
        ch, n = mix.shape
        peaks = [np.max(np.abs(mix[:, a : min(n, a + MIX_BLOCK)])) for a in range(0, n, MIX_BLOCK)] if ch else []
        peak = float(np.max(peaks)) if peaks else 0.0
        if peak > 1.0:
            warnings.append(
                f"The mix now peaks at {20 * math.log10(peak):+.2f} dBFS in the edited region. Nothing was clipped "
                "internally; 32-bit float export keeps it, integer export will ask how to handle it."
            )
        return peak

    def _commit(self, cmd: Command, change: Change, warnings_region: tuple[int, int] | None = None) -> EditResult:
        self.history.push_applied(cmd)
        self._release_unused_components()
        self.operation_log.append(cmd.summary())
        warnings: list[str] = []
        peak = None
        if warnings_region is not None:
            peak = self._check_clip(*warnings_region, warnings)
        self._after_change(change, cmd.label)
        return EditResult(cmd, warnings, peak)

    # ------------------------------------------------------------- edits
    def apply_gain(self, gain_db: float, verb: str = "Gain", sel: Selection | None = None) -> EditResult:
        """Change the selected spectral content by ``gain_db`` (``-inf`` = mute)."""
        with self._lock:
            sel = sel or self.selection_or_error()
            layer = self._editable_layer()
            if not math.isinf(gain_db) and abs(gain_db) < 1e-9:
                raise EditError("0 dB changes nothing")
            pv = self._preview if self._preview is not None and self._preview_matches(gain_db, sel, layer) else None
            comp = pv.component if pv is not None else self.component(sel, layer)
            label = self._label(verb, sel, layer, None if verb in ("Mute", "Delete spectral content") else gain_db)
            params = self._params(sel, layer, comp, gain_db=gain_db, verb=verb)
            if pv is not None:  # what was previewed is what gets committed
                patch = make_patch(self.history.store, layer, comp.start, pv.processed, comp.channels)
            else:  # x + (G − 1)·c written in place (same values as apply_gain_to_region, no float64 copy)
                d = gain_factor(gain_db) - 1.0
                patch = patch_in_place(self.history.store, layer, comp.start, comp.data.shape[1], comp.channels,
                                       lambda: add_scaled_in_place(layer.audio, comp.start, comp.channels, comp.data, d))
            cmd = PatchCommand(label, [patch], params)
            change = cmd.redo(self.project, self.history.store)
            return self._commit(cmd, change, (comp.start, comp.end) if gain_factor(gain_db) > 1.0 else None)

    def mute_selection(self) -> EditResult:
        return self.apply_gain(-math.inf, verb="Mute")

    def delete_spectral(self) -> EditResult:
        """Remove the selected spectral content; duration and alignment unchanged."""
        return self.apply_gain(-math.inf, verb="Delete spectral content")

    def apply_brush(self, brush: BrushShape, gain_db: float, respect_selection: bool = True) -> EditResult:
        """One reduce/amplify brush stroke = one gain operation on its coverage mask = one undo event."""
        sel = Selection.of(brush, channels=self.selection.channels if self.selection else None)
        if respect_selection and self.selection is not None:
            sel = Selection(
                ops=self.selection.ops + (SelectionOp(INTERSECT, brush),),
                feather_t=self.selection.feather_t,
                feather_f=self.selection.feather_f,
                feather_enabled=self.selection.feather_enabled,
                channels=self.selection.channels,
            )
        verb = "Reduce brush" if gain_db < 0 else "Amplify brush"
        return self.apply_gain(gain_db, verb=verb, sel=sel)

    # preview / bypass / apply / cancel
    def preview_gain(self, gain_db: float, verb: str = "Gain") -> EditPreview:
        with self._lock:
            sel = self.selection_or_error()
            layer = self._editable_layer()
            comp = self.component(sel, layer)
            new = apply_gain_to_region(layer.audio, comp, gain_db)
            orig = layer.audio[:, comp.start : comp.end].astype(np.float64)
            self._preview = EditPreview(
                layer.id, layer.version, comp.start, orig, new, gain_db, sel, self.edit_config,
                self._label(verb, sel, layer, gain_db), comp,
            )
            self._emit("preview")
            return self._preview

    def _preview_matches(self, gain_db, sel, layer) -> bool:
        pv = self._preview
        return (
            pv is not None and pv.layer_id == layer.id and pv.layer_version == layer.version
            and pv.selection == sel and pv.config == self.edit_config
            and (pv.gain_db == gain_db or (math.isinf(pv.gain_db) and math.isinf(gain_db)))
        )

    @property
    def preview(self) -> EditPreview | None:
        return self._preview

    def commit_preview(self) -> EditResult:
        pv = self._preview
        if pv is None:
            raise EditError("Nothing to apply: preview first")
        return self.apply_gain(pv.gain_db, sel=pv.selection)

    def cancel_preview(self) -> None:
        if self._preview is not None:
            self._preview = None
            self._emit("preview")

    # extraction
    def extract_to_new_layer(self, copy: bool = False, name: str | None = None) -> EditResult:
        """Move (or copy) the selected component into a new time-aligned layer.

        Move: new = c, remainder = x - c, so new + remainder == x (float64 exact,
        then rounded once to the storage dtype). Copy keeps the source; the
        summed level of the two layers can then increase (up to +6 dB where
        the copy fully overlaps).
        """
        with self._lock:
            sel = self.selection_or_error()
            layer = self._editable_layer() if not copy else self.active_layer
            if layer is None:
                raise EditError("There is no layer")
            comp = self.component(sel, layer)
            dt = np.dtype(self.project.storage_dtype)
            new_audio = np.zeros((self.project.channels, self.project.n_frames), dtype=dt)
            extracted = comp.data.astype(dt)
            new_audio[:, comp.start : comp.end] = extracted
            idx = self.project.index_of(layer.id)
            verb = "Copy to new layer" if copy else "Extract to new layer"
            nl = Layer(
                id=new_id("layer"),
                name=name or f"{layer.name} – {'copy' if copy else 'extract'} {len(self.project.layers)}",
                audio=new_audio,
                color=self.project.next_color(),
                base={"type": "silence"},
                provenance=f"{verb.lower()} from '{layer.name}': {self.selection_label(sel)}",
            )
            add = AddLayerCommand(verb, nl, idx + 1, self.history.store)
            children: list[Command] = [add]
            if not copy:
                remainder = layer.audio[:, comp.start : comp.end].astype(np.float64) - extracted.astype(np.float64)
                children.insert(0, PatchCommand("remainder", [make_patch(self.history.store, layer, comp.start, remainder, comp.channels)]))
            cmd = CompositeCommand(self._label(verb, sel, layer), children, self._params(sel, layer, comp, new_layer_id=nl.id, copy=copy))
            change = cmd.redo(self.project, self.history.store)
            self.active_layer_id = layer.id
            warnings = []
            if copy:
                warnings.append("The copy is summed with the original: overlapping content now plays louder (up to +6 dB).")
            res = self._commit(cmd, change)
            res.warnings.extend(warnings)
            return res

    def transfer_brush(self, brush: BrushShape, target_layer_id: str, amount: float = 1.0) -> EditResult:
        """Move ``amount`` (0..1) of the content under the brush from the active layer
        to the target layer. Source + target is unchanged (before layer gains)."""
        with self._lock:
            src = self._editable_layer()
            if target_layer_id == src.id:
                raise EditError("Choose a different target layer")
            dst = self.project.layer(target_layer_id)
            if dst.locked:
                raise EditError(f"Target layer '{dst.name}' is locked")
            amount = float(np.clip(amount, 0.0, 1.0))
            sel = Selection.of(brush, channels=self.selection.channels if self.selection else None)
            if self.selection is not None:
                sel = Selection(ops=self.selection.ops + (SelectionOp(INTERSECT, brush),), feather_t=self.selection.feather_t,
                                feather_f=self.selection.feather_f, feather_enabled=self.selection.feather_enabled,
                                channels=self.selection.channels)
            comp = self.component(sel, src)
            moved = (amount * comp.data).astype(np.dtype(self.project.storage_dtype)).astype(np.float64)
            new_src = src.audio[:, comp.start : comp.end].astype(np.float64) - moved
            new_dst = dst.audio[:, comp.start : comp.end].astype(np.float64) + moved
            p1 = make_patch(self.history.store, src, comp.start, new_src, comp.channels)
            p2 = make_patch(self.history.store, dst, comp.start, new_dst, comp.channels)
            label = f"Transfer {amount * 100:.0f}% · {self.selection_label(sel)} · {src.name} → {dst.name}"
            cmd = PatchCommand(label, [p1, p2], self._params(sel, src, comp, target=dst.id, amount=amount))
            change = cmd.redo(self.project, self.history.store)
            return self._commit(cmd, change)

    # ------------------------------------------------------------ layers
    def _props_cmd(self, layer: Layer, label: str, **values) -> EditResult:
        for k in values:
            if k not in Layer.PROPS:
                raise KeyError(k)
        before = {k: getattr(layer, k) for k in values}
        if before == values:
            raise EditError("No change")
        cmd = LayerPropsCommand(label, layer.id, before, dict(values))
        change = cmd.redo(self.project, self.history.store)
        return self._commit(cmd, change)

    def set_layer_props(self, layer_id: str, **values) -> EditResult:
        layer = self.project.layer(layer_id)
        if "gain_db" in values:
            values["gain_db"] = float(values["gain_db"])
            if not math.isfinite(values["gain_db"]):
                raise EditError("Layer gain must be a finite dB value")
        desc = ", ".join(f"{k}={v}" for k, v in values.items())
        return self._props_cmd(layer, f"Layer '{layer.name}': {desc}", **values)

    def add_empty_layer(self, name: str | None = None) -> EditResult:
        nl = Layer(id=new_id("layer"), name=name or f"Layer {len(self.project.layers) + 1}",
                   audio=self.project.silent_audio(), color=self.project.next_color(), base={"type": "silence"},
                   provenance="new empty layer")
        cmd = AddLayerCommand(f"New layer '{nl.name}'", nl, len(self.project.layers), self.history.store)
        change = cmd.redo(self.project, self.history.store)
        self.active_layer_id = nl.id
        return self._commit(cmd, change)

    def duplicate_layer(self, layer_id: str) -> EditResult:
        src = self.project.layer(layer_id)
        nl = Layer(id=new_id("layer"), name=f"{src.name} copy", audio=src.audio.copy(), color=self.project.next_color(),
                   gain_db=src.gain_db, polarity_inverted=src.polarity_inverted, base=dict(src.base),
                   provenance=f"duplicate of '{src.name}'")
        cmd = AddLayerCommand(f"Duplicate layer '{src.name}'", nl, self.project.index_of(layer_id) + 1, self.history.store)
        change = cmd.redo(self.project, self.history.store)
        res = self._commit(cmd, change)
        res.warnings.append("The duplicate is summed with the original, so the mix is louder until you mute one.")
        return res

    def delete_layer(self, layer_id: str) -> EditResult:
        layer = self.project.layer(layer_id)
        if layer.locked:
            raise EditError(f"Layer '{layer.name}' is locked")
        if len(self.project.layers) <= 1:
            raise EditError("A project needs at least one layer")
        cmd = RemoveLayerCommand(f"Delete layer '{layer.name}'", layer, self.project.index_of(layer_id))
        change = cmd.redo(self.project, self.history.store)
        return self._commit(cmd, change)

    def move_layer(self, layer_id: str, new_index: int) -> EditResult:
        old = self.project.index_of(layer_id)
        new_index = int(np.clip(new_index, 0, len(self.project.layers) - 1))
        if new_index == old:
            raise EditError("No change")
        cmd = MoveLayerCommand(f"Move layer '{self.project.layer(layer_id).name}'", layer_id, old, new_index)
        change = cmd.redo(self.project, self.history.store)
        return self._commit(cmd, change)

    def merge_layers(self, layer_ids: list[str], name: str | None = None) -> EditResult:
        """Sum layers (with their gain/polarity) into one new unity-gain layer and remove them.

        Mute/solo are ignored for the merged content (it is merged as if audible)."""
        if len(layer_ids) < 2:
            raise EditError("Select at least two layers to merge")
        layers = [self.project.layer(i) for i in layer_ids]
        for l in layers:
            if l.locked:
                raise EditError(f"Layer '{l.name}' is locked")
        dt = np.dtype(self.project.storage_dtype)
        merged = self.project.render_mix(layers=layers).astype(dt)
        idx = min(self.project.index_of(i) for i in layer_ids)
        nl = Layer(id=new_id("layer"), name=name or "Merged", audio=merged, color=self.project.next_color(),
                   base={"type": "silence"}, provenance="merge of " + ", ".join(f"'{l.name}'" for l in layers))
        children: list[Command] = [RemoveLayerCommand("remove", l, self.project.index_of(l.id)) for l in layers]
        # remove in descending index order so stored indices stay valid
        children.sort(key=lambda c: -c.index)
        children.append(AddLayerCommand("add", nl, idx, self.history.store))
        cmd = CompositeCommand(f"Merge {len(layers)} layers", children)
        change = cmd.redo(self.project, self.history.store)
        self.active_layer_id = nl.id
        return self._commit(cmd, change)

    # ------------------------------------------------------- timeline ops
    def _time_range_samples(self, sel: Selection | None = None) -> tuple[int, int]:
        sel = sel or self.selection_or_error()
        b = sel.bounds(self.project.duration, self.project.nyquist)
        if b is None:
            raise EditError("Empty selection")
        a, e = int(round(b[0] * self.sample_rate)), int(round(b[1] * self.sample_rate))
        if e - a <= 0:
            raise EditError("The selected time range is empty")
        return a, e

    def _timeline(self, label: str, fn, params: dict) -> EditResult:
        for l in self.project.layers:
            if l.locked:
                raise EditError(f"Layer '{l.name}' is locked; time operations ripple all layers")
        new = {l.id: np.ascontiguousarray(fn(l.audio)) for l in self.project.layers}
        frames = next(iter(new.values())).shape[1]
        if frames <= 0:
            raise EditError("A project cannot be shortened to zero length")
        cmd: Command = TimelineCommand(label, self.project, self.history.store, new, frames, params)
        had_areas = bool(self.areas)
        if had_areas:  # area components no longer line up with the audio: keep their effect, stop tracking them
            cmd = CompositeCommand(label, [cmd, AreasStateCommand("areas", self, self.areas, ())], params)
        change = cmd.redo(self.project, self.history.store)
        self.set_selection(None)
        res = self._commit(cmd, change)
        res.warnings.append("Time operation rippled ALL layers to keep them in sync. Markers were not moved.")
        if had_areas:
            res.warnings.append("Adjusted areas keep their effect in the audio but can no longer be changed "
                                "individually (the timeline moved). Undo restores them.")
        return res

    def delete_time(self) -> EditResult:
        """Remove the selection's time span from every layer (shortens the project)."""
        a, e = self._time_range_samples()
        return self._timeline(f"Delete time {a / self.sample_rate:.3f}-{e / self.sample_rate:.3f} s (all layers)",
                              lambda x: np.concatenate([x[:, :a], x[:, e:]], axis=1), {"start": a, "end": e})

    def crop_time(self) -> EditResult:
        """Keep only the selection's time span in every layer."""
        a, e = self._time_range_samples()
        return self._timeline(f"Crop to {a / self.sample_rate:.3f}-{e / self.sample_rate:.3f} s (all layers)",
                              lambda x: x[:, a:e].copy(), {"start": a, "end": e})

    def insert_silence(self, at_seconds: float, seconds: float) -> EditResult:
        """Insert silence into every layer at a time position (lengthens the project)."""
        at = int(np.clip(round(at_seconds * self.sample_rate), 0, self.project.n_frames))
        n = int(round(seconds * self.sample_rate))
        if n <= 0:
            raise EditError("Insert length must be positive")
        dt = np.dtype(self.project.storage_dtype)
        return self._timeline(f"Insert {seconds:.3f} s silence at {at / self.sample_rate:.3f} s (all layers)",
                              lambda x: np.concatenate([x[:, :at], np.zeros((x.shape[0], n), dt), x[:, at:]], axis=1),
                              {"at": at, "frames": n})

    # ------------------------------------------------------- adjusted areas
    def _set_areas_raw(self, areas) -> None:
        self.areas = tuple(areas)
        if self.active_area_id is not None and self.area(self.active_area_id) is None:
            self.active_area_id = None
        self._emit("areas")

    def area(self, area_id: str) -> AdjustArea | None:
        return next((a for a in self.areas if a.id == area_id), None)

    def visible_areas(self) -> list[AdjustArea]:
        """Areas whose layer still exists (oldest first = drawing order)."""
        return [a for a in self.areas if self.project.has_layer(a.layer_id)]

    def area_at(self, t: float, f: float) -> AdjustArea | None:
        """Topmost area under (t s, f Hz), preferring the active layer."""
        hits = [a for a in self.visible_areas() if area_contains(a, t, f)]
        if not hits:
            return None
        own = [a for a in hits if a.layer_id == self.active_layer_id]
        return (own or hits)[-1]

    def current_area(self) -> AdjustArea | None:
        """The area the current selection *is* (picked by click, or identical selection)."""
        if self.selection is None:
            return None
        a = self.area(self.active_area_id) if self.active_area_id else None
        if a is not None and a.selection == self.selection and self.project.has_layer(a.layer_id):
            return a
        for a in reversed(self.visible_areas()):
            if a.selection == self.selection and a.layer_id == self.active_layer_id:
                return a
        return None

    def select_area(self, area_id: str | None) -> AdjustArea | None:
        """Pick an area again: its shape becomes the selection and arrow keys adjust it."""
        if area_id is None:
            self.active_area_id = None
            self._emit("areas")
            return None
        a = self.area(area_id)
        if a is None or not self.project.has_layer(a.layer_id):
            raise EditError("That adjusted area no longer exists")
        if a.layer_id != self.active_layer_id:
            self.set_active_layer(a.layer_id)
        self.set_selection(a.selection)
        self.active_area_id = a.id
        self._emit("areas")
        return a

    def _area_label(self, a: AdjustArea, layer: Layer, verb: str | None = None) -> str:
        head = verb or f"{a.name}: {gain_text(a.gain_db)}"
        return " · ".join([head, self.selection_label(a.selection), layer.name])

    def _area_patch(self, layer: Layer, a: AdjustArea, new_gain: float, old_gain: float) -> LayerPatch:
        """Applied patch: the area's span moved from ``old_gain`` to ``new_gain`` in the layer, in place.
        Only the samples from before are copied (for undo); nothing else of span size is allocated."""
        store = self.history.store
        return patch_in_place(store, layer, a.start, a.length, a.channels,
                              lambda: change_area_gain(layer.audio, a, store, new_gain, old_gain))

    def _release_unused_components(self) -> None:
        """Free stored area components that neither the area list nor any undo/redo step refers to
        (e.g. an area whose creation was undone and then dropped from the redo list)."""
        if not self._component_handles:
            return
        used = {a.component for a in self.areas}

        def walk(cmd: Command) -> None:
            if isinstance(cmd, (AreaCommand, AreasStateCommand)):
                used.update(a.component for a in cmd.before + cmd.after)
            elif isinstance(cmd, CompositeCommand):
                for c in cmd.children:
                    walk(c)

        for cmd in self.history.undo_stack + self.history.redo_stack:
            walk(cmd)
        for h in self._component_handles - used:
            self.history.store.release(h)
        self._component_handles &= used

    def adjust_area(self, direction: int = 0, big: bool = False, target_db: float | None = None,
                    area_id: str | None = None, requested_at: float | None = None) -> EditResult | None:
        """Raise/lower the level of the current area – or turn the current selection into a new area.

        ``direction`` +1/−1 steps the gain (see :func:`step_gain`); ``target_db`` sets it directly
        (``-inf`` = silence). Returns None when nothing changes (already at the limit).

        ``requested_at`` is the ``time.monotonic()`` of the key press / click that asked for this
        change (default: now). It decides whether the change merges into the previous undo step:
        a press made while the previous change was still rendering, or within
        :data:`MERGE_WINDOW_S` after it finished, merges – however long the rendering itself takes."""
        with self._lock:
            store = self.history.store
            a = self.area(area_id) if area_id else self.current_area()
            if a is None and area_id:
                raise EditError("That adjusted area no longer exists (it was removed or undone)")
            if a is None:
                sel = self.selection_or_error()
                layer = self._editable_layer()
                g = float(target_db) if target_db is not None else step_gain(0.0, direction, big)
                if g == 0.0:
                    raise EditError("0 dB changes nothing – press ↓ to make it quieter or ↑ to make it louder")
                start, new_c, chans = self._storage_component(sel, layer)
                number = max((x.number for x in self.areas), default=0) + 1
                a = AdjustArea(new_id("area"), number, layer.id, sel, 0.0, start, new_c.shape[1], chans,
                               -1, time.time())  # component handle: stored below, once the audio has changed
                new_area = True
            else:
                layer = self.project.layer(a.layer_id)
                if layer.locked:
                    raise EditError(f"Layer '{layer.name}' is locked; unlock it to change {a.name}")
                new_area = False
                g = float(target_db) if target_db is not None else step_gain(a.gain_db, direction, big)
                if g == a.gain_db:
                    return None
            na = a.with_gain(g)
            before = self.areas
            after = tuple(na if x.id == a.id else x for x in before) if not new_area else before + (na,)
            label = self._area_label(na, layer)
            params = {"area": na.to_dict(), "config": self.edit_config.to_dict(), "action": "adjust"}
            top = self.history.undo_stack[-1] if self.history.undo_stack else None
            merge = (isinstance(top, AreaCommand) and top.area_id == a.id and top.action == "adjust"
                     and not self.history.redo_stack and self.history._saved_marker is not top
                     and (time.monotonic() if requested_at is None else requested_at) - top.touched
                     < MERGE_WINDOW_S
                     and top.patch.start == a.start and top.patch.length == a.length and top.patch.applied)
            if merge:
                # The previous step keeps the samples from before it; only the layer changes.
                snapshot = read_rows(layer.audio, a.channels, a.start, a.end)  # put back if the change fails
                try:
                    change_area_gain(layer.audio, a, store, g, a.gain_db)
                except BaseException:
                    write_rows(layer.audio, a.channels, a.start, snapshot)
                    raise
                del snapshot
                layer.bump()
                cmd = AreaCommand(label, self, top.patch, top.before, after, a.id, "adjust", params)
                change = cmd.redo(self.project, store)
                self.history.undo_stack[-1] = cmd
                self.active_area_id = a.id
                self.operation_log.append(cmd.summary())
                warnings: list[str] = []
                peak = self._check_clip(a.start, a.end, warnings) if g > 0 else None
                self._after_change(change, label)
                return EditResult(cmd, warnings, peak)
            if new_area:
                # The samples from before go into the store first and the component last, so when the
                # store moves data to disk the component (read on every change of the area) stays in RAM.
                d = factor(g) - factor(0.0)
                patch = patch_in_place(store, layer, a.start, a.length, a.channels,
                                       lambda: add_scaled_in_place(layer.audio, a.start, a.channels, new_c, d))
                try:
                    h = store.put(new_c, hot=True)
                except BaseException:
                    write_patch(self.project, store, patch, False)
                    store.release(patch.held)
                    raise
                del new_c
                self._component_handles.add(h)
                a, na = replace(a, component=h), replace(na, component=h)
                after = before + (na,)
            else:
                patch = self._area_patch(layer, a, g, a.gain_db)
            cmd = AreaCommand(label, self, patch, before, after, a.id, "adjust", params)
            change = cmd.redo(self.project, store)  # the patch is applied already: sets the area list
            self.active_area_id = a.id
            return self._commit(cmd, change, (a.start, a.end) if g > 0 else None)

    def set_area_gain(self, area_id: str, gain_db: float) -> EditResult | None:
        return self.adjust_area(target_db=gain_db, area_id=area_id)

    def remove_area(self, area_id: str | None = None) -> EditResult:
        """Undo one area's adjustment exactly (other areas and edits are untouched)."""
        with self._lock:
            a = self.area(area_id) if area_id else self.current_area()
            if a is None:
                raise EditError("Click an adjusted area first (or pick it in the Areas panel)")
            layer = self.project.layer(a.layer_id)
            if layer.locked:
                raise EditError(f"Layer '{layer.name}' is locked; unlock it to remove {a.name}")
            label = self._area_label(a, layer, f"Remove {a.name} ({gain_text(a.gain_db)})")
            params = {"area": a.to_dict(), "action": "remove"}
            after = tuple(x for x in self.areas if x.id != a.id)
            patch = self._area_patch(layer, a, 0.0, a.gain_db)
            cmd = AreaCommand(label, self, patch, self.areas, after, a.id, "remove", params)
            change = cmd.redo(self.project, self.history.store)
            return self._commit(cmd, change)

    def remove_all_areas(self) -> EditResult:
        with self._lock:
            todo = [a for a in reversed(self.visible_areas())]
            if not todo:
                raise EditError("There are no adjusted areas")
            for a in todo:
                if self.project.layer(a.layer_id).locked:
                    raise EditError(f"Layer '{self.project.layer(a.layer_id).name}' is locked")
            children: list[Command] = []
            change = Change()
            try:
                for a in todo:
                    layer = self.project.layer(a.layer_id)
                    after = tuple(x for x in self.areas if x.id != a.id)
                    patch = self._area_patch(layer, a, 0.0, a.gain_db)
                    c = AreaCommand(f"Remove {a.name}", self, patch, self.areas, after, a.id, "remove")
                    children.append(c)
                    change = change.merge(c.redo(self.project, self.history.store))
            except Exception:
                for c in reversed(children):
                    c.undo(self.project, self.history.store)
                    c.release(self.history.store)
                raise
            cmd = CompositeCommand(f"Remove all {len(todo)} adjusted areas", children)
            return self._commit(cmd, change)

    # ------------------------------------------------------------- history
    def undo(self) -> Command | None:
        with self._lock:
            r = self.history.undo(self.project)
            if r is None:
                return None
            cmd, change = r
            self._after_change(change, f"Undo {cmd.label}")
            return cmd

    def redo(self) -> Command | None:
        with self._lock:
            r = self.history.redo(self.project)
            if r is None:
                return None
            cmd, change = r
            self._after_change(change, f"Redo {cmd.label}")
            return cmd

    # ----------------------------------------------------------- audition
    def _f32(self, a: np.ndarray) -> np.ndarray:
        return np.ascontiguousarray(a, dtype=np.float32)

    def render_full_mix(self) -> np.ndarray:
        return self.project.render_mix(dtype=np.float32)

    def audition_selected(self) -> AuditionBuffer:
        """Only the selected spectral content of the active layer (real mask + ISTFT)."""
        layer = self.active_layer
        comp = self.component()
        return AuditionBuffer(comp.start, self._f32(comp.data * layer.mix_factor), "Selected content")

    def audition_remainder(self) -> AuditionBuffer:
        """The active layer over the selection's span with the selected content removed."""
        layer = self.active_layer
        comp = self.component()
        rem = layer.audio[:, comp.start : comp.end].astype(np.float64) - comp.data
        return AuditionBuffer(comp.start, self._f32(rem * layer.mix_factor), "Remainder (selection removed)")

    def audition_all_frequencies(self) -> AuditionBuffer:
        """The audible mix over the selection's time span, all frequencies."""
        sel = self.selection_or_error()
        b = sel.bounds(self.project.duration, self.project.nyquist)
        if b is None:
            raise EditError("Empty selection")
        a, e = int(b[0] * self.sample_rate), int(math.ceil(b[1] * self.sample_rate))
        return AuditionBuffer(a, self.project.render_mix(a, e, dtype=np.float32), "Selection time range, all frequencies")

    def difference_buffer(self) -> AuditionBuffer:
        """Original minus processed for the pending preview, else for the last audio edit."""
        pv = self._preview
        if pv is not None:
            layer = self.project.layer(pv.layer_id)
            return AuditionBuffer(pv.start, self._f32((pv.original - pv.processed) * layer.mix_factor),
                                  f"Removed by preview: {pv.label}")
        for i in range(len(self.history.undo_stack) - 1, -1, -1):
            cmd = self.history.undo_stack[i]
            patches = _patches_of(cmd)
            if patches:
                p = patches[0]
                f = self.project.layer(p.layer_id).mix_factor if self.project.has_layer(p.layer_id) else 1.0
                before, after = patch_versions(self.project, self.history, i, p)
                return AuditionBuffer(p.start, self._f32((before.astype(np.float64) - after.astype(np.float64)) * f),
                                      f"Removed by: {cmd.label}")
        raise EditError("No edit to compare: apply or preview an edit first")

    def ab_regions(self) -> tuple[int, np.ndarray, np.ndarray, str]:
        """(start, mix_region_A_original, mix_region_B_processed, label) for A/B.

        Uses the pending preview if any, otherwise the most recent audio edit."""
        pv = self._preview
        if pv is not None:
            layer = self.project.layer(pv.layer_id)
            a = self.mix_with_override(layer, pv.start, pv.original)
            b = self.mix_with_override(layer, pv.start, pv.processed)
            return pv.start, a, b, pv.label
        for i in range(len(self.history.undo_stack) - 1, -1, -1):
            cmd = self.history.undo_stack[i]
            patches = _patches_of(cmd)
            if patches:
                p = patches[0]
                layer = self.project.layer(p.layer_id)
                a = self.mix_with_override(layer, p.start, patch_versions(self.project, self.history, i, p)[0])
                b = self.project.render_mix(p.start, p.end, dtype=np.float32)
                return p.start, a, b, cmd.label
        raise EditError("No edit to compare: apply or preview an edit first")

    def mix_with_override(self, layer: Layer, start: int, data: np.ndarray) -> np.ndarray:
        n = data.shape[1]
        mix = self.project.render_mix(start, start + n)
        if layer in self.project.audible_layers():
            mix = mix - layer.mix_factor * layer.audio[:, start : start + n] + layer.mix_factor * data
        return mix.astype(np.float32)

    # ------------------------------------------------------- persistence
    def save(self, path: str, portable: bool = False) -> None:
        from spledio.persistence.project_file import save_project

        save_project(self, path, portable=portable)
        self.mark_saved(path)

    @classmethod
    def open_project(cls, path: str, relink: dict[str, str] | None = None) -> "EditorSession":
        from spledio.persistence.project_file import load_project

        return load_project(path, relink=relink)

    def protected_paths(self) -> list[str]:
        out = []
        for a in self.project.assets.values():
            out.append(a.path)
            if a.resolved_path:
                out.append(a.resolved_path)
        return out

    def export(self, path: str, settings, scope: str = "mix", layer_ids: list[str] | None = None,
               time_range_s: tuple[float, float] | None = None):
        """Render and export. scope: 'mix' | 'layers' (sum of given layers at their gains) |
        'selection' (selected component of the active layer)."""
        from spledio.audio.export import export_audio

        data = self.export_source(scope, layer_ids)
        if time_range_s is not None:
            a = max(0, int(round(time_range_s[0] * self.sample_rate)))
            b = min(data.shape[1], int(round(time_range_s[1] * self.sample_rate)))
            if b <= a:
                raise EditError("Export time range is empty")
            data = data.range(a, b)
        return export_audio(data, self.sample_rate, path, settings, protected_paths=self.protected_paths())

    def export_source(self, scope: str = "mix", layer_ids: list[str] | None = None) -> BlockAudio:
        """What an export of ``scope`` writes, as float64 audio rendered block by block when it is read
        (the whole mix of a long project is never held at once). Same samples as before: the mix
        (:meth:`Project.render_mix`), the given layers at their gains, or the selected component of
        the active layer at its gain (silence elsewhere)."""
        p = self.project
        if scope == "mix":
            return p.mix_view()
        if scope == "layers":
            if not layer_ids:
                raise EditError("Choose at least one layer to export")
            return p.mix_view(layers=[p.layer(i) for i in layer_ids])
        if scope == "selection":
            comp, f = self.component(), self.active_layer.mix_factor

            def render(a: int, b: int) -> np.ndarray:
                out = np.zeros((p.channels, max(0, b - a)))
                lo, hi = max(a, comp.start), min(b, comp.end)
                if hi > lo:
                    out[:, lo - a : hi - a] = comp.data[:, lo - comp.start : hi - comp.start] * f
                return out

            return BlockAudio(p.channels, p.n_frames, render)
        raise EditError(f"Unknown export scope {scope!r}")


def _patches_of(cmd: Command):
    if isinstance(cmd, PatchCommand):
        return cmd.patches
    if isinstance(cmd, CompositeCommand):
        out = []
        for c in cmd.children:
            out.extend(_patches_of(c))
        return out
    return []


__all__ = ["EditorSession", "EditError", "SessionEvent", "EditResult", "EditPreview", "AuditionBuffer"]
