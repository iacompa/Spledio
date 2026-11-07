# `.spledio` project format (schema version 1)

An original, versioned format. It is **not** compatible with, derived from, or
an interchange format for any commercial editor's project files.

## Container

A ZIP archive. All entries are written **STORED** (uncompressed) so audio
blocks can be read directly. Allowed entry names (anything else is rejected
on load):

| Entry | Content |
|---|---|
| `manifest.json` | UTF-8 JSON, ≤ 32 MiB (see below) |
| `layers/<layer-id>/index.npy` | int64 array `(n_blocks,)`: indices of 8192-frame blocks stored for this layer |
| `layers/<layer-id>/blocks.npy` | `(n_blocks, channels, 8192)` array in the project storage dtype |
| `assets/<asset-id>.<ext>` | byte-exact copy of a source file (portable projects only) |
| `areas/<area-id>.npy` | `(channels, length)` float32/float64 component of one adjusted area (see below) |

IDs match `^[a-z]+-[0-9a-f]{12}$` (e.g. `layer-3fa1c2d4e5b6`). `.npy` files
are NumPy format 1.0/2.0 arrays and are loaded with `allow_pickle=False`;
dtype, shape and value range (finite) are validated, and their declared
uncompressed size must match the manifest before reading.

## How layer audio is stored

Each layer has a **base**:

* `{"type": "asset", "asset_id": ...}` – the decoded source file, or
* `{"type": "silence"}` – zeros (extracted / new / merged layers), or
* `{"type": "generated"}` – treated as silence for storage (signal generator / tests).

On save, the layer is compared to its base in 8192-frame blocks; only blocks
that differ (exact comparison) are written. An unedited imported layer stores
nothing. On load, the base is decoded (and its SHA-256 verified) and the stored
blocks are written over it. If the source file is missing, or no longer matches its
SHA-256 when the project is saved (changed on disk, or a project opened with a
different file accepted), the layer is saved relative to silence instead, so every
block is stored and the project no longer needs that file (a portable save refuses
a changed source instead of embedding it). This reproduces the edited audio **bit-exactly**
without replaying DSP and without any model or cache.

## manifest.json

```jsonc
{
  "format": "spledio-project",
  "schema_version": 1,
  "app": {"name": "Spledio", "version": "1.1"},
  "saved_at": 1762460000.0,
  "portable": false,
  "project": {
    "id": "proj-…", "name": "…", "sample_rate": 48000, "n_frames": 384000, "channels": 2,
    "storage_dtype": "float32",            // or "float64" (32-bit int / 64-bit float sources)
    "created": 1762459000.0,
    "processing_config": {"n_fft": 4096, "hop": 1024, "window": "hann"}  // default for future edits
  },
  "assets": [{
    "id": "asset-…", "path": "/abs/original.wav", "relative_path": "../audio/original.wav",
    "sha256": "…", "format": "WAV", "subtype": "PCM_24",
    "sample_rate": 48000, "channels": 2, "frames": 384000,
    "embedded_member": null                 // "assets/asset-….wav" when portable
  }],
  "layers": [{
    "id": "layer-…", "name": "…", "color": "#4fb3ff", "gain_db": 0.0,
    "mute": false, "solo": false, "locked": false, "polarity_inverted": false, "visible": true,
    "base": {"type": "asset", "asset_id": "asset-…"},
    "provenance": "imported from original.wav",
    "data": null | {"kind": "blocks", "block_size": 8192, "n_blocks": 3,
                    "index_member": "layers/<id>/index.npy", "blocks_member": "layers/<id>/blocks.npy"}
  }],
  "active_layer_id": "layer-…",
  "selection": { "ops": [{"mode": "replace", "shape": {"kind": "rect", "t0": 1.2, "t1": 1.85, "f0": 910, "f1": 1060}}],
                 "feather_t": 0.0, "feather_f": 0.0, "feather_enabled": true, "channels": null, "name": "" },
  "named_selections": {"name": { …selection… }},
  "markers": [{"id": "…", "time": 1.0, "name": "", "end": null}],
  "areas": [{                               // optional; adjusted areas (see below)
    "id": "area-…", "number": 1, "layer_id": "layer-…", "selection": { …selection… },
    "gain_db": -12.0,                       // or the string "-inf" when silenced; range ≤ +24
    "start": 70656, "length": 147456, "channels": [0, 1], "created": 1762459100.0,
    "component_member": "areas/area-….npy"
  }],
  "view": { "t0": 0, "t1": 8, "f_lo": 0, "f_hi": 24000, "freq_scale": "log", "colormap": "ember", … },
  "operation_log": [ {"kind": "patch", "label": "Gain -6.0 dB · 1.20-1.85 s, 910-1060 Hz · …",
                      "params": {"selection": {…}, "layer_id": "…", "channels": [0, 1],
                                 "config": {"n_fft": 4096, "hop": 1024, "window": "hann"},
                                 "method": "stft", "region": [55296, 92160], "gain_db": -6.0}, "timestamp": …} ],
  "history": {"saved": false, "undo_labels": ["…"]},
  "models_used": []
}
```

Selection shapes: `rect` (`t0,t1,f0,f1`; `"inf"`/`"-inf"` strings for unbounded
time, `f1` = 1e12 for "all frequencies"), `ellipse` (`tc,fc,rt,rf`), `polygon`
(`points: [[t,f],…]`), `brush` (`path, rt, rf, hardness, square`).
Modes: `replace`, `add`, `subtract`, `intersect`, `invert` (shape `null`).

### Adjusted areas

Each entry in `areas` is a region whose level the user stepped with the arrow keys. The member
`areas/<id>.npy` holds the area's **component** `c` – the part of the layer audio the selection covered
when the area was created (`ISTFT(M · STFT(x))`), shape `(len(channels), length)`, dtype float32 or
float64, starting at sample `start`. The layer audio already contains the adjustment (`x + (G − 1)·c`),
so a project opens correctly even if the `areas` key is ignored. With the component, changing the level
from `G` to `G'` adds `(G' − G)·c`, and removing the area adds `(1 − G)·c`, so areas stay adjustable after
reopening. On load every area is validated (id, layer exists, span inside the project, channel indices,
gain range, member name, shape, dtype, finite values); a bad entry rejects the file.

Timeline edits (delete/crop/insert time) **freeze** areas: their effect stays in the audio, the area
list is cleared, and undo brings the areas back.

## Reproducibility

Every edit's processing parameters (STFT size/hop/window, selection, gain,
channel scope, method `stft`|`time`, sample region) are stored in
`operation_log`. Display settings are stored separately under `view` and never
influence audio.

## What is *not* stored (v1)

* The undo/redo stack (only its labels). After reopening, history starts empty.
* Playback device and window layout (stored per user in QSettings instead).

## Saving & loading safety

* Save writes to `.spledio-*.partial` in the destination folder, fsyncs, then
  atomically renames over the target. A failed save leaves the previous file intact.
* Load rejects: non-ZIP data, unknown entry names (path traversal such as
  `../x` is impossible to accept), compressed entries (Spledio stores every entry
  uncompressed, so nothing can inflate beyond the archive's own size when an
  embedded source is extracted), manifests > 32 MiB, newer schema versions
  (clear "saved by a newer version" message), malformed JSON or wrong field
  types, out-of-range dimensions, mismatched array shapes/dtypes, non-finite
  samples, selection shapes the editor cannot draw (non-finite coordinates, a
  polygon with fewer than 3 points, an empty brush stroke or a brush of radius 0,
  a negative ellipse radius), and assets whose SHA-256 differs (unless the user
  explicitly accepts).
* Missing linked media raises a relink request; the UI asks the user to locate
  each file, then verifies it by checksum and dimensions.
* Embedded assets are extracted to a private temporary folder (never using
  the archive's member name as a path) and deleted when the project closes.

## Versioning policy

`schema_version` increases on any incompatible change. Readers accept any
version ≤ their own and refuse newer ones. Unknown extra manifest keys are
ignored by v1 readers.
