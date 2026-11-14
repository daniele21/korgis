# Troubleshooting

Status: active
Document type: operational-guide
Owner: developer experience
Canonical scope: operations.troubleshooting
Read when: startup, routing, capability, identity, telemetry, or local integration does not behave as expected
Last reviewed: 2026-08-15

Use this guide to diagnose supported product-stack failures without weakening the server's privacy or evidence guarantees.

## Start with the four basic checks

Run these before changing configuration:

```bash
curl http://127.0.0.1:1235/health
curl http://127.0.0.1:1235/v1/models
curl http://127.0.0.1:1235/v1/runtime/identity
curl http://127.0.0.1:1235/status
```

They answer four different questions: process health, residency, stable identity and dynamic activity.

## `local-llm serve` cannot find the model

Check the registry first:

```bash
local-llm models
```

If the entry exists but is not local, either download it:

```bash
local-llm download <model-key>
```

or allow normal serving to resolve/download it. If you intentionally require an offline/local-only artifact path, use `--no-download`; a missing artifact should then fail explicitly rather than silently fetching.

For a direct source, verify `--model-path` matches the selected backend's expected source type.

## Server is healthy but `/v1/models` is empty

A healthy server is allowed to have zero resident models. Health means the product stack is responding, not that a runtime is loaded.

Start or load a model, or use the administrative load/activate endpoints when the admin API is enabled.

## Request names a model but inference fails

Local LLM Server does not silently substitute another model. Confirm that the requested `model` exactly matches a resident route visible in `/v1/models`, and check whether the runtime is ready in `/status`.

If multiple models are resident, verify `--default-model` only when the client intentionally omits the `model` field.

## Wrong backend or unexpected runtime settings

Configuration precedence is:

```text
CLI / explicit kwargs
  > environment variables
  > model registry params
  > registry defaults
  > hardcoded fallbacks
```

Inspect [`configuration-reference.md`](configuration-reference.md), then check `/v1/runtime/identity`. Its `runtime.config` is the safe effective allowlist associated with `runtime.config_digest`.

Remember that private path/source settings are intentionally absent from the public identity response.

## `/v1/runtime/identity` returns `partial` evidence

`partial` is not an error. It means useful runtime identity is available but the stronger evidence required for a verified runtime fingerprint is incomplete.

Typical missing inputs are:

- verified artifact SHA-256;
- explicit artifact revision;
- resolvable backend version;
- hardware fields that the current observer cannot measure reliably.

Do not repair partial identity by parsing filenames. Add explicit registry/config metadata or improve the owned evidence source.

## Quantization, revision or artifact hash is `null`

Those values are emitted only when explicitly known. Add them to the model registry or controlled explicit configuration where appropriate. The server intentionally refuses to infer quantization/revision/hash from a path or filename.

## AI Performance Lab says required identity discovery failed

Confirm:

```bash
curl http://127.0.0.1:1235/v1/runtime/identity
```

and verify that Performance Lab uses the server root, not the OpenAI `/v1/` base, for its Local LLM Server identity configuration.

Typical split:

```text
inference base_url -> http://127.0.0.1:1235/v1/
identity base_url  -> http://127.0.0.1:1235
telemetry base_url -> http://127.0.0.1:1235
```

If Performance Lab sets `required: true`, absence or invalidity of the identity endpoint is intentionally fatal before the fingerprint is frozen.

## `/status` does not show a short inference phase

Polling is observational. A short prefill/prompt-eval phase can occur entirely between samples. Increase polling frequency only when the added collector overhead is acceptable and keep the same sampling protocol when comparing runs.

Never treat an unobserved phase as proof that the phase did not occur.

## `chunks_per_second` differs from expected token throughput

This is expected. Runtime chunks are not model tokens. Use OpenAI `usage`, backend-native token evidence, or Performance Lab token-aware measurement when available. Do not convert chunk rate into token rate.

## Remote media request is rejected

Remote HTTP(S) media is disabled by default. This is a privacy policy, not a transport bug.

Enable `LOCAL_LLM_ALLOW_REMOTE_MEDIA=true` only when remote fetching is an explicit trusted deployment decision. Prefer local/multipart media for privacy-sensitive workflows.

## Remote model/tokenizer code is rejected

`trust_remote_code` defaults to false. Enable `LOCAL_LLM_TRUST_REMOTE_CODE=true` only for an explicitly trusted source and understand that it expands the code-execution trust boundary.

## Thinking/reasoning flags fail during startup

The registry defines whether a model's thinking mode is unsupported, always-on or switchable. Invalid combinations fail explicitly. Do not expect `--enable-thinking` or `--no-enable-thinking` to override model capability truth.

## Admin endpoints return 404/unavailable

Start the server with:

```bash
local-llm serve --model <model> --enable-admin-api
```

Administrative APIs are intentionally opt-in. Enabling them does not add authentication; do not expose them to an untrusted network.

## Browser frontend is blocked by CORS

CORS is disabled by default. Add allowed origins explicitly:

```bash
local-llm serve \
  --model <model> \
  --cors-origin http://127.0.0.1:3000
```

Repeat `--cors-origin` for multiple trusted origins. Do not use a broad origin merely to bypass a local frontend configuration problem.

## Port already in use

Change the public server port:

```bash
local-llm serve --model <model> --port 1236
```

Managed backends also have internal ports such as the llama-server and MLX-VLM server ports. If those collide, change the corresponding backend port option rather than only the public HTTP port.

## Backend import/binary is missing

Install the backend-specific extra/dependency required by the selected runtime. The core package does not make every specialist engine available on every platform.

Examples:

```bash
pip install ".[mlx]"
pip install ".[vision]"
pip install ".[audio]"
```

For an externally managed `llama-server`, verify the configured binary and its version independently.


### Pinned llama.cpp v0.3 release reports build 1

An exact-source llama.cpp `v0.3.0` Metal binary built from a shallow Git
checkout can print `version: 0.3.0 (build 1, commit c1d0e7a)` rather
than the full-history build number `10621`. Korgis recognizes this
**specific attributable release commit** as its validated v0.3 profile
without rewriting the reported build identity. A random build 1, a
missing commit or an unrecognized commit remains unsupported.

Use an explicit `--llama-server-bin` selection, verify the installed
binary's SHA-256 and upstream source provenance, and retain its actual
`--version` output. Do not set the unvalidated-runtime override or
relax host memory admission just to make a comparison run.

## First request is much slower than later requests

Cold load, backend startup, cache initialization and prompt/prefill work are distinct from warm decode performance. Do not compare a cold first request to a warm baseline without recording the classification.

Use Performance Lab's cold/warm and repeatability/load protocols for controlled measurement rather than a single manual request.

## Hardware or memory values are unavailable

Unavailable means the current observer does not have a trustworthy measurement source on that platform/runtime. It is not zero.

Do not fill missing hardware/resource evidence manually unless it comes from an explicit controlled source. Hardware-dependent claims require representative evidence outside deterministic CI.

## Worker reclamation review is inconclusive

That is a valid result. The evidence workflow deliberately preserves states such as `mixed`, `insufficient` or inconclusive rather than producing an automatic safety verdict.

Run more compatible cycles/reports on representative hardware and review the compatibility reasons before changing runtime/eviction policy.

## Need the exact request schema

Use:

```text
http://127.0.0.1:1235/docs
```

Swagger is the executable API schema for the checked-out revision. Use [`http-api-reference.md`](http-api-reference.md) for endpoint meaning and policy boundaries, and Swagger for exact request/response validation details.

## Still diagnosing

Collect only privacy-safe facts:

- command/flags excluding secrets/private paths where unnecessary;
- backend and package version;
- model registry key and public model ID;
- `/v1/runtime/identity` response;
- relevant `/status` state;
- bounded public error code/status;
- platform class and reproducible steps.

Do not paste credentials, private model paths, prompts, model output or sensitive media merely to diagnose an infrastructure issue.