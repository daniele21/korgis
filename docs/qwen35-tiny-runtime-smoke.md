# Qwen3.5 tiny real-runtime smoke

Status: active
Document type: operational-guide
Owner: developer experience
Canonical scope: operations.qwen35-tiny-runtime-smoke
Read when: verifying a low-cost real GGUF inference path in hosted CI or reproducing the tiny runtime smoke locally
Last reviewed: 2026-09-20

Purpose: low-cost real inference validation for Korgis' GGUF serving path.

## What this test proves

This environment exercises the real runtime path, not a mocked backend:

```text
Qwen3.5-0.8B GGUF
        ↓
managed llama-server
        ↓
Korgis runtime manager
        ↓
Korgis HTTP API
        ↓
/health + /v1/models + /v1/runtime/identity
        ↓
/v1/chat/completions
```

It is a functional smoke test only. It is not performance evidence and must not be used to compare latency or quality with the 4B/9B benchmark models.

## Tiny fixture

Registry key:

```text
qwen3.5-0.8b-smoke-q4
```

Artifact:

- model: `Qwen/Qwen3.5-0.8B`
- source: `ggml-org/Qwen3.5-0.8B-GGUF`
- file: `Qwen3.5-0.8B-Q4_0.gguf`
- quantization: `Q4_0`
- approximate download: 563 MB
- SHA-256: `57d1997790d1744fba5b40a7317df71ea5e2acee28c47e78f0cce39c0703f8cf`
- license: Apache-2.0
- context for smoke: 2048
- thinking: disabled

The small Q4_0 fixture is deliberate: it minimizes download/runtime cost while still exercising the same Qwen3.5 + GGUF + llama.cpp architecture family used by larger local models.

## Automated environment

The workflow `.github/workflows/qwen35-tiny-runtime-smoke.yml` runs on the dedicated feature branch and remains manually dispatchable after merge.

It:

1. installs the repository without the Python `llama-cpp-python` backend;
2. downloads a pinned CPU `llama-server` build;
3. restores/caches the 0.8B GGUF;
4. verifies the GGUF SHA-256;
5. starts Korgis on `127.0.0.1:1235`;
6. waits for `/health`;
7. snapshots health, resident models and runtime identity;
8. executes `tests/real_runtime/smoke_runtime.py`;
9. verifies that real completion and token-usage evidence were observed;
10. stops Korgis and uploads bounded evidence.

No API keys are required and there is no cloud inference charge.

## Local reproduction

Install the project and provide a compatible current `llama-server` binary:

```bash
python3 -m pip install 'uv==0.8.13'
uv sync --frozen --extra dev --no-install-package llama-cpp-python

uv run --no-sync local-llm download qwen3.5-0.8b-smoke-q4

uv run --no-sync local-llm serve \
  --model qwen3.5-0.8b-smoke-q4 \
  --backend llama_server \
  --llama-server-bin /path/to/llama-server \
  --ctx-size 2048 \
  --host 127.0.0.1 \
  --port 1235 \
  --no-download
```

Then, in another terminal:

```bash
uv run --no-sync python tests/real_runtime/smoke_runtime.py \
  --base-url http://127.0.0.1:1235 \
  --model qwen3.5-0.8b-smoke-q4
```

A successful result reports `"ok": true`, the `local-llm-identity-v1` protocol, the selected runtime/backend and whether prompt/completion token usage was observed.

## Evidence semantics

Passing this smoke means the tested Korgis revision can load the pinned tiny Qwen3.5 GGUF through the managed llama-server backend and complete one real HTTP inference on the runner.

It does **not** prove:

- performance on Apple Silicon or another target device;
- quality of Qwen3.5-4B/9B;
- production resource safety;
- long-running stability;
- throughput/concurrency behavior.
