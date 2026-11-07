"""Versioned ``.spledio`` project archive (original format, see docs/PROJECT_FORMAT.md).

A project file is a ZIP container (entries STORED, not compressed) holding:

* ``manifest.json`` – schema version, project/asset/layer metadata, selection,
  named selections, markers, view state, processing config, operation log.
* ``layers/<layer-id>/index.npy`` + ``blocks.npy`` – the samples of every
  8192-frame block in which the layer differs from its *base* (the decoded
  source asset, or silence). Unedited layers store nothing.
* ``assets/<asset-id>.<ext>`` – byte-exact copies of the source files, only
  in *portable* projects. Otherwise assets are referenced by relative and
  absolute path plus SHA-256.

Loading never unpickles (``allow_pickle=False``), never extracts member names
to disk, rejects unexpected/oversized members, validates every manifest field
and verifies asset checksums.
"""

from __future__ import annotations

import io
import json
import math
import os
import re
import shutil
import tempfile
import time
import zipfile

import numpy as np

from spledio import __version__
from spledio.branding import APP_NAME, PROJECT_FORMAT_ID
from spledio.domain.project import Asset, Layer, Marker, Project
from spledio.dsp.stft import STFTConfig
from spledio.selections.selection import Selection

SCHEMA_VERSION = 1
BLOCK = 8192
MAX_MANIFEST_BYTES = 32 * 2**20
MAX_CHANNELS = 64
MAX_FRAMES = 2**36
_ID = r"[a-z]+-[0-9a-f]{12}"
_MEMBER_RE = re.compile(
    rf"^(manifest\.json|assets/{_ID}\.[A-Za-z0-9]{{1,6}}|layers/{_ID}/(index|blocks)\.npy|areas/{_ID}\.npy)$"
)
_ID_RE = re.compile(rf"^{_ID}$")


class ProjectFormatError(Exception):
    """The file is not a valid project of a supported version."""


class MissingMediaError(Exception):
    def __init__(self, missing: list[dict]):
        self.missing = missing
        names = ", ".join(os.path.basename(m.get("path", "?")) for m in missing)
        super().__init__(f"Source audio not found: {names}. Locate the file(s) to relink.")


class AssetMismatchError(Exception):
    def __init__(self, asset_id: str, path: str):
        self.asset_id, self.path = asset_id, path
        super().__init__(f"{os.path.basename(path)} does not match the checksum of the original source")


# ---------------------------------------------------------------- helpers
def _npy_bytes(arr: np.ndarray) -> bytes:
    buf = io.BytesIO()
    np.save(buf, np.ascontiguousarray(arr), allow_pickle=False)
    return buf.getvalue()


def _base_audio(project: Project, layer_base: dict, asset_audio: dict[str, np.ndarray]) -> np.ndarray | None:
    if layer_base.get("type") == "asset":
        return asset_audio.get(layer_base.get("asset_id"))
    return None  # silence / generated -> zeros


def _changed_blocks(audio: np.ndarray, base: np.ndarray | None) -> np.ndarray:
    n = audio.shape[1]
    nb = -(-n // BLOCK)
    changed = []
    for b in range(nb):
        a, e = b * BLOCK, min(n, (b + 1) * BLOCK)
        seg = audio[:, a:e]
        if base is None:
            if np.any(seg != 0):
                changed.append(b)
        elif not np.array_equal(seg, base[:, a:e]):
            changed.append(b)
    return np.asarray(changed, dtype=np.int64)


def _decode_asset_file(path: str, dtype) -> np.ndarray:
    from spledio.audio.io import load_audio

    audio, _info = load_audio(path, dtype=dtype)
    return audio


# ------------------------------------------------------------------- save
def save_project(session, path: str, portable: bool = False) -> None:
    """Write the project atomically (temp file in the same folder + rename)."""
    from spledio.audio.io import file_sha256

    proj: Project = session.project
    path = os.path.abspath(path)
    proj_dir = os.path.dirname(path)
    dtype = np.dtype(proj.storage_dtype)

    asset_audio: dict[str, np.ndarray] = {}
    assets_json = []
    for a in proj.assets.values():
        rel = None
        try:
            rel = os.path.relpath(a.path, proj_dir)
        except ValueError:  # different drive on Windows
            rel = None
        entry = a.to_dict()
        entry["relative_path"] = rel
        entry["embedded_member"] = None
        assets_json.append(entry)
        needs_base = any(l.base.get("asset_id") == a.id for l in proj.layers)
        if needs_base:
            src = a.readable_path if os.path.exists(a.readable_path) else None
            if src is not None:
                if file_sha256(src) != a.sha256:
                    if portable:  # would embed a file that is not the project's source
                        raise AssetMismatchError(a.id, src)
                    # changed on disk since it was opened (or opened with "use it anyway"): the
                    # layers are in memory, so store them in full, like a missing source
                    continue
                asset_audio[a.id] = _decode_asset_file(src, dtype)

    fd, tmp = tempfile.mkstemp(prefix=".spledio-", suffix=".partial", dir=proj_dir)
    os.close(fd)
    try:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
            layers_json = []
            for layer in proj.layers:
                base_kind = layer.base.get("type")
                base = None
                if base_kind == "asset":
                    base = asset_audio.get(layer.base.get("asset_id"))
                    if base is None:
                        # source unavailable: store everything relative to silence
                        base_kind = "silence"
                idx = _changed_blocks(layer.audio, base)
                lj = {
                    "id": layer.id,
                    **{k: getattr(layer, k) for k in Layer.PROPS},
                    "base": layer.base if base_kind == layer.base.get("type") else {"type": "silence"},
                    "provenance": layer.provenance,
                    "data": None,
                }
                if len(idx):
                    blocks = np.zeros((len(idx), proj.channels, BLOCK), dtype=dtype)
                    for i, b in enumerate(idx):
                        a0, e0 = b * BLOCK, min(proj.n_frames, (b + 1) * BLOCK)
                        blocks[i, :, : e0 - a0] = layer.audio[:, a0:e0]
                    im, bm = f"layers/{layer.id}/index.npy", f"layers/{layer.id}/blocks.npy"
                    zf.writestr(im, _npy_bytes(idx))
                    zf.writestr(bm, _npy_bytes(blocks))
                    lj["data"] = {"kind": "blocks", "block_size": BLOCK, "n_blocks": int(len(idx)),
                                  "index_member": im, "blocks_member": bm}
                layers_json.append(lj)
            areas_json = []
            for a in session.visible_areas():
                member = f"areas/{a.id}.npy"
                zf.writestr(member, _npy_bytes(session.history.store.get(a.component)))
                areas_json.append({**a.to_dict(), "component_member": member})
            if portable:
                for entry in assets_json:
                    ext = os.path.splitext(entry["path"])[1].lstrip(".")[:6] or "bin"
                    ext = re.sub(r"[^A-Za-z0-9]", "", ext) or "bin"
                    member = f"assets/{entry['id']}.{ext}"
                    readable = proj.assets[entry["id"]].readable_path
                    if os.path.exists(readable):
                        zf.write(readable, member)
                    else:
                        raise MissingMediaError([entry])
                    entry["embedded_member"] = member
            manifest = {
                "format": PROJECT_FORMAT_ID,
                "schema_version": SCHEMA_VERSION,
                "app": {"name": APP_NAME, "version": __version__},
                "saved_at": time.time(),
                "portable": bool(portable),
                "project": {
                    "id": proj.id,
                    "name": proj.name,
                    "sample_rate": proj.sample_rate,
                    "n_frames": proj.n_frames,
                    "channels": proj.channels,
                    "storage_dtype": proj.storage_dtype,
                    "created": proj.created,
                    "processing_config": proj.processing_config.to_dict(),
                },
                "assets": assets_json,
                "layers": layers_json,
                "active_layer_id": session.active_layer_id,
                "selection": session.selection.to_dict() if session.selection else None,
                "named_selections": {k: v.to_dict() for k, v in session.named_selections.items()},
                "areas": areas_json,
                "markers": [m.to_dict() for m in proj.markers],
                "view": session.view_state,
                "operation_log": session.operation_log[-5000:],
                "history": {"saved": False, "undo_labels": session.history.labels()[0][-200:]},
                "models_used": [],
            }
            zf.writestr("manifest.json", json.dumps(manifest, indent=1, default=_json_default))
        with open(tmp, "rb") as f:
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _json_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, float) and math.isinf(o):
        return "inf" if o > 0 else "-inf"
    raise TypeError(type(o))


# ------------------------------------------------------------------- load
def _req(d: dict, key: str, typ, where: str):
    if key not in d:
        raise ProjectFormatError(f"{where}: missing '{key}'")
    v = d[key]
    if typ is float and isinstance(v, int) and not isinstance(v, bool):
        v = float(v)
    if not isinstance(v, typ) or (typ is int and isinstance(v, bool)):
        raise ProjectFormatError(f"{where}: '{key}' has wrong type")
    return v


def _load_areas(zf: zipfile.ZipFile, items, s, dtype) -> None:
    """Adjusted areas: metadata + their stored component (see engine/areas.py)."""
    from spledio.engine.areas import AdjustArea

    if not isinstance(items, list):
        raise ProjectFormatError("bad adjusted-area list")
    proj = s.project
    out = []
    for it in items[:10000]:
        try:
            aid, lid = str(it["id"]), str(it["layer_id"])
            if not _ID_RE.match(aid) or not proj.has_layer(lid):
                raise ProjectFormatError("adjusted area refers to an unknown layer")
            start, length = int(it["start"]), int(it["length"])
            chans = tuple(int(c) for c in it["channels"])
            g = it["gain_db"]
            g = -math.inf if g == "-inf" else float(g)
            if not (g == -math.inf or (math.isfinite(g) and -200 <= g <= 60)):
                raise ProjectFormatError("adjusted area gain out of range")
            if not (0 <= start and 0 < length and start + length <= proj.n_frames):
                raise ProjectFormatError("adjusted area span out of range")
            if not chans or any(not (0 <= c < proj.channels) for c in chans):
                raise ProjectFormatError("adjusted area channels out of range")
            member = it.get("component_member")
            if member != f"areas/{aid}.npy":
                raise ProjectFormatError("adjusted area member mismatch")
            comp = _read_npy(zf, member, proj.channels * length * 8 + 4096)
            if comp.shape != (proj.channels, length) or comp.dtype not in (np.dtype(np.float32), np.dtype(np.float64)):
                raise ProjectFormatError("adjusted area component has unexpected shape/dtype")
            if not np.all(np.isfinite(comp)):
                raise ProjectFormatError("adjusted area component contains non-finite samples")
            sel = Selection.from_dict(it["selection"])
            out.append(AdjustArea(aid, int(it.get("number", len(out) + 1)), lid, sel, g, start, length, chans,
                                  s.history.store.put(comp), float(it.get("created", 0.0))))
        except ProjectFormatError:
            raise
        except (KeyError, ValueError, TypeError) as e:
            raise ProjectFormatError(f"bad adjusted-area data: {e}") from e
    s.areas = tuple(out)


def _read_npy(zf: zipfile.ZipFile, member: str, max_bytes: int) -> np.ndarray:
    info = zf.getinfo(member)
    if info.file_size > max_bytes:
        raise ProjectFormatError(f"{member} is larger than expected")
    with zf.open(member) as f:
        data = f.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ProjectFormatError(f"{member} is larger than expected")
    try:
        return np.load(io.BytesIO(data), allow_pickle=False)
    except Exception as e:  # malformed npy
        raise ProjectFormatError(f"{member} is not a valid array: {e}") from e


def read_manifest(path: str) -> dict:
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as e:
        raise ProjectFormatError(f"Not a {APP_NAME} project archive: {e}") from e
    with zf:
        return _manifest_from(zf)


def _manifest_from(zf: zipfile.ZipFile) -> dict:
    names = zf.namelist()
    for n in names:
        if not _MEMBER_RE.match(n):
            raise ProjectFormatError(f"Unexpected entry in project archive: {n!r}")
    if "manifest.json" not in names:
        raise ProjectFormatError("manifest.json missing")
    info = zf.getinfo("manifest.json")
    if info.file_size > MAX_MANIFEST_BYTES:
        raise ProjectFormatError("manifest too large")
    try:
        m = json.loads(zf.read("manifest.json").decode("utf-8"))
    except Exception as e:
        raise ProjectFormatError(f"manifest.json is not valid JSON: {e}") from e
    if not isinstance(m, dict) or m.get("format") != PROJECT_FORMAT_ID:
        raise ProjectFormatError("Not a project manifest")
    ver = m.get("schema_version")
    if not isinstance(ver, int):
        raise ProjectFormatError("schema_version missing")
    if ver > SCHEMA_VERSION:
        raise ProjectFormatError(
            f"This project was saved by a newer version (schema {ver}); this app reads schema ≤ {SCHEMA_VERSION}"
        )
    return m


def load_project(path: str, relink: dict[str, str] | None = None, allow_checksum_mismatch: bool = False):
    from spledio.audio.io import file_sha256
    from spledio.engine.session import EditorSession

    relink = relink or {}
    path = os.path.abspath(path)
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as e:
        raise ProjectFormatError(f"Not a {APP_NAME} project archive: {e}") from e
    tmpdir = tempfile.mkdtemp(prefix="spledio-assets-")  # kept for the session (embedded assets)
    ok = False
    try:
        with zf:
            m = _manifest_from(zf)
            pj = _req(m, "project", dict, "manifest")
            sr = _req(pj, "sample_rate", int, "project")
            n = _req(pj, "n_frames", int, "project")
            ch = _req(pj, "channels", int, "project")
            dts = _req(pj, "storage_dtype", str, "project")
            if not (1000 <= sr <= 768000) or not (0 < n <= MAX_FRAMES) or not (1 <= ch <= MAX_CHANNELS):
                raise ProjectFormatError("project dimensions out of range")
            if dts not in ("float32", "float64"):
                raise ProjectFormatError("unsupported storage dtype")
            dtype = np.dtype(dts)
            try:
                cfg = STFTConfig.from_dict(_req(pj, "processing_config", dict, "project"))
            except Exception as e:
                raise ProjectFormatError(f"bad processing config: {e}") from e
            proj = Project(sample_rate=sr, n_frames=n, channels=ch, storage_dtype=dts,
                           id=str(pj.get("id", "proj-000000000000")), name=str(pj.get("name", "Untitled")),
                           processing_config=cfg, created=float(pj.get("created", time.time())))

            # assets
            asset_audio: dict[str, np.ndarray] = {}
            missing = []
            assets_list = _req(m, "assets", list, "manifest")
            layers_list = _req(m, "layers", list, "manifest")
            needed = {l.get("base", {}).get("asset_id") for l in layers_list if isinstance(l, dict)
                      and isinstance(l.get("base"), dict) and l["base"].get("type") == "asset"}
            for a in assets_list:
                if not isinstance(a, dict):
                    raise ProjectFormatError("asset entry is not an object")
                aid = _req(a, "id", str, "asset")
                if not _ID_RE.match(aid):
                    raise ProjectFormatError("bad asset id")
                asset = Asset(id=aid, path=_req(a, "path", str, "asset"), sha256=_req(a, "sha256", str, "asset"),
                              format=str(a.get("format", "")), subtype=str(a.get("subtype", "")),
                              sample_rate=_req(a, "sample_rate", int, "asset"), channels=_req(a, "channels", int, "asset"),
                              frames=_req(a, "frames", int, "asset"), embedded_member=a.get("embedded_member"))
                proj.assets[aid] = asset
                if aid not in needed:
                    continue
                src = None
                member = a.get("embedded_member")
                if aid in relink:
                    src = relink[aid]
                elif isinstance(member, str) and member in zf.namelist():
                    src = os.path.join(tmpdir, f"{aid}.{member.rsplit('.', 1)[-1]}")
                    with zf.open(member) as fin, open(src, "wb") as fout:
                        shutil.copyfileobj(fin, fout)
                else:
                    rel = a.get("relative_path")
                    cands = []
                    if isinstance(rel, str):
                        cands.append(os.path.normpath(os.path.join(os.path.dirname(path), rel)))
                    cands.append(asset.path)
                    src = next((c for c in cands if os.path.isfile(c)), None)
                if src is None or not os.path.isfile(src):
                    missing.append(a)
                    continue
                if file_sha256(src) != asset.sha256 and not allow_checksum_mismatch:
                    raise AssetMismatchError(aid, src)
                audio = _decode_asset_file(src, dtype)
                if audio.shape != (asset.channels, asset.frames) or asset.sample_rate != sr:
                    raise ProjectFormatError(f"source {os.path.basename(src)} does not match the project dimensions")
                asset_audio[aid] = audio
                asset.resolved_path = os.path.abspath(src)
                if aid in relink:
                    asset.path = os.path.abspath(relink[aid])
            if missing:
                raise MissingMediaError(missing)

            # layers
            block_bytes = ch * BLOCK * dtype.itemsize
            for lj in layers_list:
                if not isinstance(lj, dict):
                    raise ProjectFormatError("layer entry is not an object")
                lid = _req(lj, "id", str, "layer")
                if not _ID_RE.match(lid):
                    raise ProjectFormatError("bad layer id")
                base = lj.get("base") if isinstance(lj.get("base"), dict) else {"type": "silence"}
                b_audio = asset_audio.get(base.get("asset_id")) if base.get("type") == "asset" else None
                audio = b_audio.copy() if b_audio is not None else np.zeros((ch, n), dtype=dtype)
                data = lj.get("data")
                if data is not None:
                    if not isinstance(data, dict) or data.get("kind") != "blocks" or data.get("block_size") != BLOCK:
                        raise ProjectFormatError("unsupported layer data")
                    nb = _req(data, "n_blocks", int, "layer data")
                    im, bm = data.get("index_member"), data.get("blocks_member")
                    if im != f"layers/{lid}/index.npy" or bm != f"layers/{lid}/blocks.npy":
                        raise ProjectFormatError("layer data member mismatch")
                    max_blocks = -(-n // BLOCK)
                    if not (0 < nb <= max_blocks):
                        raise ProjectFormatError("layer block count out of range")
                    idx = _read_npy(zf, im, nb * 8 + 4096)
                    blocks = _read_npy(zf, bm, nb * block_bytes + 4096)
                    if idx.dtype != np.int64 or idx.shape != (nb,) or blocks.dtype != dtype or blocks.shape != (nb, ch, BLOCK):
                        raise ProjectFormatError("layer arrays have unexpected shape/dtype")
                    if np.any(idx < 0) or np.any(idx >= max_blocks) or len(np.unique(idx)) != nb:
                        raise ProjectFormatError("layer block index out of range")
                    if not np.all(np.isfinite(blocks)):
                        raise ProjectFormatError("layer data contains non-finite samples")
                    for i, b in enumerate(idx):
                        a0, e0 = int(b) * BLOCK, min(n, (int(b) + 1) * BLOCK)
                        audio[:, a0:e0] = blocks[i, :, : e0 - a0]
                layer = Layer(id=lid, name=str(lj.get("name", "Layer")), audio=audio, base=base,
                              provenance=str(lj.get("provenance", "")))
                for k in Layer.PROPS:
                    if k in lj and k != "name":
                        v = lj[k]
                        if k == "gain_db":
                            v = float(v)
                            if not math.isfinite(v):
                                raise ProjectFormatError("layer gain not finite")
                        elif k == "color":
                            v = str(v)
                        else:
                            v = bool(v)
                        setattr(layer, k, v)
                proj.layers.append(layer)
            if not proj.layers:
                raise ProjectFormatError("project has no layers")

            for mk in m.get("markers", []) or []:
                if isinstance(mk, dict) and isinstance(mk.get("time"), (int, float)):
                    proj.markers.append(Marker(id=str(mk.get("id", "")), time=float(mk["time"]),
                                               name=str(mk.get("name", "")),
                                               end=float(mk["end"]) if isinstance(mk.get("end"), (int, float)) else None))

            s = EditorSession(proj)
            act = m.get("active_layer_id")
            if isinstance(act, str) and proj.has_layer(act):
                s.active_layer_id = act
            try:
                if m.get("selection"):
                    s.selection = Selection.from_dict(m["selection"])
                for k, v in (m.get("named_selections") or {}).items():
                    s.named_selections[str(k)] = Selection.from_dict(v)
            except (ValueError, KeyError, TypeError) as e:
                raise ProjectFormatError(f"bad selection data: {e}") from e
            _load_areas(zf, m.get("areas") or [], s, dtype)
            s.view_state = m.get("view") if isinstance(m.get("view"), dict) else {}
            log = m.get("operation_log")
            s.operation_log = log if isinstance(log, list) else []
            s.path = path
            s.mark_saved(path)
            s.asset_cache_dir = tmpdir
            ok = True
            return s
    finally:
        if not ok:
            shutil.rmtree(tmpdir, ignore_errors=True)
