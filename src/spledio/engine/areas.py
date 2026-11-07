"""Adjusted areas: re-adjustable, removable gain regions.

The user selects a region and nudges its level up or down (keys or the Areas
panel). Every such region is an :class:`AdjustArea` that remembers the
selected *component* ``c`` of the layer audio at the moment the area was
created (``c = ISTFT(M · STFT(x))``, the same phase-preserving extraction used
by all spectral edits). With the area at gain ``G`` the layer contains
``x + (G − 1)·c``. Changing the gain to ``G'`` adds ``(G' − G)·c``; removing
the area adds ``(1 − G)·c``. This bookkeeping is linear, so:

* adjusting or removing one area never disturbs other areas or other edits,
  even where they overlap (each only ever adds/subtracts its own component);
* going down to silence (``G = 0``) and back up again is exact – the
  component is kept, so nothing is lost;
* each change is an ordinary undoable command, and rapid repeated key presses
  on the same area are merged into one undo step.

No Qt here; the UI calls :class:`spledio.engine.session.EditorSession` methods.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, replace

import numpy as np

from spledio.commands.history import Change, Command, LayerPatch, PatchStore, write_patch
from spledio.dsp.spectral import add_scaled_in_place
from spledio.selections.selection import Selection

MUTE = -math.inf
MAX_BOOST_DB = 24.0
FLOOR_DB = -48.0  # stepping below this goes to silence
STEP_DB = 1.0  # ↑ / ↓ (and + / −)
BIG_STEP_DB = 6.0  # Shift+↑ / Shift+↓
MERGE_WINDOW_S = 2.0  # repeated adjustments of one area within this time = one undo step


def step_gain(current_db: float, direction: int, big: bool = False) -> float:
    """Next gain when the user presses up (+1) or down (−1).

    Normal: ±1 dB. ``big`` (Shift): to the next multiple of 6 dB (−4 → −6 → −12 …).
    Below −48 dB the next step down is silence; up from silence returns to −48 dB.
    Capped at +24 dB."""
    if direction == 0:
        return current_db
    if math.isinf(current_db) and current_db < 0:
        return FLOOR_DB if direction > 0 else MUTE
    if big:
        q = current_db / BIG_STEP_DB
        g = (math.floor(q + 1e-9) + 1) * BIG_STEP_DB if direction > 0 else (math.ceil(q - 1e-9) - 1) * BIG_STEP_DB
    else:
        g = current_db + (STEP_DB if direction > 0 else -STEP_DB)
    if g < FLOOR_DB - 1e-9:
        return MUTE
    return float(min(MAX_BOOST_DB, round(g, 6)))


def gain_text(g: float) -> str:
    if math.isinf(g) and g < 0:
        return "Silenced"
    if abs(g) < 0.05:
        return "0 dB"
    return f"{g:+.0f} dB" if abs(g - round(g)) < 0.05 else f"{g:+.1f} dB"


def factor(g: float) -> float:
    return 0.0 if (math.isinf(g) and g < 0) else float(10.0 ** (g / 20.0))


@dataclass(frozen=True)
class AdjustArea:
    id: str
    number: int  # "Area 3" (display only)
    layer_id: str
    selection: Selection
    gain_db: float
    start: int  # component start sample
    length: int
    channels: tuple[int, ...]
    component: int  # PatchStore handle: (channels, length) component c of the audio when the area was made
    created: float = 0.0

    @property
    def end(self) -> int:
        return self.start + self.length

    @property
    def name(self) -> str:
        return f"Area {self.number}"

    def with_gain(self, g: float) -> "AdjustArea":
        return replace(self, gain_db=float(g))

    def to_dict(self) -> dict:
        return {
            "id": self.id, "number": self.number, "layer_id": self.layer_id,
            "selection": self.selection.to_dict(),
            "gain_db": "-inf" if math.isinf(self.gain_db) else float(self.gain_db),
            "start": int(self.start), "length": int(self.length), "channels": list(self.channels),
            "created": float(self.created),
        }


class AreasStateCommand(Command):
    """Replace the session's area list (no audio change); used inside composites."""

    kind = "areas"
    changes_audio = False

    def __init__(self, label: str, session, before: tuple[AdjustArea, ...], after: tuple[AdjustArea, ...]):
        super().__init__(label)
        self._session, self.before, self.after = session, tuple(before), tuple(after)

    def redo(self, project, store):
        self._session._set_areas_raw(self.after)
        return Change()

    def undo(self, project, store):
        self._session._set_areas_raw(self.before)
        return Change()


class AreaCommand(Command):
    """One area change: an audio patch on the area's span + the area list before/after."""

    kind = "area"

    def __init__(self, label: str, session, patch: LayerPatch, before: tuple[AdjustArea, ...],
                 after: tuple[AdjustArea, ...], area_id: str, action: str, params: dict | None = None):
        super().__init__(label, params)
        self._session = session
        self.patch = patch
        self.before, self.after = tuple(before), tuple(after)
        self.area_id = area_id
        self.action = action  # "adjust" | "remove"
        self.touched = time.monotonic()

    def redo(self, project, store):
        ch = Change(audio=[write_patch(project, store, self.patch, True)])
        self._session._set_areas_raw(self.after)
        return ch

    def undo(self, project, store):
        ch = Change(audio=[write_patch(project, store, self.patch, False)])
        self._session._set_areas_raw(self.before)
        return ch

    def release(self, store):
        store.release(self.patch.held)


def change_area_gain(layer_audio: np.ndarray, area: AdjustArea, store: PatchStore, new_gain: float,
                     old_gain: float | None = None) -> None:
    """Move the area's span in ``layer_audio`` from ``old_gain`` to ``new_gain``, in place:
    each sample becomes ``x + (G' − G)·c``, computed in float64 and rounded once to the layer's
    dtype (:func:`~spledio.dsp.spectral.add_scaled_in_place`), so no full-size float64 copy of
    the span or of ``c`` is made. The component is read before any sample changes."""
    old = area.gain_db if old_gain is None else old_gain
    c = store.get(area.component)
    add_scaled_in_place(layer_audio, area.start, area.channels, c, factor(new_gain) - factor(old))


def area_contains(area: AdjustArea, t: float, f: float) -> bool:
    b = area.selection.bounds(math.inf, math.inf)
    if b is not None:
        t0, t1, f0, f1 = b
        if not (t0 <= t <= t1 and f0 <= f <= f1):
            return False
    m = area.selection.evaluate(np.array([[t]]), np.array([[f]]))
    return bool(m.ravel()[0] > 0.5)


__all__ = ["AdjustArea", "AreaCommand", "AreasStateCommand", "step_gain", "gain_text", "change_area_gain",
           "area_contains", "MUTE", "MERGE_WINDOW_S", "FLOOR_DB", "MAX_BOOST_DB", "STEP_DB", "BIG_STEP_DB"]
