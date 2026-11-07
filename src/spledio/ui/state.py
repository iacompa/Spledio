"""View and tool state models shared by widgets (Qt objects, GUI thread only).

ViewState holds *display-only* settings. Nothing here can change audio:
the spectrogram analyser reads these values, the DSP never does.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QObject, Signal

from spledio.dsp.stft import STFTConfig

TOOLS = (
    # id, label, group, tooltip
    ("time", "Time range", "select", "Select a time span across all frequencies (drag left/right)"),
    ("freq", "Frequency range", "select", "Select a frequency band over the whole duration (drag up/down)"),
    ("rect", "Rectangle", "select", "Drag a time-frequency box"),
    ("ellipse", "Ellipse", "select", "Drag an elliptical time-frequency region"),
    ("lasso", "Freehand lasso", "select", "Draw an arbitrary closed selection"),
    ("polygon", "Polygon lasso", "select", "Click vertices; finish with Enter or double-click, cancel with Esc"),
    ("sel_brush", "Selection brush", "select", "Paint a soft selection (does not change audio)"),
    ("transform", "Transform selection", "select", "Drag to move the selection mask only (never the sound)"),
    ("reduce_brush", "Reduce brush", "edit", "Paint attenuation onto the spectral content (one stroke = one undo)"),
    ("amplify_brush", "Amplify brush", "edit", "Paint a gain boost onto the spectral content (one stroke = one undo)"),
    ("transfer_brush", "Transfer brush", "edit", "Move part of the painted content to another layer (sum preserved)"),
    ("hand", "Hand / pan", "navigate", "Drag to pan the view"),
    ("zoom", "Zoom", "navigate", "Click to zoom in at the cursor, Alt+click to zoom out, drag to zoom to a box"),
)
TOOL_IDS = tuple(t[0] for t in TOOLS)
SELECTION_MODES = ("replace", "add", "subtract", "intersect")


class ViewState(QObject):
    """Viewport + display settings. ``changed`` carries 'viewport', 'display' or 'analysis'."""

    changed = Signal(str)

    MIN_SPAN_S = 0.002

    def __init__(self, parent=None):
        super().__init__(parent)
        self.duration = 1.0
        self.nyquist = 22050.0
        self.t0 = 0.0
        self.t1 = 1.0
        self.f_lo = 0.0
        self.f_hi = 22050.0
        self.freq_scale = "log"  # 'linear' | 'log'
        self.log_floor_hz = 20.0
        self.colormap = "ember"
        self.min_db = -110.0
        self.max_db = 0.0
        self.gamma = 1.0
        self.overlay_opacity = 0.35
        self.show_crosshair = True
        self.show_note_labels = False
        self.show_overview = True
        self.show_waveform = True
        self.show_mask_overlay = True
        self.show_areas = True  # outline + label every adjusted area ("Highlight" toggle)
        self.display_config = STFTConfig(4096, 1024, "hann")
        self.channel_mode = "mix"  # 'mix' | 'ch0' | 'ch1' ...
        self.layer_display = "composite"  # 'composite' | 'active'
        self.playhead_follow = True

    # ----- project binding --------------------------------------------------
    def bind_project(self, duration: float, nyquist: float) -> None:
        self.duration = max(duration, 1e-3)
        self.nyquist = nyquist
        self.fit_project(emit=False)
        self.changed.emit("viewport")

    # ----- viewport ------------------------------------------------------------
    def set_time_range(self, t0: float, t1: float, emit: bool = True) -> None:
        span = max(self.MIN_SPAN_S, t1 - t0)
        span = min(span, self.duration * 1.0 + 0.0)
        t0 = min(max(t0, 0.0), max(0.0, self.duration - span))
        self.t0, self.t1 = t0, t0 + span
        if emit:
            self.changed.emit("viewport")

    def set_freq_range(self, f_lo: float, f_hi: float, emit: bool = True) -> None:
        f_lo, f_hi = max(0.0, min(f_lo, f_hi)), min(self.nyquist, max(f_lo, f_hi))
        min_span = 5.0
        if f_hi - f_lo < min_span:
            mid = 0.5 * (f_lo + f_hi)
            f_lo, f_hi = max(0.0, mid - min_span / 2), min(self.nyquist, mid + min_span / 2)
        self.f_lo, self.f_hi = f_lo, f_hi
        if emit:
            self.changed.emit("viewport")

    def zoom_time(self, factor: float, anchor_t: float | None = None) -> None:
        """factor < 1 zooms in. The anchor time stays at the same screen position."""
        anchor = 0.5 * (self.t0 + self.t1) if anchor_t is None else anchor_t
        frac = (anchor - self.t0) / max(self.t1 - self.t0, 1e-12)
        span = min(self.duration, max(self.MIN_SPAN_S, (self.t1 - self.t0) * factor))
        self.set_time_range(anchor - frac * span, anchor - frac * span + span)

    def zoom_freq(self, factor: float, anchor_f: float | None = None) -> None:
        if self.freq_scale == "log":
            lo = max(self.f_lo, self.log_floor_hz)
            anchor = math.sqrt(lo * self.f_hi) if anchor_f is None else max(anchor_f, self.log_floor_hz)
            la, l0, l1 = math.log(anchor), math.log(lo), math.log(self.f_hi)
            frac = (la - l0) / max(l1 - l0, 1e-12)
            span = (l1 - l0) * factor
            n0 = la - frac * span
            new_lo = math.exp(n0)
            new_hi = math.exp(n0 + span)
            if new_lo <= self.log_floor_hz * 1.0001:
                new_lo = 0.0 if self.f_lo <= 0 else self.log_floor_hz
            self.set_freq_range(new_lo, new_hi)
        else:
            anchor = 0.5 * (self.f_lo + self.f_hi) if anchor_f is None else anchor_f
            frac = (anchor - self.f_lo) / max(self.f_hi - self.f_lo, 1e-12)
            span = min(self.nyquist, (self.f_hi - self.f_lo) * factor)
            self.set_freq_range(anchor - frac * span, anchor - frac * span + span)

    def pan_time(self, dt: float) -> None:
        self.set_time_range(self.t0 + dt, self.t1 + dt)

    def fit_project(self, emit: bool = True) -> None:
        self.t0, self.t1 = 0.0, self.duration
        self.f_lo, self.f_hi = 0.0, self.nyquist
        if emit:
            self.changed.emit("viewport")

    def fit_box(self, t0, t1, f0, f1, margin: float = 0.08) -> None:
        dt = max(t1 - t0, self.MIN_SPAN_S)
        self.set_time_range(t0 - margin * dt, t1 + margin * dt, emit=False)
        if self.freq_scale == "log" and f0 > 0:
            r = (f1 / max(f0, 1.0)) ** margin
            self.set_freq_range(f0 / r, f1 * r, emit=False)
        else:
            df = max(f1 - f0, 10.0)
            self.set_freq_range(f0 - margin * df, f1 + margin * df, emit=False)
        self.changed.emit("viewport")

    def reset(self) -> None:
        self.colormap = "ember"
        self.min_db, self.max_db, self.gamma = -110.0, 0.0, 1.0
        self.overlay_opacity = 0.35
        self.freq_scale = "log"
        self.fit_project(emit=False)
        self.changed.emit("display")
        self.changed.emit("viewport")

    # ----- display ---------------------------------------------------------------
    def set_display(self, **kw) -> None:
        analysis = False
        for k, v in kw.items():
            if not hasattr(self, k):
                raise AttributeError(k)
            if k in ("display_config", "channel_mode", "layer_display"):
                analysis = analysis or getattr(self, k) != v
            setattr(self, k, v)
        self.changed.emit("analysis" if analysis else "display")

    def to_dict(self) -> dict:
        return {
            "t0": self.t0, "t1": self.t1, "f_lo": self.f_lo, "f_hi": self.f_hi, "freq_scale": self.freq_scale,
            "colormap": self.colormap, "min_db": self.min_db, "max_db": self.max_db, "gamma": self.gamma,
            "overlay_opacity": self.overlay_opacity, "display_config": self.display_config.to_dict(),
            "channel_mode": self.channel_mode, "layer_display": self.layer_display,
            "show_areas": self.show_areas,
        }

    def load_dict(self, d: dict) -> None:
        try:
            for k in ("freq_scale", "colormap", "channel_mode", "layer_display"):
                if isinstance(d.get(k), str):
                    setattr(self, k, d[k])
            for k in ("min_db", "max_db", "gamma", "overlay_opacity"):
                if isinstance(d.get(k), (int, float)):
                    setattr(self, k, float(d[k]))
            if isinstance(d.get("show_areas"), bool):
                self.show_areas = d["show_areas"]
            if isinstance(d.get("display_config"), dict):
                self.display_config = STFTConfig.from_dict(d["display_config"])
            if all(isinstance(d.get(k), (int, float)) for k in ("t0", "t1", "f_lo", "f_hi")):
                self.set_time_range(d["t0"], d["t1"], emit=False)
                self.set_freq_range(d["f_lo"], d["f_hi"], emit=False)
        except Exception:
            pass
        self.changed.emit("analysis")
        self.changed.emit("viewport")


class ToolState(QObject):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tool = "rect"
        self.selection_mode = "replace"
        self.channel_scope: tuple[int, ...] | None = None  # None = all channels linked
        # brush
        self.brush_size_mode = "screen"  # 'screen' (pixels, constant on screen) | 'spectral' (s x Hz, constant in audio)
        self.brush_size_px = 28.0
        self.brush_aspect = 1.0  # width/height on screen
        self.brush_width_s = 0.06
        self.brush_height_hz = 150.0
        self.brush_hardness = 0.5
        self.brush_square = False
        self.reduce_db = -12.0
        self.amplify_db = 6.0
        self.transfer_target: str | None = None
        self.transfer_amount = 1.0
        # gain module (Reduce selected sound)
        self.gain_db = -6.0
        # new-selection feather defaults
        self.feather_t = 0.0
        self.feather_f = 0.0
        # Selection panel expand / contract step
        self.expand_step_s = 0.010
        self.expand_step_hz = 50.0

    def set(self, **kw) -> None:
        for k, v in kw.items():
            if not hasattr(self, k):
                raise AttributeError(k)
            setattr(self, k, v)
        self.changed.emit()
