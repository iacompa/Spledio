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
from dataclasses import dataclass, field
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
)
from spledio.domain.project import Asset, Layer, Project, new_id
from spledio.engine.areas import (
    MERGE_WINDOW_S,
    AdjustArea,
    AreaCommand,
    AreasStateCommand,
    area_audio,
    area_contains,
    gain_text,
    step_gain,
)
from spledio.dsp.spectral import Component, apply_gain_to_region, gain_factor, selected_component
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
                raise EditError(
                    "The selection covers no analysis cells at the current processing resolution "
                    f"({self.edit_config.frame_spacing(self.sample_rate) * 1000:.1f} ms x "
                    f"{self.edit_config.bin_spacing(self.sample_rate):.1f} Hz); make it larger"
                )
            self._comp_cache = (key, comp)
            return comp

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
        mix = self.project.render_mix(start, end)
        peak = float(np.max(np.abs(mix))) if mix.size else 0.0
        if peak > 1.0:
            warnings.append(
                f"The mix now peaks at {20 * math.log10(peak):+.2f} dBFS in the edited region. Nothing was clipped "
                "internally; 32-bit float export keeps it, integer export will ask how to handle it."
            )
        return peak

    def _commit(self, cmd: Command, change: Change, warnings_region: tuple[int, int] | None = None) -> EditResult:
        self.history.push_applied(cmd)
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
            if self._preview is not None and self._preview_matches(gain_db, sel, layer):
                pv = self._preview
                new = pv.processed
                comp = pv.component
            else:
                comp = self.component(sel, layer)
                new = apply_gain_to_region(layer.audio, comp, gain_db)
            patch = make_patch(self.history.store, layer, comp.start, new, comp.channels)
            label = self._label(verb, sel, layer, None if verb in ("Mute", "Delete spectral content") else gain_db)
            cmd = PatchCommand(label, [patch], self._params(sel, layer, comp, gain_db=gain_db, verb=verb))
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
        then rounded once to the storage dtype). The new layer takes the source
        layer's gain, polarity, mute and solo, so a move leaves the mix
        unchanged. Copy keeps the source; the summed level of the two layers can
        then increase (up to +6 dB where the copy fully overlaps).
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
                gain_db=layer.gain_db,
                mute=layer.mute,
                solo=layer.solo,
                polarity_inverted=layer.polarity_inverted,
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
        to the target layer, keeping the mix unchanged.

        The content is rescaled by the ratio of the two layers' mix factors (gain and
        polarity), so source + target sounds the same at their current levels. A move
        between an audible and a silent layer (mute / solo) cannot keep the mix and is
        refused."""
        with self._lock:
            src = self._editable_layer()
            if target_layer_id == src.id:
                raise EditError("Choose a different target layer")
            dst = self.project.layer(target_layer_id)
            if dst.locked:
                raise EditError(f"Target layer '{dst.name}' is locked")
            audible = self.project.audible_layers()
            if (src in audible) != (dst in audible):
                silent = dst if src in audible else src
                raise EditError(f"Layer '{silent.name}' is muted or not soloed, so the moved sound would "
                                f"{'go silent' if silent is dst else 'become audible'}. Change its mute / solo first.")
            if dst.mix_factor == 0.0:
                raise EditError(f"Target layer '{dst.name}' is at -inf dB; raise its level first")
            ratio = src.mix_factor / dst.mix_factor
            amount = float(np.clip(amount, 0.0, 1.0))
            sel = Selection.of(brush, channels=self.selection.channels if self.selection else None)
            if self.selection is not None:
                sel = Selection(ops=self.selection.ops + (SelectionOp(INTERSECT, brush),), feather_t=self.selection.feather_t,
                                feather_f=self.selection.feather_f, feather_enabled=self.selection.feather_enabled,
                                channels=self.selection.channels)
            comp = self.component(sel, src)
            dt = np.dtype(self.project.storage_dtype)
            moved = (amount * comp.data).astype(dt).astype(np.float64)
            new_src = src.audio[:, comp.start : comp.end].astype(np.float64) - moved
            new_dst = dst.audio[:, comp.start : comp.end].astype(np.float64) + (ratio * moved if ratio != 1.0 else moved)
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
    def time_range_samples(self, sel: Selection | None = None) -> tuple[int, int]:
        """The sample span Delete/Crop Time act on: the drawn shape's time extent.

        The feather is a soft spectral edge, not part of the time range, so it is
        ignored here; otherwise a feathered box would delete (or keep) more time
        than the user drew."""
        sel = (sel or self.selection_or_error()).with_feather(enabled=False)
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
        children = [cmd]
        had_areas = bool(self.areas)
        if had_areas:  # area components no longer line up with the audio: keep their effect, stop tracking them
            children.append(AreasStateCommand("areas", self, self.areas, ()))
        if self.selection is not None:  # it no longer lines up either; part of the same step, so undo brings it back
            children.append(SelectionCommand("selection", self, self.selection, None))
        if len(children) > 1:
            cmd = CompositeCommand(label, children, params)
        change = cmd.redo(self.project, self.history.store)
        res = self._commit(cmd, change)
        res.warnings.append("Time operation rippled ALL layers to keep them in sync. Markers were not moved.")
        if had_areas:
            res.warnings.append("Adjusted areas keep their effect in the audio but can no longer be changed "
                                "individually (the timeline moved). Undo restores them.")
        return res

    def delete_time(self) -> EditResult:
        """Remove the selection's time span from every layer (shortens the project)."""
        a, e = self.time_range_samples()
        return self._timeline(f"Delete time {a / self.sample_rate:.3f}-{e / self.sample_rate:.3f} s (all layers)",
                              lambda x: np.concatenate([x[:, :a], x[:, e:]], axis=1), {"start": a, "end": e})

    def crop_time(self) -> EditResult:
        """Keep only the selection's time span in every layer."""
        a, e = self.time_range_samples()
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
        new = area_audio(layer.audio, a, self.history.store, new_gain, old_gain)
        return make_patch(self.history.store, layer, a.start, new, a.channels)

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
                comp = self.component(sel, layer)
                dt = np.dtype(np.float64 if self.project.storage_dtype == "float64" else np.float32)
                number = max((x.number for x in self.areas), default=0) + 1
                a = AdjustArea(new_id("area"), number, layer.id, sel, 0.0, comp.start, comp.data.shape[1],
                               tuple(comp.channels), store.put(comp.data.astype(dt)), time.time())
                new_area = True
            else:
                layer = self.project.layer(a.layer_id)
                if layer.locked:
                    raise EditError(f"Layer '{layer.name}' is locked; unlock it to change {a.name}")
                new_area = False
            g = float(target_db) if target_db is not None else step_gain(a.gain_db, direction, big)
            if g == a.gain_db:
                if new_area:
                    store.release(a.component)
                    raise EditError("0 dB changes nothing – press ↓ to make it quieter or ↑ to make it louder")
                return None
            patch = self._area_patch(layer, a, g, a.gain_db)
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
                     and top.patch.start == patch.start and top.patch.length == patch.length)
            if merge:
                merged = LayerPatch(layer.id, patch.start, top.patch.before, patch.after, patch.channels, patch.length)
                store.release(patch.before)
                store.release(top.patch.after)
                cmd = AreaCommand(label, self, merged, top.before, after, a.id, "adjust", params)
                change = cmd.redo(self.project, store)
                self.history.undo_stack[-1] = cmd
                self.active_area_id = a.id
                self.operation_log.append(cmd.summary())
                warnings: list[str] = []
                peak = self._check_clip(a.start, a.end, warnings) if g > 0 else None
                self._after_change(change, label)
                return EditResult(cmd, warnings, peak)
            cmd = AreaCommand(label, self, patch, before, after, a.id, "adjust", params)
            change = cmd.redo(self.project, store)
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
            patch = self._area_patch(layer, a, 0.0, a.gain_db)
            after = tuple(x for x in self.areas if x.id != a.id)
            cmd = AreaCommand(self._area_label(a, layer, f"Remove {a.name} ({gain_text(a.gain_db)})"), self, patch,
                              self.areas, after, a.id, "remove", {"area": a.to_dict(), "action": "remove"})
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
                    patch = self._area_patch(layer, a, 0.0, a.gain_db)
                    after = tuple(x for x in self.areas if x.id != a.id)
                    c = AreaCommand(f"Remove {a.name}", self, patch, self.areas, after, a.id, "remove")
                    change = change.merge(c.redo(self.project, self.history.store))
                    children.append(c)
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
        cmd, start, parts = self._last_edit()
        diff = sum(layer.mix_factor * (before - after) for layer, before, after in parts)
        return AuditionBuffer(start, self._f32(diff), f"Removed by: {cmd.label}")

    def _last_edit(self) -> tuple[Command, int, list[tuple[Layer, np.ndarray, np.ndarray]]]:
        """The most recent audio edit that can still be compared (A/B, Difference), whole.

        Returns ``(command, start, parts)``: every layer the edit changed, with its audio over
        the span covering all of the edit's parts as it was before and as it is now (float64).
        A layer the edit created (Extract) is silent before. Gain edits, brush strokes,
        transfers, extractions and adjusted-area changes count. A time edit (delete / crop /
        insert) moves the audio, so no older edit lines up any more; neither does an edit on a
        layer that has been deleted since."""
        for cmd in reversed(self.history.undo_stack):
            if _moves_timeline(cmd):
                break
            patches = _patches_of(cmd)
            if not patches:
                continue
            if not all(self.project.has_layer(p.layer_id) for p in patches):
                break
            start = min(p.start for p in patches)
            end = max(p.end for p in patches)
            added = _layers_added_by(cmd)
            parts: list[tuple[Layer, np.ndarray, np.ndarray]] = []
            for lid in dict.fromkeys(p.layer_id for p in patches):
                if lid in added:
                    continue  # silent before the edit, whatever was written into it
                layer = self.project.layer(lid)
                after = layer.audio[:, start:end].astype(np.float64)
                before = after.copy()
                for p in reversed([p for p in patches if p.layer_id == lid]):  # undo the parts in reverse
                    before[:, p.start - start : p.end - start] = self.history.store.get(p.before)
                parts.append((layer, before, after))
            for lid in added:
                if self.project.has_layer(lid):
                    layer = self.project.layer(lid)
                    after = layer.audio[:, start:end].astype(np.float64)
                    parts.append((layer, np.zeros_like(after), after))
            return cmd, start, parts
        raise EditError("No edit to compare: apply or preview an edit first")

    def ab_regions(self) -> tuple[int, np.ndarray, np.ndarray, str]:
        """(start, mix_region_A_original, mix_region_B_processed, label) for A/B.

        Uses the pending preview if any, otherwise the whole of the most recent audio edit."""
        pv = self._preview
        if pv is not None:
            layer = self.project.layer(pv.layer_id)
            a = self.mix_with_override(layer, pv.start, pv.original)
            b = self.mix_with_override(layer, pv.start, pv.processed)
            return pv.start, a, b, pv.label
        cmd, start, parts = self._last_edit()
        b = self.project.render_mix(start, start + parts[0][2].shape[1])
        a = b.copy()
        audible = self.project.audible_layers()
        for layer, before, after in parts:
            if layer in audible:
                a += layer.mix_factor * (before - after)
        return start, a.astype(np.float32), b.astype(np.float32), cmd.label

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

        if scope == "mix":
            data = self.project.render_mix()
            start = 0
        elif scope == "layers":
            if not layer_ids:
                raise EditError("Choose at least one layer to export")
            data = self.project.render_mix(layers=[self.project.layer(i) for i in layer_ids])
            start = 0
        elif scope == "selection":
            comp = self.component()
            data = np.zeros((self.project.channels, self.project.n_frames))
            data[:, comp.start : comp.end] = comp.data * self.active_layer.mix_factor
            start = 0
        else:
            raise EditError(f"Unknown export scope {scope!r}")
        if time_range_s is not None:
            a = max(0, int(round(time_range_s[0] * self.sample_rate)) - start)
            b = min(data.shape[1], int(round(time_range_s[1] * self.sample_rate)) - start)
            if b <= a:
                raise EditError("Export time range is empty")
            data = data[:, a:b]
        return export_audio(data, self.sample_rate, path, settings, protected_paths=self.protected_paths())


def _patches_of(cmd: Command):
    if isinstance(cmd, PatchCommand):
        return cmd.patches
    if isinstance(cmd, AreaCommand):
        return [cmd.patch]
    if isinstance(cmd, CompositeCommand):
        out = []
        for c in cmd.children:
            out.extend(_patches_of(c))
        return out
    return []


def _layers_added_by(cmd: Command) -> list[str]:
    if isinstance(cmd, AddLayerCommand):
        return [cmd.layer.id]
    if isinstance(cmd, CompositeCommand):
        return [lid for c in cmd.children for lid in _layers_added_by(c)]
    return []


def _moves_timeline(cmd: Command) -> bool:
    if isinstance(cmd, TimelineCommand):
        return True
    return isinstance(cmd, CompositeCommand) and any(_moves_timeline(c) for c in cmd.children)


__all__ = ["EditorSession", "EditError", "SessionEvent", "EditResult", "EditPreview", "AuditionBuffer"]
