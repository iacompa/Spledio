"""Spectrogram colour maps as 256 x 3 ``uint8`` sRGB lookup tables.

Every map is built here from a handful of control points, interpolated in
CIELAB (D65) so steps are close to perceptually even, then converted back to
sRGB and clipped to gamut.

* ``ember`` and ``ice`` are Spledio's own designs. Their control points are
  placed along the ramp *by their CIE L\\** lightness, so lightness rises
  linearly from black to the brightest colour (perceptually ordered: louder is
  always lighter, also when printed in grey or seen with a colour-vision
  deficiency).
* ``grayscale`` is a plain linear sRGB ramp.
* ``viridis-like`` and ``cividis-like`` interpolate nine anchor colours
  (rounded to three decimals) approximating the published viridis
  (van der Walt & Smith, released CC0 / public domain with matplotlib) and
  cividis (Nuñez, Anderton & Renslow 2018, distributed with matplotlib under
  CC0) tables. Only these anchors are used, so the LUTs approximate – but are
  not byte-identical to – the originals. Both stay readable for the common
  colour-vision deficiencies (monotonic lightness, blue-yellow hue axis).

No LUT data is copied from any other program.
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np

__all__ = ["COLORMAP_NAMES", "COLORBLIND_FRIENDLY", "DEFAULT_COLORMAP", "get_lut", "get_lut_rgba", "lightness"]

# ---------------------------------------------------------------------------
# sRGB <-> CIELAB (D65)
# ---------------------------------------------------------------------------

_M_RGB_TO_XYZ = np.array(
    [
        [0.4124564, 0.3575761, 0.1804375],
        [0.2126729, 0.7151522, 0.0721750],
        [0.0193339, 0.1191920, 0.9503041],
    ]
)
_M_XYZ_TO_RGB = np.linalg.inv(_M_RGB_TO_XYZ)
_WHITE = np.array([0.95047, 1.0, 1.08883])
_D = 6.0 / 29.0


def _srgb_to_linear(c: np.ndarray) -> np.ndarray:
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _linear_to_srgb(c: np.ndarray) -> np.ndarray:
    c = np.clip(c, 0.0, 1.0)
    return np.where(c <= 0.0031308, 12.92 * c, 1.055 * np.power(c, 1.0 / 2.4) - 0.055)


def _srgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    xyz = _srgb_to_linear(np.asarray(rgb, dtype=np.float64)) @ _M_RGB_TO_XYZ.T
    t = xyz / _WHITE
    f = np.where(t > _D**3, np.cbrt(t), t / (3 * _D**2) + 4.0 / 29.0)
    L = 116.0 * f[..., 1] - 16.0
    a = 500.0 * (f[..., 0] - f[..., 1])
    b = 200.0 * (f[..., 1] - f[..., 2])
    return np.stack([L, a, b], axis=-1)


def _lab_to_srgb(lab: np.ndarray) -> np.ndarray:
    L, a, b = lab[..., 0], lab[..., 1], lab[..., 2]
    fy = (L + 16.0) / 116.0
    f = np.stack([fy + a / 500.0, fy, fy - b / 200.0], axis=-1)
    t = np.where(f > _D, f**3, 3 * _D**2 * (f - 4.0 / 29.0))
    lin = (t * _WHITE) @ _M_XYZ_TO_RGB.T
    return _linear_to_srgb(lin)


def lightness(rgb_u8: np.ndarray) -> np.ndarray:
    """CIE L* (0..100) of ``uint8`` sRGB colours (``(..., 3)``)."""
    return _srgb_to_lab(np.asarray(rgb_u8, dtype=np.float64) / 255.0)[..., 0]


# ---------------------------------------------------------------------------
# control points (sRGB, 0..1)
# ---------------------------------------------------------------------------

# Spledio originals. Positions are derived from L* (see _by_lightness).
_EMBER = [
    (0.000, 0.000, 0.000),  # black
    (0.130, 0.030, 0.250),  # deep purple
    (0.420, 0.060, 0.450),  # plum
    (0.800, 0.200, 0.300),  # rose red
    (0.960, 0.450, 0.100),  # orange
    (0.990, 0.720, 0.300),  # amber
    (1.000, 0.960, 0.780),  # pale yellow
]
_ICE = [
    (0.000, 0.000, 0.000),  # black
    (0.020, 0.050, 0.220),  # navy
    (0.050, 0.250, 0.650),  # blue
    (0.100, 0.550, 0.900),  # azure
    (0.400, 0.850, 0.970),  # cyan
    (1.000, 1.000, 1.000),  # white
]

# Anchor colours at ramp positions 0, 32, 64, ..., 224, 255 (of 255), rounded to
# three decimals, approximating the CC0 viridis table.
_VIRIDIS_ANCHORS = [
    (0.267, 0.005, 0.329),
    (0.283, 0.141, 0.458),
    (0.230, 0.322, 0.546),
    (0.173, 0.449, 0.558),
    (0.128, 0.567, 0.551),
    (0.135, 0.659, 0.518),
    (0.369, 0.789, 0.383),
    (0.637, 0.857, 0.217),
    (0.993, 0.906, 0.144),
]
# Same positions, approximating the CC0 cividis table (blue -> grey -> yellow).
_CIVIDIS_ANCHORS = [
    (0.000, 0.135, 0.305),
    (0.131, 0.231, 0.433),
    (0.261, 0.309, 0.429),
    (0.373, 0.395, 0.443),
    (0.487, 0.483, 0.471),
    (0.606, 0.573, 0.460),
    (0.733, 0.668, 0.428),
    (0.863, 0.768, 0.362),
    (0.996, 0.909, 0.218),
]
_ANCHOR_POS = np.array([0, 32, 64, 96, 128, 160, 192, 224, 255], dtype=np.float64) / 255.0


def _by_lightness(points) -> tuple[np.ndarray, np.ndarray]:
    lab = _srgb_to_lab(np.asarray(points, dtype=np.float64))
    L = lab[:, 0]
    if np.any(np.diff(L) <= 0):
        raise AssertionError("control points must have strictly increasing lightness")
    return (L - L[0]) / (L[-1] - L[0]), lab


def _build(pos: np.ndarray, lab: np.ndarray) -> np.ndarray:
    x = np.linspace(0.0, 1.0, 256)
    lab_i = np.stack([np.interp(x, pos, lab[:, i]) for i in range(3)], axis=-1)
    rgb = _lab_to_srgb(lab_i)
    return np.clip(np.rint(rgb * 255.0), 0, 255).astype(np.uint8)


def _make(name: str) -> np.ndarray:
    if name == "ember":
        lut = _build(*_by_lightness(_EMBER))
    elif name == "ice":
        lut = _build(*_by_lightness(_ICE))
    elif name == "grayscale":
        g = np.arange(256, dtype=np.uint8)
        lut = np.stack([g, g, g], axis=-1)
    elif name == "viridis-like":
        lut = _build(_ANCHOR_POS, _srgb_to_lab(np.asarray(_VIRIDIS_ANCHORS)))
    elif name == "cividis-like":
        lut = _build(_ANCHOR_POS, _srgb_to_lab(np.asarray(_CIVIDIS_ANCHORS)))
    else:
        raise KeyError(name)
    lut = np.ascontiguousarray(lut)
    lut.setflags(write=False)
    return lut


COLORMAP_NAMES: tuple[str, ...] = ("ember", "ice", "grayscale", "viridis-like", "cividis-like")
COLORBLIND_FRIENDLY: frozenset[str] = frozenset({"viridis-like", "cividis-like", "grayscale"})
DEFAULT_COLORMAP = "ember"


@lru_cache(maxsize=None)
def _lut_cached(name: str) -> np.ndarray:
    return _make(name)


def get_lut(name: str) -> np.ndarray:
    """256 x 3 ``uint8`` sRGB LUT (read-only, cached). Index 0 = quietest."""
    if name not in COLORMAP_NAMES:
        raise KeyError(f"Unknown colour map {name!r}; available: {', '.join(COLORMAP_NAMES)}")
    return _lut_cached(name)


@lru_cache(maxsize=None)
def _rgba_cached(name: str) -> np.ndarray:
    rgb = get_lut(name)
    out = np.empty((256, 4), dtype=np.uint8)
    out[:, :3] = rgb
    out[:, 3] = 255
    out.setflags(write=False)
    return out


def get_lut_rgba(name: str) -> np.ndarray:
    """256 x 4 ``uint8`` RGBA version of :func:`get_lut` (alpha 255)."""
    if name not in COLORMAP_NAMES:
        raise KeyError(f"Unknown colour map {name!r}; available: {', '.join(COLORMAP_NAMES)}")
    return _rgba_cached(name)
