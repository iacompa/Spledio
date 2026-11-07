"""Display-only spectrogram analysis and image rendering (no Qt imports).

``analysis``  tiled / cached / LOD display STFT (:class:`SpectrogramAnalyzer`)
``scales``    frequency scales, note names, ruler ticks
``colormaps`` 256-entry colour LUTs
``image``     dB -> RGBA colouring, selection overlay, grid -> view mapping
"""

from spledio.rendering.analysis import (
    DB_FLOOR,
    TILE_FRAMES,
    DisplayAnalysisSettings,
    SpectrogramAnalyzer,
    TileStatus,
    ViewData,
)
from spledio.rendering.colormaps import COLORBLIND_FRIENDLY, COLORMAP_NAMES, DEFAULT_COLORMAP, get_lut, get_lut_rgba
from spledio.rendering.image import colorize, map_grid_to_view, mask_overlay, nearest_cells
from spledio.rendering.scales import (
    FrequencyScale,
    Ticks,
    nice_freq_ticks,
    nice_time_ticks,
    note_name,
    note_ticks,
)

__all__ = [
    "DB_FLOOR",
    "TILE_FRAMES",
    "DisplayAnalysisSettings",
    "SpectrogramAnalyzer",
    "TileStatus",
    "ViewData",
    "COLORBLIND_FRIENDLY",
    "COLORMAP_NAMES",
    "DEFAULT_COLORMAP",
    "get_lut",
    "get_lut_rgba",
    "colorize",
    "map_grid_to_view",
    "mask_overlay",
    "nearest_cells",
    "FrequencyScale",
    "Ticks",
    "nice_freq_ticks",
    "nice_time_ticks",
    "note_name",
    "note_ticks",
]
