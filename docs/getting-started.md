# Getting started

Status: active
Document type: operational-guide
Owner: developer experience
Canonical scope: operations.getting-started
Read when: installing Local LLM Server, starting a first runtime, or verifying a working local deployment
Last reviewed: 2026-08-15

This guide is the shortest path from a clean checkout to a verified local inference endpoint. It focuses on the supported product stack rather than the legacy module-level compatibility app.

## 1. Prerequisites

Local LLM Server requires Python 3.10+ and macOS or Linux. Some backends have additional platform requirements:

- `llama_cpp`: GGUF through `llama-cpp-python`;
- `mlx`: Apple Silicon text inference through MLX;
- `llama_server`: managed `llama-server` subprocess, including supported GGUF multimodal workloads;
- `mlx_vlm_server`: Apple Silicon vision-language serving;
- transcription requires an explicitly transcription-capable runtime plus the audio extra when local preprocessing helpers are needed.

Install the package from the repository:

```bash
pip install .
```

For development or backend-specific extras:

```bash
pip install ".[dev]"
pip install ".[mlx]"
pip install ".[vision]"
pip install ".[audio]"
```

A development checkout that needs all current extras can use:

```bash
pip install -e ".[dev,mlx,vision,audio]"
```

## 2. Inspect the model registry

List configured models before starting the server:

```bash
local-llm models
```

The command shows the registry key, resolved model ID, approximate size when known, backend, tags, local-download state and the configured default model.

Download a registry model without starting the server:

```bash
local-llm download nemotron-nano-4b-q8
```

To guarantee that serving never triggers a download, add `--no-download` when starting the server.

## 3. Start one model

A minimal supported launch is:

```bash
local-llm serve --model nemotron-nano-4b-q8
```

To expose the administrative control-plane APIs and the complete Studio workflows:

```bash
local-llm serve \
  --model nemotron-nano-4b-q8 \
  --enable-admin-api
```

Defaults are intentionally local:

```text
host = 127.0.0.1
port = 1235
```

After startup, the main surfaces are:

```text
Studio       http://127.0.0.1:1235/
Swagger      http://127.0.0.1:1235/docs
Health       http://127.0.0.1:1235/health
Models       http://127.0.0.1:1235/v1/models
Identity     http://127.0.0.1:1235/v1/runtime/identity
Status       http://127.0.0.1:1235/status
```

## 4. Verify the serving stack

Check the server first:

```bash
curl http://127.0.0.1:1235/health
```

Then inspect the resident model list:

```bash
curl http://127.0.0.1:1235/v1/models
```

Inspect stable execution identity:

```bash
curl http://127.0.0.1:1235/v1/runtime/identity
```

Inspect dynamic runtime state:

```bash
curl http://127.0.0.1:1235/status
```

These last two endpoints intentionally answer different questions:

- `/v1/runtime/identity` describes the model/runtime/configuration/hardware identity that an evaluator can freeze before a run;
- `/status` describes mutable activity such as active requests, current phase and observed chunk rate.

See [`runtime-identity-api.md`](runtime-identity-api.md) and [`runtime-status-reference.md`](runtime-status-reference.md).

## 5. Send a chat completion

```bash
curl http://127.0.0.1:1235/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "nemotron-nano-4b-q8",
    "messages": [
      {"role": "user", "content": "Reply with the single word OK."}
    ],
    "temperature": 0
  }'
```

The public text API is OpenAI-compatible. Application code may therefore use the OpenAI Python SDK with the local base URL:

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:1235/v1", api_key="local")
response = client.chat.completions.create(
    model="nemotron-nano-4b-q8",
    messages=[{"role": "user", "content": "Summarize this meeting."}],
    temperature=0,
)
print(response.choices[0].message.content)
```

`api_key="local"` satisfies SDK construction; Local LLM Server does not currently authenticate the normal loopback API.

## 6. Start multiple resident runtimes

```bash
local-llm serve \
  --models nemotron-nano-4b-q8 qwen3-vl-4b \
  --default-model nemotron-nano-4b-q8 \
  --enable-admin-api
```

A configured artifact, a resident runtime and the default route are distinct states. A server may also be healthy with zero resident runtimes.

## 7. Run the Jev-vs-LLM local benchmark models

The built-in registry includes three text models used by the external Jev-vs-LLM experiment:

| Registry key | Model | Quantization | Approx. weights | Backend |
| --- | --- | --- | ---: | --- |
| `qwen3.5-4b-q4km` | Qwen3.5-4B | Q4_K_M | 2.71 GB | `llama_server` |
| `qwen3.5-9b-q4km` | Qwen3.5-9B | Q4_K_M | 5.63 GB | `llama_server` |
| `nemotron-nano-4b` | NVIDIA Nemotron-3-Nano-4B | Q4_K_M | 2.5 GB | existing configured backend |

Qwen3.5 uses 4B and 9B sizes; there is no official Qwen3.5-8B checkpoint. The 9B model is the corresponding larger local comparison.

Download the artifacts before the benchmark:

```bash
local-llm download qwen3.5-4b-q4km
local-llm download qwen3.5-9b-q4km
local-llm download nemotron-nano-4b
```

The Qwen3.5 registry entries use the managed `llama_server` backend, so a compatible current `llama-server` binary must be available to Korgis. The registry pins the Q4_K_M artifact SHA-256 metadata used by runtime identity.

For a low-memory benchmark setup, start only the small anchor runtime and enable the admin API:

```bash
local-llm serve \
  --model nemotron-nano-4b \
  --enable-admin-api \
  --no-download
```

The external benchmark can then activate the Qwen runtimes sequentially through `/api/v1/models/activate`, return to the Nemotron anchor, and unload each larger temporary runtime. This avoids requiring all three GGUFs to stay resident at once.

Benchmark requests explicitly disable thinking and hide reasoning output. Qwen3.5 registry defaults also use its non-thinking sampling profile so the measured workload is bounded decision inference rather than open-ended reasoning.

## 8. Use transcription

When an explicitly transcription-capable runtime is resident:

```bash
curl http://127.0.0.1:1235/v1/audio/transcriptions \
  -F "model=my-asr-runtime" \
  -F "file=@meeting.wav"
```

Audio modality alone does not imply transcription support. Unsupported task/capability combinations are rejected before backend execution on supported product entrypoints.

## 9. Connect AI Performance Lab

For the richest Performance Lab run, Local LLM Server provides three independent surfaces:

```text
/v1/models + /v1/chat/completions  -> inference
/v1/runtime/identity               -> frozen execution identity
/status                            -> sampled dynamic telemetry
```

Performance Lab does not need an internal Python dependency on this repository. See the Performance Lab operational guide and this repository's [`runtime-identity-api.md`](runtime-identity-api.md).

## 10. Before treating the setup as verified

A useful local readiness check is:

1. `local-llm models` shows the intended registry entry;
2. the intended model is resident in `/v1/models`;
3. a deterministic chat request succeeds;
4. `/v1/runtime/identity` identifies the intended runtime, while unknown evidence remains explicitly `null`/partial;
5. `/status` changes during inference without being confused with stable identity;
6. no unexpected remote media/code behavior has been enabled;
7. if the server is bound beyond loopback, the deployment has an explicit trusted network/authentication boundary.

If any step fails, use [`troubleshooting.md`](troubleshooting.md).