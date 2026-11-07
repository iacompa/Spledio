"""Audio file probing and import (libsndfile via ``soundfile``).

Conventions (see ``docs/ARCHITECTURE.md``):

* Decoded audio is returned as ``numpy.ndarray`` with shape
  **(channels, frames)**, C-contiguous. ``soundfile`` works in
  (frames, channels); the transpose happens only inside this module.
* Import never downmixes, never resamples, never normalises and never writes
  to the source file. Files are opened with ``os.open(..., O_RDONLY)`` and the
  descriptor is handed to libsndfile, so the container is detected purely from
  the file *header* (libsndfile's file-name/extension fallbacks, e.g. treating
  an unrecognised ``.au`` file as headerless u-law, cannot trigger).
* Storage dtype: ``float32`` represents 8/16/24-bit integer PCM (and 20/24-bit
  ALAC, u-law, A-law) and 32-bit float exactly. 32-bit integer sources
  (``PCM_32``, ``ALAC_32``) and 64-bit float sources (``DOUBLE``) are stored as
  ``float64`` so that no sample value changes. libsndfile scales integer PCM
  by ``1 / 2**(bits-1)`` when decoding to float, which is exact in these dtypes.

Accepted inputs: the uncompressed / lossless containers in
:data:`SUPPORTED_IMPORT_CONTAINERS` with an encoding from
:data:`SUPPORTED_IMPORT_SUBTYPES`. Lossy or block-compressed formats (MP3,
Ogg Vorbis/Opus, ADPCM, GSM, ...) are rejected with a clear message: the core
importer does not handle them yet (ADPCM, for example, pads the final block,
so its decoded length differs from the source length).

Every failure a user can cause (missing file, folder, empty or corrupt file,
unsupported format, absurd header values, decode errors, out of memory) raises
:class:`AudioImportError` whose ``str()`` is a user-facing sentence and whose
``.path`` holds the offending path.
"""

from __future__ import annotations

import dataclasses
import hashlib
import operator
import os
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator, Mapping

import numpy as np
import soundfile as sf

__all__ = [
    "SUPPORTED_IMPORT_CONTAINERS",
    "SUPPORTED_IMPORT_SUBTYPES",
    "MIN_SAMPLE_RATE",
    "MAX_SAMPLE_RATE",
    "MAX_CHANNELS",
    "AudioImportError",
    "AudioFileInfo",
    "probe_audio",
    "storage_dtype_for",
    "load_audio",
    "read_block",
    "file_sha256",
]

_AVAILABLE_FORMATS = sf.available_formats()

#: libsndfile major formats (containers) accepted by the importer; only those
#: that this libsndfile build actually provides.
SUPPORTED_IMPORT_CONTAINERS: tuple[str, ...] = tuple(
    c for c in ("WAV", "WAVEX", "RF64", "W64", "FLAC", "AIFF", "CAF") if c in _AVAILABLE_FORMATS
)

_AVAILABLE_SUBTYPES = sf.available_subtypes()

#: Sample encodings accepted inside those containers: integer PCM, IEEE float,
#: companded 8-bit (u-law / A-law) and lossless ALAC. All decode to exact values.
SUPPORTED_IMPORT_SUBTYPES: tuple[str, ...] = tuple(
    s
    for s in (
        "PCM_S8",
        "PCM_U8",
        "PCM_16",
        "PCM_24",
        "PCM_32",
        "FLOAT",
        "DOUBLE",
        "ULAW",
        "ALAW",
        "ALAC_16",
        "ALAC_20",
        "ALAC_24",
        "ALAC_32",
    )
    if s in _AVAILABLE_SUBTYPES
)

# Lossy / compressed containers that libsndfile can decode but the core importer rejects.
_COMPRESSED_CONTAINERS = ("MP3", "OGG", "MPEG", "OPUS")

# Subtypes whose values do not fit float32 exactly.
_FLOAT64_SUBTYPES = frozenset({"PCM_32", "DOUBLE", "ALAC_32"})
_FLOAT_SUBTYPES = frozenset({"FLOAT", "DOUBLE"})

MIN_SAMPLE_RATE = 1000
MAX_SAMPLE_RATE = 768_000
MAX_CHANNELS = 64

_BLOCK_FRAMES = 1 << 14  # small blocks: a decode error loses at most one block of progress info

_READ_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_BINARY", 0)
    | getattr(os, "O_CLOEXEC", 0)
    # Opening a FIFO must not block; regular files ignore O_NONBLOCK.
    | getattr(os, "O_NONBLOCK", 0)
)


class AudioImportError(Exception):
    """A file could not be imported. ``str(e)`` is a user-facing message."""

    def __init__(self, message: str, path: str | None = None):
        super().__init__(message)
        self.message = message
        self.path = path


@dataclass(frozen=True)
class AudioFileInfo:
    """Header-derived description of an audio file (as libsndfile reports it).

    ``path`` is the absolute path (symlinks are *not* resolved). ``format`` and
    ``subtype`` are libsndfile's names (e.g. ``"FLAC"``, ``"PCM_24"``).
    """

    path: str
    format: str
    subtype: str
    sample_rate: int
    channels: int
    frames: int

    @property
    def duration_seconds(self) -> float:
        return self.frames / self.sample_rate if self.sample_rate > 0 else 0.0

    def to_dict(self) -> dict[str, Any]:
        """Plain JSON-serialisable dict (str / int values only)."""
        return {
            "path": self.path,
            "format": self.format,
            "subtype": self.subtype,
            "sample_rate": int(self.sample_rate),
            "channels": int(self.channels),
            "frames": int(self.frames),
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "AudioFileInfo":
        """Inverse of :meth:`to_dict`. Unknown keys are ignored (forward compatibility).

        Raises ``ValueError`` for missing keys or wrongly typed values.
        """
        if not isinstance(d, Mapping):
            raise ValueError("AudioFileInfo.from_dict expects a mapping")
        missing = [f.name for f in dataclasses.fields(cls) if f.name not in d]
        if missing:
            raise ValueError(f"AudioFileInfo dict is missing keys: {', '.join(missing)}")
        kwargs: dict[str, Any] = {}
        for key in ("path", "format", "subtype"):
            value = d[key]
            if not isinstance(value, str):
                raise ValueError(f"AudioFileInfo.{key} must be a string, got {type(value).__name__}")
            kwargs[key] = value
        for key in ("sample_rate", "channels", "frames"):
            value = d[key]
            if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
                raise ValueError(f"AudioFileInfo.{key} must be an integer, got {value!r}")
            if value < 0:
                raise ValueError(f"AudioFileInfo.{key} must not be negative, got {value!r}")
            kwargs[key] = int(value)
        return cls(**kwargs)


# --------------------------------------------------------------------------- helpers


def _normalise_path(path: Any) -> str:
    try:
        p = os.fspath(path)
    except TypeError:
        raise AudioImportError(f"Not a file path: {path!r}") from None
    if isinstance(p, bytes):
        p = os.fsdecode(p)
    if not p:
        raise AudioImportError("No file path given", p)
    return os.path.abspath(p)


def _sf_error_detail(exc: BaseException) -> str:
    detail = getattr(exc, "error_string", None) or str(exc)
    return str(detail).strip().rstrip(".")


def _check_float_dtype(dtype: Any) -> np.dtype:
    dt = np.dtype(dtype)
    if dt not in (np.dtype(np.float32), np.dtype(np.float64)):
        raise ValueError(f"audio can only be decoded to float32 or float64, not {dt}")
    return dt


def _validated_info(path: str, f: sf.SoundFile) -> AudioFileInfo:
    name = os.path.basename(path)
    fmt = str(f.format)
    subtype = str(f.subtype)
    if fmt in _COMPRESSED_CONTAINERS:
        raise AudioImportError(
            f"Cannot import '{name}': compressed format {fmt} is not supported by the core importer yet. "
            "Convert it to WAV, FLAC or AIFF first.",
            path,
        )
    if fmt not in SUPPORTED_IMPORT_CONTAINERS:
        desc = _AVAILABLE_FORMATS.get(fmt, fmt)
        raise AudioImportError(
            f"Cannot import '{name}': the {desc} container is not supported "
            f"(supported: {', '.join(SUPPORTED_IMPORT_CONTAINERS)}).",
            path,
        )
    if subtype not in SUPPORTED_IMPORT_SUBTYPES:
        desc = _AVAILABLE_SUBTYPES.get(subtype, subtype)
        raise AudioImportError(
            f"Cannot import '{name}': compressed encoding {subtype} ({desc}) inside a {fmt} file "
            "is not supported by the core importer yet. Convert it to uncompressed PCM or FLAC first.",
            path,
        )
    sr = f.samplerate
    if isinstance(sr, bool) or not isinstance(sr, (int, np.integer)):
        raise AudioImportError(f"Cannot import '{name}': the header has a non-integer sample rate ({sr!r}).", path)
    sr = int(sr)
    if sr <= 0:
        raise AudioImportError(f"Cannot import '{name}': the header declares a sample rate of {sr} Hz.", path)
    if sr < MIN_SAMPLE_RATE or sr > MAX_SAMPLE_RATE:
        raise AudioImportError(
            f"Cannot import '{name}': implausible sample rate {sr} Hz "
            f"(supported range {MIN_SAMPLE_RATE}–{MAX_SAMPLE_RATE} Hz).",
            path,
        )
    channels = int(f.channels)
    if channels <= 0:
        raise AudioImportError(f"Cannot import '{name}': the header declares {channels} channels.", path)
    if channels > MAX_CHANNELS:
        raise AudioImportError(
            f"Cannot import '{name}': it has {channels} channels; at most {MAX_CHANNELS} are supported.",
            path,
        )
    frames = int(f.frames)
    if frames <= 0:
        raise AudioImportError(f"Cannot import '{name}': the file contains no audio frames.", path)
    return AudioFileInfo(
        path=path, format=fmt, subtype=subtype, sample_rate=sr, channels=channels, frames=frames
    )


@contextmanager
def _open_validated(path: Any) -> Iterator[tuple[sf.SoundFile, AudioFileInfo]]:
    """Open ``path`` read-only, detect the format from its header and validate it."""
    p = _normalise_path(path)
    name = os.path.basename(p) or p
    try:
        st = os.stat(p)
    except FileNotFoundError:
        raise AudioImportError(f"Audio file not found: {p}", p) from None
    except PermissionError:
        raise AudioImportError(f"Permission denied reading '{name}'.", p) from None
    except OSError as e:
        raise AudioImportError(f"Cannot access '{name}': {e.strerror or e}.", p) from e
    if stat.S_ISDIR(st.st_mode):
        raise AudioImportError(f"'{name}' is a folder, not an audio file.", p)
    if not stat.S_ISREG(st.st_mode):
        raise AudioImportError(f"'{name}' is not a regular file.", p)
    try:
        fd = os.open(p, _READ_FLAGS)
    except PermissionError:
        raise AudioImportError(f"Permission denied reading '{name}'.", p) from None
    except OSError as e:
        raise AudioImportError(f"Cannot open '{name}': {e.strerror or e}.", p) from e
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise AudioImportError(f"'{name}' is not a regular file.", p)
        if st.st_size == 0:
            raise AudioImportError(f"Cannot import '{name}': the file is empty (0 bytes).", p)
        try:
            f = sf.SoundFile(fd, mode="r", closefd=False)
        except (sf.LibsndfileError, RuntimeError, OSError, TypeError, ValueError) as e:
            raise AudioImportError(
                f"Cannot import '{name}': it is not a recognised audio file or its header is damaged "
                f"({_sf_error_detail(e)}).",
                p,
            ) from e
        try:
            info = _validated_info(p, f)
            yield f, info
        finally:
            f.close()
    finally:
        os.close(fd)


# --------------------------------------------------------------------------- public API


def probe_audio(path: Any) -> AudioFileInfo:
    """Read and validate the header of an audio file without decoding samples.

    Detection is header based: a FLAC stream saved as ``.wav`` is reported as
    ``FLAC``; a text file renamed ``.wav`` raises :class:`AudioImportError`.
    """
    with _open_validated(path) as (_f, info):
        return info


def storage_dtype_for(info: AudioFileInfo | str) -> np.dtype:
    """Layer storage dtype that holds every sample of ``info.subtype`` exactly.

    ``float64`` for 32-bit integer (``PCM_32``, ``ALAC_32``) and 64-bit float
    (``DOUBLE``) sources, ``float32`` otherwise. Also accepts a subtype string.
    """
    subtype = info if isinstance(info, str) else info.subtype
    return np.dtype(np.float64) if subtype.upper() in _FLOAT64_SUBTYPES else np.dtype(np.float32)


def load_audio(path: Any, dtype: Any = None) -> tuple[np.ndarray, AudioFileInfo]:
    """Decode a whole file to a C-contiguous ``(channels, frames)`` array.

    ``dtype`` defaults to :func:`storage_dtype_for`; only float32/float64 are
    accepted (``ValueError`` otherwise). Samples are returned exactly as
    libsndfile decodes them (no downmix, resample, normalisation or clipping;
    float files may contain values beyond ±1.0).

    If libsndfile delivers fewer frames than the header announced (e.g. a
    truncated file), the decoded frames are returned and the returned info's
    ``frames`` is the number actually decoded; compare with
    :func:`probe_audio` to detect this. Float files containing NaN or infinity
    are rejected, because they cannot be analysed or edited meaningfully.
    """
    with _open_validated(path) as (f, info):
        dt = storage_dtype_for(info) if dtype is None else _check_float_dtype(dtype)
        name = os.path.basename(info.path)
        try:
            out = np.empty((info.channels, info.frames), dtype=dt)
            buf = np.empty((min(_BLOCK_FRAMES, info.frames), info.channels), dtype=dt)
        except (MemoryError, ValueError, OverflowError):
            raise AudioImportError(
                f"Cannot import '{name}': not enough memory to decode {info.frames} frames "
                f"x {info.channels} channels.",
                info.path,
            ) from None
        check_finite = info.subtype in _FLOAT_SUBTYPES
        non_finite = 0
        filled = 0
        try:
            while filled < info.frames:
                n = min(buf.shape[0], info.frames - filled)
                chunk = f.read(out=buf[:n])
                got = chunk.shape[0]
                if got == 0:
                    break
                if check_finite:
                    non_finite += int(chunk.size - np.count_nonzero(np.isfinite(chunk)))
                out[:, filled : filled + got] = chunk.T
                filled += got
                if got < n:
                    break
        except (sf.LibsndfileError, RuntimeError, OSError) as e:
            raise AudioImportError(
                f"Cannot import '{name}': decoding failed after {filled} of {info.frames} frames "
                f"({_sf_error_detail(e)}).",
                info.path,
            ) from e
        if non_finite:
            raise AudioImportError(
                f"Cannot import '{name}': it contains {non_finite} non-finite samples (NaN or infinity).",
                info.path,
            )
        if filled == 0:
            raise AudioImportError(f"Cannot import '{name}': no audio frames could be decoded.", info.path)
        if filled < info.frames:
            out = np.ascontiguousarray(out[:, :filled])
            info = dataclasses.replace(info, frames=filled)
        return out, info


def read_block(path: Any, start: int, frames: int, dtype: Any = np.float32) -> np.ndarray:
    """Decode ``frames`` frames starting at frame ``start`` as ``(channels, n)``.

    For long files (display tiles, streaming). Opens and closes the file on
    each call. ``n`` is shorter than ``frames`` at the end of the file and 0
    when ``start`` is at or beyond the end. ``start``/``frames`` must be
    non-negative integers (``ValueError`` otherwise). Unlike
    :func:`load_audio`, the block is not checked for NaN/infinity.
    """
    start = operator.index(start)
    frames = operator.index(frames)
    if start < 0 or frames < 0:
        raise ValueError(f"start and frames must be non-negative (got start={start}, frames={frames})")
    dt = _check_float_dtype(dtype)
    with _open_validated(path) as (f, info):
        if frames == 0 or start >= info.frames:
            return np.zeros((info.channels, 0), dtype=dt)
        n = min(frames, info.frames - start)
        try:
            f.seek(start)
            block = f.read(n, dtype=dt, always_2d=True)
        except (sf.LibsndfileError, RuntimeError, OSError) as e:
            raise AudioImportError(
                f"Cannot read '{os.path.basename(info.path)}' at frame {start} ({_sf_error_detail(e)}).",
                info.path,
            ) from e
        return np.ascontiguousarray(block.T)


def file_sha256(path: Any, chunk: int = 1 << 20) -> str:
    """Hex SHA-256 of the file's bytes (streamed in ``chunk``-byte reads).

    Raises :class:`AudioImportError` if the file cannot be read.
    """
    chunk = operator.index(chunk)
    if chunk <= 0:
        raise ValueError("chunk must be positive")
    p = _normalise_path(path)
    h = hashlib.sha256()
    try:
        with open(p, "rb") as fh:
            while True:
                data = fh.read(chunk)
                if not data:
                    break
                h.update(data)
    except FileNotFoundError:
        raise AudioImportError(f"Audio file not found: {p}", p) from None
    except IsADirectoryError:
        raise AudioImportError(f"'{os.path.basename(p)}' is a folder, not an audio file.", p) from None
    except OSError as e:
        raise AudioImportError(f"Cannot read '{os.path.basename(p)}': {e.strerror or e}.", p) from e
    return h.hexdigest()
