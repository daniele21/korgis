<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="design/brand/logo/korgis-reversed-dark.png">
    <img src="design/brand/logo/korgis-horizontal.png" alt="Korgis logo" width="420">
  </picture>
</p>

<h1 align="center">Korgis</h1>

<p align="center">
  <strong>Your AI. Local. Ready to use.</strong><br>
  Run and manage local AI models behind one stable application-facing API.
</p>

<p align="center">
  <a href="https://daniele21.github.io/">Mission</a> ·
  <a href="#why-korgis-exists">Why</a> ·
  <a href="#what-you-can-do-today">Today</a> ·
  <a href="#how-to-use-it">How to use it</a> ·
  <a href="#how-it-works">How it works</a> ·
  <a href="#current-status-and-limits">Status</a> ·
  <a href="docs/README.md">Docs</a>
</p>

**Korgis** is the product and browser control-plane identity. **Local LLM Server** remains the repository, package and CLI identity.

## Why Korgis exists

I'm exploring [how much AI can move from the cloud to infrastructure and devices we control](https://daniele21.github.io/), and where Local, Hybrid or Cloud actually makes sense.

Korgis tackles the runtime side of that question:

> **Can local models become reliable infrastructure for real applications?**

Running one model is easy. Real applications are harder. They may need several specialist models, predictable memory use, safe lifecycle handling, scheduling, observability and reproducible execution.

Korgis puts those concerns behind one local control plane.

## What you can do today

You can:

- run GGUF and MLX models through supported local backends;
- keep multiple runtimes resident behind one HTTP server;
- use text, vision-language, transcription and local image-generation capabilities when the selected runtime supports them;
- load, inspect, pin and unload runtimes from the browser control plane;
- enforce explicit resource budgets and bounded request admission;
- inspect runtime identity, resource state, scheduler state and diagnostics;
- run reproducible evaluations and compare compatible evidence;
- call text generation through an OpenAI-compatible API;
- generate local images through `/v1/images/generations` with an explicit image-generation runtime.

Korgis does not replace inference engines such as `llama.cpp` or MLX. It manages the lifecycle, policy, resources and evidence around them.

## How to use it

### 1. Install the project

```bash
git clone https://github.com/daniele21/local-llm-server.git
cd local-llm-server

python3 -m pip install 'uv==0.8.13'
uv sync --frozen --extra dev
```

### 2. Download and start a model

```bash
uv run --frozen local-llm models
uv run --frozen local-llm download nemotron-nano-4b

uv run --frozen local-llm serve \
  --model nemotron-nano-4b \
  --enable-admin-api \
  --no-download
```

The server binds to `127.0.0.1` by default.

#### Spark-X2.5-4B Q4_K_M

The built-in `spark-x2.5-4b-q4km` profile uses
`XHToken/Spark-X2.5-4B-GGUF` and requires llama.cpp build **10828 or newer**.
Korgis enforces that requirement before starting the subprocess.

```bash
uv run --frozen local-llm download spark-x2.5-4b-q4km

uv run --frozen local-llm serve \
  --model spark-x2.5-4b-q4km \
  --enable-admin-api \
  --no-download
```

If the discovered llama-server is too old, update llama.cpp or point
`LOCAL_LLM_SERVER_BIN` to a build >= 10828. The model supports a native 1M-token
context; the built-in profile uses 131072 tokens as a practical local benchmark
default. Thinking is switchable and disabled by default in this profile.

#### Ternary Bonsai 2 27B PTQ1_0

The built-in `ternary-bonsai2-27b-ptq1` profile uses PrismML's
`PTQ1_0` GGUF. This artifact requires the PrismML llama.cpp fork rather
than stock llama.cpp. Keep that specialist runtime scoped to this model:

```bash
export PRISM_LLAMA_SERVER_BIN="/absolute/path/to/prism-llama.cpp/build/bin/llama-server"

uv run --frozen local-llm download ternary-bonsai2-27b-ptq1

uv run --frozen local-llm serve \
  --model ternary-bonsai2-27b-ptq1 \
  --enable-admin-api \
  --no-download
```

Download or build the compatible runtime from
`https://github.com/PrismML-Eng/llama.cpp`. The model-specific
`PRISM_LLAMA_SERVER_BIN` setting is intentionally fail-closed: Korgis will
not silently fall back to a stock `llama-server` for this model.

Open:

```text
http://127.0.0.1:1235/
```

Useful local surfaces:

| Surface | URL |
| --- | --- |
| Korgis | `http://127.0.0.1:1235/` |
| API examples | `http://127.0.0.1:1235/example` |
| Swagger | `http://127.0.0.1:1235/docs` |
| Health | `http://127.0.0.1:1235/health` |
| Runtime identity | `http://127.0.0.1:1235/v1/runtime/identity` |
| Runtime status | `http://127.0.0.1:1235/status` |

### 3. Use the product

A simple first loop is:

1. open **Overview** and check that the server is healthy;
2. inspect the model under **Models & Runtimes**;
3. send a prompt from **Playground**;
4. inspect runtime/resource evidence under **System / Diagnostics**;
5. run an evaluation if you want comparable evidence.

### 4. Call it from an application

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:1235/v1",
    api_key="local",
)

response = client.chat.completions.create(
    model="nemotron-nano-4b",
    messages=[{"role": "user", "content": "Extract the action items."}],
    temperature=0,
)

print(response.choices[0].message.content)
```

`api_key="local"` only satisfies SDK construction. The normal loopback API does not currently require authentication.

To use your own GGUF, multiple models or other backends, see [`docs/getting-started.md`](docs/getting-started.md) and [`docs/configuration-reference.md`](docs/configuration-reference.md).

## How it works

```text
Application / Korgis
        |
        v
Local HTTP API
        |
        v
Capability + policy boundary
        |
        v
Scheduler + runtime manager
        |
        v
Resident model + resource lease
        |
        v
llama.cpp / MLX / specialist backend
        |
        v
Normalized output + evidence
```

A few concepts stay deliberately separate:

```text
artifact != configured model != resident runtime != default route
```

That makes lifecycle and resource ownership explicit instead of hiding them behind a single “model loaded” state.

Korgis also keeps evidence honest: measured, estimated, configured and unavailable values are not collapsed into the same claim.

## Security defaults

The default boundary is local and conservative:

- loopback bind on `127.0.0.1`;
- CORS disabled unless configured;
- admin APIs disabled unless explicitly enabled;
- no silent cloud fallback;
- remote media and remote model/tokenizer code require explicit trust;
- public execution identity does not expose private paths, prompts or outputs.

The server does not currently provide authentication. Do not expose administrative routes to an untrusted network.

## Current status and limits

Korgis is an active local AI runtime control plane with accepted deterministic software validation and representative Apple Silicon evidence for the tested scope.

Current limits still matter:

- support claims are tied to tested models, backends, hardware and procedures;
- automatic pressure-triggered eviction remains disabled;
- post-stop memory deltas are observations, not a general reclamation or production-safety guarantee;
- Qwen Image 2.1 has the generic BF16 Diffusers baseline, an MFlux Q8 Apple/MLX-oriented
  profile and a separate GGUF Q4_K_M profile managed through stable-diffusion.cpp; deterministic
  software validation does not establish real Mac peak-memory or performance claims;
- new hardware, performance, cancellation, thermal or cross-device claims need matching representative evidence.

See [`docs/current-state.md`](docs/current-state.md) for the exact current state.

## Documentation

| Need | Start here |
| --- | --- |
| First run | [`docs/getting-started.md`](docs/getting-started.md) |
| Configuration | [`docs/configuration-reference.md`](docs/configuration-reference.md) |
| HTTP API | [`docs/http-api-reference.md`](docs/http-api-reference.md) |
| Runtime status | [`docs/runtime-status-reference.md`](docs/runtime-status-reference.md) |
| Runtime identity | [`docs/runtime-identity-api.md`](docs/runtime-identity-api.md) |
| Architecture | [`docs/architecture.md`](docs/architecture.md) |
| Current state | [`docs/current-state.md`](docs/current-state.md) |
| Hardware evidence | [`docs/device-evidence-runbook.md`](docs/device-evidence-runbook.md) |
| Documentation index | [`docs/README.md`](docs/README.md) |

## Develop and validate

Contributors work from `dev` and follow [`AGENTS.md`](AGENTS.md). Canonical setup, test, E2E, build and cleanup commands live in [`.engineering/commands.json`](.engineering/commands.json).

## License

See [`LICENSE`](LICENSE).

Built by [Daniele Moltisanti](https://daniele21.github.io/) as the reusable execution layer of a broader Local AI effort: control the runtime, measure the result, then decide Local, Hybrid or Cloud.
