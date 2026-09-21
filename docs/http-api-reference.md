# HTTP API reference

Status: active
Document type: operational-reference
Owner: public API
Canonical scope: operations.http-api
Read when: integrating an application, evaluator, or operational tool with Local LLM Server
Last reviewed: 2026-09-21

This document explains the supported HTTP surfaces and their operational semantics. Swagger at `/docs` remains the executable schema for the checked-out revision; this guide owns the cross-endpoint meaning, compatibility expectations and usage patterns that are difficult to express in generated API docs.

## Base URL and security model

The default local root is:

```text
http://127.0.0.1:1235
```

The OpenAI-compatible namespace is:

```text
http://127.0.0.1:1235/v1
```

The default bind is loopback and the server does not currently provide built-in authentication. Binding to `0.0.0.0` or another non-loopback interface exposes public routes to the network; place that deployment behind an explicit trusted network/authentication boundary.

Administrative routes are disabled unless the server is launched with `--enable-admin-api`.

## Public runtime endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | server/runtime readiness |
| `GET` | `/status` | mutable runtime activity/phase evidence |
| `GET` | `/v1/runtime/identity` | stable, versioned, path-free execution identity |
| `GET` | `/v1/models` | OpenAI-compatible resident model discovery |
| `POST` | `/v1/chat/completions` | OpenAI-compatible chat completion and SSE streaming |
| `POST` | `/v1/audio/transcriptions` | first-class multipart audio-to-text for explicit ASR runtimes |
| `GET` | `/` | Local LLM Studio |
| `GET` | `/example` | copy-ready integration examples |
| `GET` | `/docs` | Swagger UI for the active revision |

## `GET /health`

Use this for a basic process/product-stack readiness check before attempting inference. A successful health response means the HTTP stack is responding; it does not imply that a particular requested model is resident or that every backend is ready for every task.

For model-specific readiness, combine health with `/v1/models` and, when relevant, `/v1/runtime/identity`.

## `GET /v1/models`

The endpoint follows the OpenAI model-list shape and exposes resident models. A minimal client should rely on `data[].id` for model discovery.

Representative shape:

```json
{
  "object": "list",
  "data": [
    {
      "id": "nemotron-nano-4b-q8",
      "object": "model"
    }
  ]
}
```

Local LLM Server may enrich model entries with server-owned metadata such as registry key, backend, creation state or default-route status. Generic OpenAI clients should not require provider-specific enrichments.

A configured/downloaded model is not automatically resident, and a resident model is not automatically the default route.

## `POST /v1/chat/completions`

### Minimal request

```json
{
  "model": "nemotron-nano-4b-q8",
  "messages": [
    {"role": "user", "content": "Reply with OK."}
  ],
  "temperature": 0
}
```

If the request names a model, Local LLM Server either routes to that model or fails explicitly. It does not silently substitute a different local or remote model.

### Canonical non-streaming response

Consumers should primarily rely on standard OpenAI-compatible fields:

```json
{
  "model": "nemotron-nano-4b-q8",
  "choices": [
    {
      "message": {
        "role": "assistant",
        "content": "OK"
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 8,
    "completion_tokens": 1
  }
}
```

`usage` and backend timing evidence are emitted only when the underlying runtime can supply trustworthy values. Missing metrics remain missing; they are not reconstructed from unrelated counters.

The server may include provider-specific convenience/evidence fields in addition to the OpenAI-compatible response. Integrations that require reproducible semantics should explicitly document any such field they consume instead of treating all extras as stable API.

### Workload scheduling headers

When request admission is enabled, callers may classify a chat request with:

```text
x-local-llm-workload-class: interactive | standard | batch | background
```

The default is `standard`. Higher-priority classes can overtake lower-priority queued work, while one priority point per second of queue wait prevents indefinite starvation. Equal effective priority remains FIFO within a runtime and uses runtime round-robin across runtimes. This is admission policy only: Korgis does not interrupt an already-running backend request to service a newer high-priority request.

The validated class is echoed in the response header `x-local-llm-workload-class`. An unsupported class returns HTTP 400 before inference. Queue timeout remains independently controlled by `x-local-llm-queue-timeout-ms`.

### Streaming

Set:

```json
{
  "stream": true,
  "stream_options": {"include_usage": true}
}
```

The response uses Server-Sent Events with `data:` frames and an OpenAI-compatible delta structure. Terminal usage/timing information may arrive after content frames. A buffered completed response is never labelled as true incremental streaming.

## `POST /v1/audio/transcriptions`

This endpoint is a first-class transcription task, not a chat prompt convention.

```bash
curl http://127.0.0.1:1235/v1/audio/transcriptions \
  -F "model=my-asr-runtime" \
  -F "file=@meeting.wav"
```

The selected runtime must explicitly declare transcription capability. Merely accepting audio as a modality is not sufficient. Task-specific evidence such as backend wall-clock duration, audio duration, realtime factor and segment count remains distinct from generation token/TTFT metrics.

## `GET /v1/runtime/identity`

This endpoint answers: **what exact resident execution configuration would produce an inference result?**

Protocol:

```text
local-llm-identity-v1
```

It can expose model ID/revision/hash/quantization, backend/version, safe effective runtime configuration + digest and bounded hardware identity. It deliberately excludes private paths, URLs, credentials, prompt/output content, hostname and dynamic request counters.

Read [`runtime-identity-api.md`](runtime-identity-api.md) for the complete JSON schema and evidence rules.

## `GET /status`

This endpoint answers: **what is the serving runtime doing now?**

Representative selected-runtime fields include:

```json
{
  "model": "nemotron-nano-4b-q8",
  "backend": "llama_cpp",
  "state": "ready",
  "active_requests": 1,
  "max_concurrent_requests": 1,
  "phase": "generating",
  "output_chunks": 12,
  "output_characters": 640,
  "chunks_per_second": 18.4
}
```

The multi-runtime response may place these objects under `models[model_key]` and expose `default_model` at the top level.

`chunks_per_second` is chunk evidence, not token throughput. See [`runtime-status-reference.md`](runtime-status-reference.md).

## Administrative control-plane endpoints

These routes exist only when `--enable-admin-api` is active.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/v1/models/registry` | configured catalog and capabilities |
| `POST` | `/api/v1/models/load` | load another runtime |
| `POST` | `/api/v1/models/activate` | load/select the default runtime |
| `DELETE` | `/api/v1/models/{model}` | unload an idle runtime |
| `GET` | `/api/v1/resources` | resource budget and accounting evidence |
| `GET` | `/api/v1/evidence` | privacy-safe runtime/task evidence |
| `GET` | `/api/v1/scheduler` | queue/admission and aggregate workload-class evidence |
| `GET` | `/api/v1/policies` | effective policy evidence |
| `GET` | `/api/v1/residency` | pin/eviction eligibility state |
| `POST` | `/api/v1/residency/pin` | pin or unpin a runtime |
| `POST` | `/api/v1/residency/eviction/preview` | deterministic LRU/TTL candidate preview |
| `POST` | `/api/v1/residency/evict` | explicit administrative eviction attempt |
| `GET/POST` | `/api/v1/evaluation/...` | test sets, runs, history and comparisons |
| `GET` | `/api/v1/logs/stream` | live logs over SSE |

Use Swagger for the exact request/response body of each administrative operation on the checked-out revision. The important policy boundary is stable: administrative mutation is opt-in, evidence surfaces must remain privacy-safe, and missing resource values are not fabricated as zero.

`POST /api/v1/evaluation/runs` accepts the backwards-compatible `retain_content` flag (default `true`) to control generated-output retention in private local history. Prompt and expected value remain attached to the immutable test-set context; when output retention is disabled, local history stores those definitions plus scores, errors and metrics but omits the generated output. The immediate response includes the output for the active local drill-down. The manifest field `content_retained` records whether output was stored. Legacy reports can recover prompt and expected value only when the matching test-set identity is still available. Generic/shareable `report_to_dict` serialization remains content-free unless a trusted caller opts in.

## Failure semantics

Supported product entrypoints validate task/media/capability policy before backend execution when the information is available. Clients should expect explicit HTTP failure for invalid input, unavailable routing/capability, disabled policy or backend failure rather than silent fallback.

Do not build automation by matching free-form backend exception strings. Prefer HTTP status, bounded public error fields where supplied, and explicit capability/identity/status surfaces.

## Which endpoint should an integration use?

Use this decision rule:

```text
Need to generate/transcribe?        -> /v1/* inference endpoint
Need to know what is resident?      -> /v1/models
Need reproducible execution ID?     -> /v1/runtime/identity
Need mutable live runtime activity? -> /status
Need resource/policy/admin state?   -> /api/v1/* with admin enabled
Need exact current schema?          -> /docs
```

For AI Performance Lab specifically, inference, identity and status are three independent contracts; none should be inferred from another.
