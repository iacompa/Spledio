"""Project / layer / asset model and the single shared mix renderer.

Audibility rules (documented in docs/ACTIONS.md):

* A layer is *audible* when it is not muted and either no layer is soloed or
  it is soloed itself. (Solo does not override mute.)
* ``visible`` only affects the display; it never changes audio.
* The mix is ``sum(audible layer audio * 10**(gain_db/20) * (-1 if polarity))``
  computed in float64. Playback, A/B, and export all call
  :meth:`Project.render_mix`, so preview and export share one code path.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

import numpy as np

from spledio.dsp.stft import DEFAULT_EDIT_CONFIG, STFTConfig

MIX_BLOCK = 1 << 18  # frames per block when a mix is rendered block by block

LAYER_COLORS = ("#4fb3ff", "#ff9f43", "#7bd88f", "#e86fd8", "#ffd166", "#5ce1e6", "#ff6b6b", "#b39ddb")


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


@dataclass
class Asset:
    """Immutable reference to an imported source file (never modified)."""

    id: str
    path: str
    sha256: str
    format: str
    subtype: str
    sample_rate: int
    channels: int
    frames: int
    embedded_member: str | None = None
    resolved_path: str | None = None  # runtime only: where the bytes are readable now (not saved)

    @property
    def readable_path(self) -> str:
        return self.resolved_path or self.path

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "path": self.path,
            "sha256": self.sha256,
            "format": self.format,
            "subtype": self.subtype,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "frames": self.frames,
            "embedded_member": self.embedded_member,
        }


@dataclass
class Layer:
    id: str
    name: str
    audio: np.ndarray  # (channels, frames), project storage dtype; mutated only via commands
    color: str = LAYER_COLORS[0]
    gain_db: float = 0.0
    mute: bool = False
    solo: bool = False
    locked: bool = False
    polarity_inverted: bool = False
    visible: bool = True
    base: dict = field(default_factory=lambda: {"type": "silence"})
    provenance: str = ""
    version: int = 0

    PROPS = ("name", "color", "gain_db", "mute", "solo", "locked", "polarity_inverted", "visible")

    @property
    def mix_factor(self) -> float:
        g = 10.0 ** (self.gain_db / 20.0)
        return -g if self.polarity_inverted else g

    def props(self) -> dict:
        return {k: getattr(self, k) for k in self.PROPS}

    def bump(self) -> None:
        self.version += 1


@dataclass
class Marker:
    id: str
    time: float
    name: str = ""
    end: float | None = None  # region marker when set

    def to_dict(self):
        return {"id": self.id, "time": self.time, "name": self.name, "end": self.end}


@dataclass
class Project:
    sample_rate: int
    n_frames: int
    channels: int
    storage_dtype: str = "float32"
    id: str = field(default_factory=lambda: new_id("proj"))
    name: str = "Untitled"
    assets: dict[str, Asset] = field(default_factory=dict)
    layers: list[Layer] = field(default_factory=list)
    processing_config: STFTConfig = DEFAULT_EDIT_CONFIG
    markers: list[Marker] = field(default_factory=list)
    created: float = field(default_factory=time.time)
    mix_version: int = 0

    # ----- layers ---------------------------------------------------------
    def layer(self, layer_id: str) -> Layer:
        for l in self.layers:
            if l.id == layer_id:
                return l
        raise KeyError(layer_id)

    def has_layer(self, layer_id: str) -> bool:
        return any(l.id == layer_id for l in self.layers)

    def index_of(self, layer_id: str) -> int:
        for i, l in enumerate(self.layers):
            if l.id == layer_id:
                return i
        raise KeyError(layer_id)

    def next_color(self) -> str:
        return LAYER_COLORS[len(self.layers) % len(LAYER_COLORS)]

    def silent_audio(self) -> np.ndarray:
        return np.zeros((self.channels, self.n_frames), dtype=self.storage_dtype)

    def audible_layers(self) -> list[Layer]:
        any_solo = any(l.solo for l in self.layers)
        return [l for l in self.layers if not l.mute and (not any_solo or l.solo)]

    @property
    def duration(self) -> float:
        return self.n_frames / self.sample_rate

    @property
    def nyquist(self) -> float:
        return self.sample_rate / 2.0

    # ----- rendering ----------------------------------------------------------
    def _mix_sources(self, layers: list[Layer] | None) -> list[tuple[np.ndarray, float]]:
        return [(l.audio, l.mix_factor) for l in (self.audible_layers() if layers is None else layers)]

    def render_mix(
        self, start: int = 0, end: int | None = None, layers: list[Layer] | None = None, dtype=np.float64
    ) -> np.ndarray:
        """Audible mix (or the given layers, honouring their gain/polarity) for ``[start, end)``.

        Summed in float64. For another ``dtype`` the mix is summed and rounded block by block,
        so no float64 copy of the whole range is made (the same values: the mix is per sample)."""
        end = self.n_frames if end is None else end
        start, end = max(0, start), min(self.n_frames, end)
        sources = self._mix_sources(layers)
        dtype = np.dtype(dtype)
        if dtype == np.float64:
            return _mix_block(sources, self.channels, start, end)
        out = np.empty((self.channels, max(0, end - start)), dtype=dtype)
        for a in range(start, end, MIX_BLOCK):
            b = min(end, a + MIX_BLOCK)
            out[:, a - start : b - start] = _mix_block(sources, self.channels, a, b)
        return out

    def mix_view(self, start: int = 0, end: int | None = None, layers: list[Layer] | None = None) -> "BlockAudio":
        """The same float64 mix as :meth:`render_mix`, rendered only block by block as it is read
        (for export and peak measurement of long projects). The layers and their gains are taken
        now; layer audio is read when a block is rendered."""
        end = self.n_frames if end is None else end
        start, end = max(0, start), min(self.n_frames, end)
        sources, ch = self._mix_sources(layers), self.channels
        return BlockAudio(ch, end - start, lambda a, b: _mix_block(sources, ch, start + a, start + b))

    def layer_is_identity_of_asset(self) -> bool:
        return len(self.layers) == 1 and self.layers[0].mix_factor == 1.0 and not self.layers[0].mute


def _mix_block(sources: list[tuple[np.ndarray, float]], channels: int, start: int, end: int) -> np.ndarray:
    """float64 sum of ``audio * factor`` over ``[start, end)`` (the one mix rule; see module docstring)."""
    out = np.zeros((channels, max(0, end - start)), dtype=np.float64)
    for audio, f in sources:
        if f == 0.0:
            continue
        if f == 1.0:
            out += audio[:, start:end]
        else:
            out += f * audio[:, start:end].astype(np.float64)
    return out


class BlockAudio:
    """A read-only ``(channels, frames)`` float64 signal that is rendered on demand.

    ``x[:, a:b]`` (and ``x[c]``, ``x[c, a:b]``) render just that part and return an ndarray;
    :meth:`range` gives a sub-view without rendering anything; ``np.asarray(x)`` renders it all.
    The export code reads it block by block, so a long mix is never held whole."""

    ndim = 2
    dtype = np.dtype(np.float64)
    renders_blocks = True  # marker for export: slice it in blocks instead of converting it whole

    def __init__(self, channels: int, frames: int, render, offset: int = 0):
        self.shape = (int(channels), max(0, int(frames)))
        self._render, self._offset = render, int(offset)

    def range(self, a: int, b: int) -> "BlockAudio":
        a, b = max(0, min(a, self.shape[1])), max(0, min(b, self.shape[1]))
        return BlockAudio(self.shape[0], max(0, b - a), self._render, self._offset + a)

    def _block(self, a: int, b: int) -> np.ndarray:
        return self._render(self._offset + a, self._offset + max(a, b))

    def __getitem__(self, key):
        if not isinstance(key, tuple):
            key = (key,)
        rows = key[0]
        cols = key[1] if len(key) > 1 else slice(None)
        if len(key) > 2 or not isinstance(cols, slice) or cols.step not in (None, 1):
            raise IndexError("BlockAudio supports [rows] and [rows, a:b] only")
        a, b, _ = cols.indices(self.shape[1])
        return self._block(a, b)[rows]

    def __array__(self, dtype=None, copy=None):
        out = self._block(0, self.shape[1])
        return out if dtype is None else out.astype(dtype, copy=False)

    def __len__(self) -> int:
        return self.shape[0]
