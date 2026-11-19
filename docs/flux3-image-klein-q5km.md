# FLUX.3 Image Klein Q5_K_M · LM Studio integration (experimental)

**Status:** native Korgis registry/loader integrated. Unit tests do not prove
the user's actual Mac binary can generate FLUX.3 images; first image E2E and
resource calibration are mandatory before marking production-ready.

## Components (distilled, 16-step, CFG 1.0)

FLUX.3 **Image** Klein is not FLUX.2 Klein or FLUX.3 Klein **Base**.
The Q5_K_M GGUF is the **diffusion transformer only**, not a complete
standalone model. [Unsloth](https://huggingface.co/unsloth/flux-3-image-klein-GGUF)
and [stable-diffusion.cpp](https://github.com/leejet/stable-diffusion.cpp/blob/master/docs/models/flux3.md)
specify a separate **Qwen3-VL-4B-Instruct text encoder** and **FLUX.2 VAE**.

1. Diffusion GGUF: **existing**, no copy or automatic download:
   `$HOME/.lmstudio/models/unsloth/flux-3-image-klein-GGUF/flux-3-image-klein-Q5_K_M.gguf`
   (Unsloth Q5_K_M; approximately 4.22 decimal GB).
2. Text encoder: `unsloth/Qwen3-VL-4B-Instruct-GGUF`,
   `Qwen3-VL-4B-Instruct-Q4_K_M.gguf`, managed target
   `~/.local-llm/models/flux-3-image-klein-gguf-q5km/text_encoder/Qwen3-VL-4B-Instruct-Q4_K_M.gguf`.
   Registry revision `cc8371d67a57fc6ae05fc5a9c2df763914928539`.
3. VAE: `unsloth/flux-3-image-klein`, `ae.safetensors`, managed target
   `~/.local-llm/models/flux-3-image-klein-gguf-q5km/vae/ae.safetensors`.
   **Its upstream SHA256/revision are not yet locked**, so do not treat this
   provisional source entry as immutable-provenance evidence.

Both auxiliary components must be present to start image inference. The
registry path is resolved relative to **the current user's home**, not a
hard-coded Mac account. If the LM Studio GGUF is absent, Korgis rejects it
without downloading it somewhere else. The original GGUF is validated for
regular file, a plausible minimum byte size, and the `GGUF` header, but
this is *not yet full artifact integrity verification*.

## Start on a Mac with LM Studio weights already installed

```bash
cd ~/Projects/experiments/korgis
git fetch origin
git switch feat/flux3-klein-q5km-lmstudio-20261119
git pull --ff-only

# Check the local transformer is present; no model inference runs here.
ls -lh "$HOME/.lmstudio/models/unsloth/flux-3-image-klein-GGUF/flux-3-image-klein-Q5_K_M.gguf"

# First run: Korgis may download the separate 4B encoder and VAE to its
# own managed bundle. It never re-downloads the existing LM Studio GGUF.
uv run --frozen local-llm serve \
  --model flux-3-image-klein-gguf-q5km \
  --host 127.0.0.1 --port 1235 \
  --sd-server-bin "$HOME/.local-llm/tools/sdcpp-b43af40461cc64c89cac3b621019f8914f50dd9c/build/bin/sd-server"
```

For a strictly offline run *after* the two auxiliary artifacts exist, add
`--no-download` to the command. If they are not installed, the error will
show the exact missing managed path. Source repository downloads could be
gated or unavailable; use an approved Hugging Face client to fetch those two
files explicitly if Korgis cannot obtain them. The pinned native
stable-diffusion.cpp build must include FLUX.3 (upstream support introduced
October 2026). A filename or revision alone does not establish binary support.

Check `curl -fsS http://127.0.0.1:1235/v1/models` for
`flux-3-image-klein-gguf-q5km`. Then run one **small** generation:

```bash
curl -fsS http://127.0.0.1:1235/v1/images/generations \
  -H 'Content-Type: application/json' \
  -d '{"model":"flux-3-image-klein-gguf-q5km",
       "prompt":"A single emerald-green mug on a white desk, morning window light",
       "size":"512x512","n":1,"seed":43,"num_inference_steps":16,
       "output_format":"png","guidance_scale":1.0}' \
  > flux3-response.json

python3 - <<'PY'
import base64, json
from pathlib import Path
data = json.loads(Path("flux3-response.json").read_text())
Path("flux3-test.png").write_bytes(base64.b64decode(data["data"][0]["b64_json"]))
print("Saved flux3-test.png")
PY
open flux3-test.png
```

Check that the output is coherent, the backend actually uses the GGUF and
**Qwen3-VL-4B + FLUX.2 VAE** rather than Qwen Image weights, and record
elapsed time, process RSS/unified-memory pressure and swap I/O. The
configured `disk_backed_image` estimate is **9 GiB provisional**; it is
not a benchmarked peak or a license to turn off host protections.

## Experiments comparison

Check out Experiments branch
`feat/imagegen-flux3-klein-q5km-20261119`. In the Image Generation
Playground select **FLUX.3 Image Klein GGUF Q5_K_M**, or pick it as an
additional arm in **Compare models**. To generate a FLUX vs Gemini vs GPT
comparison, start FLUX in Korgis and configure server-side cloud API keys.
You can also compare FLUX with Qwen if *both* are loaded and host memory
permits. The default `serve --model` starts only one local runtime;
`--models` can start multiple but may exceed available RAM on a 32 GiB Mac.
Avoid automatically forcing simultaneous residency.

The existing benchmark `smoke`, `budget`, and `robust` suites can
compare the new model using the catalog key. The FLUX Kommunity License
has noncommercial/commercial-output conditions; review it before customer
use. No auto-download of the 9B prompt upsampler is attempted. Unlike the
BFL reference pipeline, Korgis feeds the **literal prompt as entered**,
so prompt-upsampled BFL service examples are not identical to this setup.

### Acceptance checklist

- [x] Registered distinct model and 16-step preset, without altering Qwen
- [x] Deterministic path resolution and local-only GGUF startup checks
- [x] Separate encoder/VAE dependencies, per-model metadata and benchmark arm
- [ ] CI test verification and complete native Mac startup
- [ ] 512px generation at seed 43 with correct 16-step sampling
- [ ] Measured RSS, unified-memory pressure, swap and runtime latency
- [ ] Validate outputs at 768/1024 before quality comparisons
- [ ] Lock the upstream VAE revision/checksum and local GGUF digest
- [ ] Validate native image editing separately; it is not enabled by this PR
