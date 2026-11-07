"""Turn display analysis into RGBA pixels (no Qt: plain ``uint8 (H, W, 4)`` arrays).

All outputs are C-contiguous ``uint8`` RGBA with **straight (non-premultiplied)
alpha**, matching ``QImage.Format_RGBA8888`` (bytes R, G, B, A per pixel,
``bytesPerLine = 4 * width``). Colouring is pure display: it reads cached dB
values and never touches audio or analysis, so changing colour map, dB range
or gamma costs only this function.
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np

from spledio.dsp.stft import STFTConfig
from spledio.rendering.analysis import TileStatus
from spledio.rendering.colormaps import get_lut
from spledio.rendering.scales import FrequencyScale, column_centre_times, nearest_index, row_centres_hz

__all__ = ["colorize", "hatch_colour", "mask_overlay", "nearest_cells", "map_grid_to_view"]

_HATCH_PERIOD = 8  # pixels per diagonal stripe pair


def _rgba(c) -> np.ndarray:
    c = tuple(int(v) for v in c)
    if len(c) == 3:
        c = c + (255,)
    if len(c) != 4 or any(not 0 <= v <= 255 for v in c):
        raise ValueError(f"colour must be 3 or 4 values in 0..255, got {c}")
    return np.array(c, dtype=np.uint8)


def _u32(rgba: np.ndarray) -> np.uint32:
    return np.ascontiguousarray(rgba, dtype=np.uint8).view(np.uint32)[0]


def hatch_colour(rgba) -> tuple[int, int, int, int]:
    """The second stripe colour of a hatch: ``rgba`` blended 35 % toward white."""
    c = _rgba(rgba).astype(np.float64)
    rgb = np.rint(c[:3] + (255.0 - c[:3]) * 0.35).astype(int)
    return int(rgb[0]), int(rgb[1]), int(rgb[2]), int(c[3])


@lru_cache(maxsize=64)
def _gamma_table(gamma: float) -> np.ndarray:
    """``uint16`` quantised level (0..65535) -> LUT index ``round(255 * q**gamma)``."""
    q = np.arange(65536, dtype=np.float64) / 65535.0
    t = np.floor(np.power(q, gamma) * 255.0 + 0.5).astype(np.uint8)
    t.setflags(write=False)
    return t


def _lut_rgba(lut) -> np.ndarray:
    if isinstance(lut, str):
        lut = get_lut(lut)
    lut = np.asarray(lut)
    if lut.dtype != np.uint8 or lut.ndim != 2 or lut.shape[0] != 256 or lut.shape[1] not in (3, 4):
        raise ValueError("lut must be a (256, 3) or (256, 4) uint8 array or a colour-map name")
    out = np.empty((256, 4), dtype=np.uint8)
    out[:, :3] = lut[:, :3]
    out[:, 3] = lut[:, 3] if lut.shape[1] == 4 else 255
    return out


def colorize(
    db,
    status,
    min_db,
    max_db,
    gamma,
    lut,
    pending_rgba=(40, 40, 48, 255),
    failed_rgba=(120, 20, 20, 255),
    outside_rgba=(16, 16, 20, 255),
    *,
    hatch_origin: tuple[int, int] = (0, 0),
) -> np.ndarray:
    """Colour a dB raster. Returns ``uint8 (H, W, 4)`` straight-alpha RGBA.

    ``x = clip((db - min_db) / (max_db - min_db), 0, 1) ** gamma`` indexes the
    256-entry ``lut`` (``gamma`` > 0; ``gamma < 1`` brightens quiet detail,
    ``gamma > 1`` darkens it; for ``gamma != 1`` ``x`` is quantised to 16 bits
    first, which changes an index by at most one step). Pixels whose ``status`` is not READY ignore ``db``
    entirely: PENDING pixels show a diagonal hatch of ``pending_rgba`` and
    :func:`hatch_colour` of it, FAILED pixels the same hatch in ``failed_rgba``,
    OUTSIDE pixels are solid ``outside_rgba``. ``hatch_origin`` (x, y) shifts the
    stripe phase so the pattern can stay fixed to the content while panning.
    """
    db = np.ascontiguousarray(db, dtype=np.float32)
    status = np.ascontiguousarray(status, dtype=np.uint8)
    if db.ndim != 2 or db.shape != status.shape:
        raise ValueError("db and status must be 2-D arrays of the same shape")
    min_db, max_db, gamma = float(min_db), float(max_db), float(gamma)
    if not max_db > min_db:
        raise ValueError("max_db must be greater than min_db")
    if not gamma > 0 or not np.isfinite(gamma):
        raise ValueError("gamma must be > 0")
    lut32 = _lut_rgba(lut).view(np.uint32).reshape(256)

    x = np.subtract(db, np.float32(min_db), order="C")
    x *= np.float32(1.0 / (max_db - min_db))
    np.nan_to_num(x, copy=False, nan=0.0, posinf=1.0, neginf=0.0)
    np.clip(x, 0.0, 1.0, out=x)
    if gamma == 1.0:
        x *= np.float32(255.0)
        x += np.float32(0.5)
        out = lut32[x.astype(np.uint8)]  # (H, W) uint32
    else:
        # gamma through a 65536-step table (np.power per pixel is ~2x slower);
        # quantisation error is at most one LUT step in the darkest region
        x *= np.float32(65535.0)
        x += np.float32(0.5)
        out = lut32[_gamma_table(gamma)][x.astype(np.uint16)]

    if status.any():
        H, W = status.shape
        stripe = None
        for code, colour, hatched in (
            (TileStatus.PENDING, pending_rgba, True),
            (TileStatus.FAILED, failed_rgba, True),
            (TileStatus.OUTSIDE, outside_rgba, False),
        ):
            sel = status == code
            if not sel.any():
                continue
            base = _u32(_rgba(colour))
            if not hatched:
                out[sel] = base
                continue
            if stripe is None:
                yy = np.arange(H)[:, None] + int(hatch_origin[1])
                xx = np.arange(W)[None, :] + int(hatch_origin[0])
                stripe = ((xx + yy) % _HATCH_PERIOD) < (_HATCH_PERIOD // 2)
            alt = _u32(np.array(hatch_colour(colour), dtype=np.uint8))
            out[sel & stripe] = alt
            out[sel & ~stripe] = base
    return out.view(np.uint8).reshape(out.shape[0], out.shape[1], 4)


def mask_overlay(mask_values, rgba=(80, 200, 255), opacity=0.35, *, premultiplied: bool = False) -> np.ndarray:
    """Selection overlay: ``uint8 (H, W, 4)``.

    Colour is constant ``rgba[:3]``; alpha is ``255 * opacity * clip(mask, 0, 1)``
    (times ``rgba[3]/255`` if a 4th value is given). Default output is **straight
    alpha** (``QImage.Format_RGBA8888``); ``premultiplied=True`` multiplies the
    colour by alpha (``Format_RGBA8888_Premultiplied``).
    """
    m = np.asarray(mask_values, dtype=np.float32)
    if m.ndim != 2:
        raise ValueError("mask_values must be 2-D")
    c = _rgba(rgba)
    opacity = float(opacity)
    if not 0.0 <= opacity <= 1.0:
        raise ValueError("opacity must be in [0, 1]")
    a = np.nan_to_num(m, nan=0.0)
    np.clip(a, 0.0, 1.0, out=a)
    a *= np.float32(opacity * c[3])
    alpha = np.rint(a).astype(np.uint8)
    out = np.empty(m.shape + (4,), dtype=np.uint8)
    if premultiplied:
        for i in range(3):
            out[..., i] = np.rint(alpha.astype(np.float32) * (c[i] / 255.0)).astype(np.uint8)
    else:
        out[..., :3] = c[:3]
    out[..., 3] = alpha
    return out


def nearest_cells(t0, t1, width, scale: FrequencyScale, height, sample_rate, cfg: STFTConfig):
    """Processing-grid cell under each pixel centre.

    Returns ``(k_per_column (W,), b_per_row (H,))`` int64, with rows top-first
    (row 0 = highest frequency), using ``k = round(t*sr/hop)`` and
    ``b = round(f*n_fft/sr)`` (round half up). The analysis view uses the same
    column rule, so at stride 1 ``ViewData.frame_index`` equals ``k_per_column``
    for in-file columns when the display and edit configs match.
    """
    sr = float(sample_rate)
    tc = column_centre_times(t0, t1, int(width))
    fc = row_centres_hz(scale, int(height))[::-1]
    k = nearest_index(tc * (sr / cfg.hop))
    b = nearest_index(fc * (cfg.n_fft / sr))
    return k, b


def map_grid_to_view(mask, k0, b0, t0, t1, width, scale: FrequencyScale, height, sample_rate, cfg: STFTConfig):
    """Sample a processing-grid raster at the view's pixels.

    ``mask`` has shape ``(n_frames, n_bins_subset)``: ``mask[i, j]`` belongs to
    frame ``k0 + i`` and bin ``b0 + j`` of ``cfg`` (the same (frames, bins)
    orientation as :func:`spledio.dsp.stft.stft`). Each pixel takes the value of
    its nearest cell (:func:`nearest_cells`), or 0 if that cell is outside the
    raster – so the overlay lights exactly the pixels whose nearest cell is
    selected, i.e. exactly the cells the DSP will process. Returns ``float32
    (height, width)``, row 0 = top. (When zoomed far out a cell narrower than a
    pixel may be skipped by every pixel centre; that is the price of exactness.)
    """
    m = np.asarray(mask)
    if m.ndim != 2:
        raise ValueError("mask must be 2-D (frames, bins)")
    k, b = nearest_cells(t0, t1, width, scale, height, sample_rate, cfg)
    ki = k - int(k0)
    bi = b - int(b0)
    kin = (ki >= 0) & (ki < m.shape[0])
    bin_ = (bi >= 0) & (bi < m.shape[1])
    out = np.zeros((int(height), int(width)), dtype=np.float32)
    if kin.any() and bin_.any():
        cols = np.flatnonzero(kin)
        rows = np.flatnonzero(bin_)
        out[np.ix_(rows, cols)] = m[np.ix_(ki[cols], bi[rows])].T.astype(np.float32)
    return out
