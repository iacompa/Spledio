"""How much RAM this machine has, and memory budgets sized to it.

Caches that only save time (display tiles, undo data kept in RAM rather than
on disk) are sized as a fraction of physical memory, within fixed bounds, so
an 8 GB Mac does not give them the same room as a 32 GB one. No Qt here.
"""

from __future__ import annotations

import ctypes
import os
import sys

MB = 2**20


def physical_memory() -> int | None:
    """Physical RAM in bytes, or None if it cannot be found out."""
    try:
        if sys.platform == "win32":
            class _MemStatus(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            st = _MemStatus()
            st.dwLength = ctypes.sizeof(st)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
                return int(st.ullTotalPhys)
            return None
        n = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        return int(n) if n > 0 else None
    except (AttributeError, OSError, ValueError):
        return None


def ram_budget(fraction: float, lo: int, hi: int, total: int | None = None) -> int:
    """``fraction`` of physical RAM, clamped to ``[lo, hi]`` bytes (``hi`` if the RAM is unknown)."""
    total = physical_memory() if total is None else total
    if not total:
        return int(hi)
    return int(min(hi, max(lo, total * fraction)))


def tile_cache_budget(total: int | None = None) -> int:
    """Spectrogram tile cache: 1/32 of RAM, 128–512 MB (8 GB → 256 MB, 16 GB or more → 512 MB).
    Recently replaced tiles may use another quarter of it."""
    return ram_budget(1 / 32, 128 * MB, 512 * MB, total)


def undo_ram_budget(total: int | None = None) -> int:
    """Undo data kept in RAM before older steps move to disk: 1/16 of RAM, 256–768 MB
    (8 GB → 512 MB, 12 GB or more → 768 MB)."""
    return ram_budget(1 / 16, 256 * MB, 768 * MB, total)


__all__ = ["physical_memory", "ram_budget", "tile_cache_budget", "undo_ram_budget", "MB"]
