# Model licenses

**No AI models are bundled or downloaded.** Spledio 1.1 ships no model
weights, imports no machine-learning framework, and has no download code.
`src/spledio/models/` is an empty adapter slot, and the verified `.venv`
contains no PyTorch, ONNX Runtime, TensorFlow or similar package (see
`requirements.txt`). Project files record `"models_used": []`.

## Rules for any future optional adapter

From [`CONTINUATION.md`](CONTINUATION.md):

- Optional and lazily imported.
- A download must be explicit, started by the user, show the license, and
  verify a checksum.
- Model name, version, license and weights checksum go into the manifest's
  `models_used`.
- Before merging an adapter, add a row below with **verified** code and
  weight licenses, and check GPL-3.0-only compatibility. Weights under a
  non-commercial or otherwise restricted license must not be bundled or
  auto-downloaded.

## Candidate adapters (not installed, not evaluated in this app)

Each fact is linked to the source it was read from on 2026-11-06 (the
project's own README). Anything not stated there is marked **to verify
before use**. Even "verified" items should be re-checked at the exact
release and weights file you adopt.

| Candidate | Code license | Weights license | Redistribution of weights permitted? | Required runtime | Input sample rate | Output |
|---|---|---|---|---|---|---|
| **Demucs / HTDemucs** ([facebookresearch/demucs](https://github.com/facebookresearch/demucs)) | MIT ("Demucs is released under the MIT license") | **To verify before use**: the README states no separate license for the pretrained weights | **To verify before use** | Python + PyTorch/torchaudio (README: Python ≥ 3.8; ffmpeg needed on Windows); GPU optional (README: ≥ 3 GB VRAM, ~7 GB with defaults) | Output stems are 44.1 kHz; input is "resampled on the fly if necessary" | `htdemucs`: 4 stems (drums, bass, other, vocals). `htdemucs_6s`: adds guitar and piano (README: piano "not working great"). |
| **DeepFilterNet** ([Rikorose/DeepFilterNet](https://github.com/Rikorose/DeepFilterNet)) | MIT OR Apache-2.0 ("All code and model weights in this repository are dual-licensed") | MIT OR Apache-2.0 (same statement) | Yes per README (MIT/Apache-2.0); **to verify before use** for the exact weights file | PyTorch (`deepfilternet` pip package), or the Rust `deep-filter` binary / `libDF`, or a LADSPA plugin | 48 kHz (the `deep-filter` binary accepts only 48 kHz WAV) | One enhanced (noise-suppressed) speech signal, not stems |
| **openai-whisper** ([openai/whisper](https://github.com/openai/whisper)) | MIT ("Whisper's code and model weights are released under the MIT License") | MIT (same statement) | Yes per README; **to verify before use** for the exact checkpoint | Python + PyTorch; **requires the `ffmpeg` command-line tool**; Rust may be needed to build `tiktoken` | Not stated in the README; **to verify before use** (16 kHz mono is commonly cited) | Text transcript; `--task translate` to English (not with `turbo`). Timestamp details: **to verify before use**. |
| **whisper.cpp** ([ggml-org/whisper.cpp](https://github.com/ggml-org/whisper.cpp)) | MIT | **To verify before use**: ggml models are hosted at huggingface.co/ggerganov/whisper.cpp, and the README states no model license (they are conversions of OpenAI's MIT-licensed weights; verify) | **To verify before use** | Plain C/C++ "without dependencies", CPU-only possible; optional GPU backends (Metal/Core ML/CUDA/Vulkan); ffmpeg optional (`WHISPER_COMMON_FFMPEG`) | `whisper-cli` takes 16-bit WAV; the README converts to 16 kHz mono PCM | Text transcript (with timestamps; **to verify** the output formats) |

### GPL-3.0-only notes

- MIT and Apache-2.0 code and weights are compatible with GPL-3.0-only.
- Weights whose license is unstated (Demucs, the whisper.cpp ggml
  conversions) must stay **"to verify"**. Do not bundle or auto-download them
  until a license is confirmed from an authoritative source.
- All candidates except whisper.cpp pull in PyTorch, a very large dependency
  with its own third-party notices. It must stay an optional extra and never
  enter `requirements.txt`.
- openai-whisper needs an FFmpeg binary at runtime. Any FFmpeg Spledio
  distributes must be an LGPL build (see
  [`DEPENDENCY_INVENTORY.md`](DEPENDENCY_INVENTORY.md#ffmpeg-not-used-by-spledio-not-to-be-bundled)).
- The Demucs repository was archived on 2025-01-01 and is unmaintained,
  according to its README, which points to a fork. Evaluate the fork's
  license separately.
