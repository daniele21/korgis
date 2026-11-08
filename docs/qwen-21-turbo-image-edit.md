# Qwen Image 2.1 Turbo: resource-safe reference editing (draft)

**Status: integration foundation implemented, native edit with vision projector not yet proven E2E.** Keep this separate from existing text-to-image and from Experiments benchmarks. Do not merge based on API unit tests alone.

## Why this design

The pinned `stable-diffusion.cpp` commit
[`b43af40461cc64c89cac3b621019f8914f50dd9c`](https://github.com/leejet/stable-diffusion.cpp/tree/b43af40461cc64c89cac3b621019f8914f50dd9c)
already supports reference images through its asynchronous `/sdcpp/v1/img_gen`
(`ref_images` field). It also has `/v1/images/edits` and SDAPI `img2img`,
but using the already integrated native async-job API reuses the current
submission, polling, timeout and cancellation machinery and avoids a second
inference engine. This first release does not expose arbitrary native options,
init-image strength or masks before validating their behavior with Qwen Turbo.

Qwen Image 2.1 + a GGUF Qwen3-VL-8B text encoder **requires the matching
vision projector**. The [upstream model guide](https://github.com/leejet/stable-diffusion.cpp/blob/b43af40461cc64c89cac3b621019f8914f50dd9c/docs/qwen_image_2.1.md)
specifies `--llm_vision` for reference editing.

Other designs studied:
- **InvokeAI:** model cache has RAM/VRAM limits, lock-based access, and controlled
  model offload/eviction; Korgis retains its own central ResourceManager and
  serializes inference through the existing runtime lease.
- **Diffusers:** CPU, group and disk offloading all trade memory for latency.
  For this Mac/Metal GGUF use case Korgis retains `diffusion=disk,te=cpu,vae=cpu`,
  `--disable-prefetch` and tiled VAE rather than adding a PyTorch runtime.
- **ComfyUI:** flexible image workflows are useful as a quality/reference oracle,
  but do not justify adding a DAG runtime or second weights manager to Korgis.

## Opt-in model and resource contract

The original three local files remain the text-to-image bundle: diffusion,
text encoder and VAE. A fourth **optional** artifact is registered separately:

- Repo: `unsloth/Qwen3-VL-8B-Instruct-GGUF`
- Immutable revision: `174e4a339d8aceef41c87fda9f732c042d49e50b`
- Filename: `mmproj-BF16.gguf` (~1.16 GB on disk)
- SHA256: `6516bb64bae1503a0fcd7ec9fa39655f8c481580be0a0a066397941d9761c9f4`
- Local target: `~/.local-llm/models/qwen-image-2.1-turbo-gguf-q4km/text_encoder/mmproj-BF16.gguf`

Projector metadata is not part of the text-to-image completeness gate. Without
the explicit editing flag the projector is not opened, verified or downloaded.
With the flag, Korgis verifies the full SHA256 and launches its existing native
server with `--llm_vision`. `--no-download` fails if the projector is missing;
the app does not silently download model assets in offline mode.

Existing Turbo T2I uses an **8 GiB provisional** resident estimate. Edit mode
adds **2 GiB provisional** for projector and vision activations, yielding a
**10 GiB effective resident estimate** on the same memory ledger. The existing
disk-backed profile uses a 1.25× reserve factor and up to 3 GiB OS reserve:
for a 32 GiB Mac an editing startup therefore needs roughly **15.5 GiB
reclaimable memory**, before optional concurrent load. This bound is *not*
proven adequate until a real edit is profiled. Existing swap usage is never
added to available RAM. The API rejects new editing work when host pressure
is elevated or unknown, both before and after acquisition of the runtime lease.

## First API slice

`POST /v1/images/edits`, multipart form:
- `image`: exactly one PNG file (max 8 MiB, up to 1 megapixel source pixels).
- `prompt`: text editing instruction.
- `model`: resident model key, optional.
- `size`: `WIDTHxHEIGHT`, optional; default source dimensions.
- `seed`, `num_inference_steps`: optional, validated against 8-step Turbo cap.
- `n`: must be 1.

First-release image output is capped at 1 megapixel and dimensions must be
multiples of 32. Input data is validated using PNG signature, IHDR,
dimensions and IHDR checksum before encoding to base64 and sending `ref_images`
to the private native server. No remote image URL, arbitrary filesystem path,
multiple reference images, mask, or unvalidated native parameters are accepted.
Korgis does not intentionally persist the uploaded image (the multipart
library may spool larger bodies to temporary local files); raw images are not
logged. Output is OpenAI-shaped `data[0].b64_json`; metadata includes source
SHA256, original dimensions, generation and local runtime evidence.

**This is not a network-hardened multi-user upload service.** Use loopback
binding, avoid untrusted remote clients, and do not publish the API publicly
without transport-level size limits/authentication and further input fuzzing.

## Manual test when projector is installed

First verify free disk space, the file and SHA256. Explicit download is needed
only for this editing variant, not for normal text-to-image:

```bash
mkdir -p "$HOME/.local-llm/models/qwen-image-2.1-turbo-gguf-q4km/text_encoder"
# Download from the pinned Hugging Face revision above using an approved
# network-enabled tool, then verify its SHA256:
shasum -a 256 "$HOME/.local-llm/models/qwen-image-2.1-turbo-gguf-q4km/text_encoder/mmproj-BF16.gguf"
```

Run the *edit foundation branch* of Korgis (not the stable T2I branch):
```bash
uv run --frozen local-llm serve \
  --model qwen-image-2.1-turbo-gguf-q4km \
  --image-editing-enabled \
  --host 127.0.0.1 --port 1235 \
  --sd-server-bin "$HOME/.local-llm/tools/sdcpp-b43af40461cc64c89cac3b621019f8914f50dd9c/build/bin/sd-server" \
  --no-download
```

When resident startup and memory admission succeed, submit a local 512x512 PNG:

```bash
curl -fsS http://127.0.0.1:1235/v1/images/edits \
  -F "model=qwen-image-2.1-turbo-gguf-q4km" \
  -F "prompt=Change the ceramic mug from red to blue; keep the rest identical" \
  -F "image=@/absolute/path/to/reference-512.png;type=image/png" \
  -F "size=512x512" -F "seed=42" -F "num_inference_steps=8" \
  > edited-response.json
python3 - <<'PY'
import base64
import json
from pathlib import Path
result = json.loads(Path("edited-response.json").read_text())
Path("edited-image.png").write_bytes(
    base64.b64decode(result["data"][0]["b64_json"])
)
print(result["korgis"]["latency_ms"])
PY
```

Do **not** run production image-editing workloads on the previous T2I 8 GiB
estimate, or bypass `LOCAL_LLM_HOST_MEMORY_GUARD` after a refusal.

## Acceptance plan, in order

1. Static and isolated API/engine tests: text-to-image unchanged; opt-in
   capabilities, checksum and path, command `--llm_vision`, malformed image
   rejection, exact one native `ref_images` field, no input pixels logged.
2. On the Mac, check if the projector is present; if absent, do not simulate
   editing success. Download and checksum it in a controlled separate step.
3. Native `sd-server` small 512px edit with exact seed and prompt. Verify
   that the pinned build actually supports Qwen Turbo reference conditioning.
4. Full Korgis E2E edit on host: initial and final memory pressure, RSS,
   swap-in/out, wall time, image fidelity and source/image hashes.
5. Repeat at 768 and 1024, with at least three prompts: object color change,
   preserve composition, and legible text replacement. Include negative
   source checks (invalid PNG, too large, missing projector, low memory).
6. Only after E2E and manual review: JPEG/WebP ingestion, multiple references,
   masks, queued jobs/cancellation and separate Experiments evaluation suite.

Do not interpret a valid output PNG as successful editing: image-to-image
requires **measurable adherence to the source and instruction**, not just
successful inference. Keep the benchmark and visual review in
`daniele21/experiments`.
