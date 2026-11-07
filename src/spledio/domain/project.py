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
    def render_mix(
        self, start: int = 0, end: int | None = None, layers: list[Layer] | None = None, dtype=np.float64
    ) -> np.ndarray:
        """Audible mix (or the given layers, honouring their gain/polarity) for ``[start, end)``."""
        end = self.n_frames if end is None else end
        start, end = max(0, start), min(self.n_frames, end)
        out = np.zeros((self.channels, max(0, end - start)), dtype=np.float64)
        for l in self.audible_layers() if layers is None else layers:
            f = l.mix_factor
            if f == 0.0:
                continue
            if f == 1.0:
                out += l.audio[:, start:end]
            else:
                out += f * l.audio[:, start:end].astype(np.float64)
        return out.astype(dtype, copy=False)

    def layer_is_identity_of_asset(self) -> bool:
        return len(self.layers) == 1 and self.layers[0].mix_factor == 1.0 and not self.layers[0].mute
