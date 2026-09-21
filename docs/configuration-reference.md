# Configuration reference

Status: active
Document type: operational-reference
Owner: runtime configuration
Canonical scope: operations.configuration
Read when: choosing server/runtime settings, diagnosing unexpected effective configuration, or preparing reproducible runs
Last reviewed: 2026-09-21

Local LLM Server resolves runtime configuration from multiple sources. This reference documents the supported precedence and the settings that materially affect serving behavior and execution identity.

## Resolution precedence

Configuration is resolved in this order, from highest to lowest priority:

1. explicit Python kwargs / CLI flags;
2. environment variables;
3. selected model registry `params`;
4. registry `defaults`;
5. hardcoded fallbacks.

A model registry entry may additionally own model-specific identity and capability metadata such as `model_id`, backend, quantization, artifact revision/hash, modalities and features.

The public `/v1/runtime/identity` endpoint exposes only a safe allowlisted effective configuration. It is not a dump of every private source used to build the runtime.

## Core server and inference settings

| Effective key | CLI | Environment | Fallback | Meaning |
| --- | --- | --- | --- | --- |
| `host` | `--host` | `LOCAL_LLM_HOST` | `127.0.0.1` | public bind address |
| `port` | `--port` | `LOCAL_LLM_PORT` | `1235` | public HTTP port |
| `backend` | `--backend` | `LOCAL_LLM_BACKEND` | `llama_cpp` | runtime adapter |
| `ctx_size` | `--ctx-size` | `LOCAL_LLM_CTX_SIZE` | `4096` | context limit used by compatible backends |
| `n_gpu_layers` | `--n-gpu-layers` | `LOCAL_LLM_N_GPU_LAYERS` | `0` | GGUF GPU/offload layer setting where supported |
| `n_threads` | `--n-threads` | `LOCAL_LLM_N_THREADS` | `8` | CPU thread setting where supported |
| `n_batch` | — | `LOCAL_LLM_N_BATCH` | `512` | batch size where supported |
| `n_ubatch` | — | `LOCAL_LLM_N_UBATCH` | `512` | micro-batch size where supported |
| `timeout` | — | `LOCAL_LLM_TIMEOUT` | `1200` | backend/request timeout configuration |
| `startup_timeout` | `--startup-timeout` | `LOCAL_LLM_STARTUP_TIMEOUT` | `60` | managed backend startup timeout |
| `max_concurrent_requests` | `--max-concurrent-requests` | `LOCAL_LLM_MAX_CONCURRENT_REQUESTS` | `1` | per-runtime concurrency limit |
| `max_kv_size` | `--max-kv-size` | `LOCAL_LLM_MAX_KV_SIZE` | `null` | MLX KV-cache bound when supported |
| `chat_format` | `--chat-format` | `LOCAL_LLM_CHAT_FORMAT` | `null` | explicit backend chat template/format override |
| `force_json` | `--force-json/--no-force-json` | `LOCAL_LLM_FORCE_JSON` | `false` | default structured JSON forcing policy |
| `enable_thinking` | `--enable-thinking/--no-enable-thinking` | `LOCAL_LLM_ENABLE_THINKING` | `false` | reasoning/thinking enablement when model supports it |
| `show_thinking` | `--show-thinking/--no-show-thinking` | `LOCAL_LLM_SHOW_THINKING` | `false` | whether reasoning content may be surfaced when supported |
| `verbose` | `--verbose` | `LOCAL_LLM_VERBOSE` | `false` | debug logging |

Boolean environment variables accept common truthy values such as `1`, `true`, `yes` and `on` case-insensitively.

## Request admission and global execution governor

Request admission is explicitly opt-in and is configured independently from per-model `build_config`. Leaving all values unset preserves the existing no-queue/no-global-cap behavior.

| Environment | Fallback | Meaning |
| --- | --- | --- |
| `LOCAL_LLM_REQUEST_QUEUE_CAPACITY` | `null` | bounded priority-aware wait capacity for each resident runtime before execution admission |
| `LOCAL_LLM_QUEUE_TIMEOUT_MS` | `null` | deadline for the complete pre-execution wait; requires a per-runtime queue and/or global governor |
| `LOCAL_LLM_GLOBAL_MAX_RUNNING` | `null` | aggregate number of executions that may hold a global compute slot across resident runtimes |
| `LOCAL_LLM_GLOBAL_QUEUE_CAPACITY` | `null` | bounded aggregate wait capacity for the global execution governor |

`LOCAL_LLM_GLOBAL_MAX_RUNNING` and `LOCAL_LLM_GLOBAL_QUEUE_CAPACITY` must be configured together. The global governor uses priority aging with runtime round-robin as the tie-break/fairness rule and also respects each runtime's `max_concurrent_requests` as an eligibility bound so global slots are not consumed by work that would immediately block on that runtime's semaphore. The runtime semaphore remains the final per-runtime safeguard, and backend-native batching remains backend-owned.

The optional request header `x-local-llm-queue-timeout-ms` overrides `LOCAL_LLM_QUEUE_TIMEOUT_MS` for that HTTP request. The timeout covers the combined pre-execution wait rather than restarting between the per-runtime queue and global governor; it is not an end-to-end inference timeout.

When request admission is enabled, `x-local-llm-workload-class` can classify chat inference as `interactive`, `standard`, `batch` or `background`. Missing headers default to `standard`, preserving the behavior of existing clients. Base priorities are 30, 20, 10 and 0 respectively. Every second spent queued adds one effective priority point; therefore older low-priority work eventually competes with fresh higher-priority work instead of starving indefinitely. Equal effective priority preserves FIFO inside one runtime and uses runtime round-robin globally. Invalid workload classes fail with HTTP 400 before backend execution.

The response echoes the validated class in `x-local-llm-workload-class`. Workload priority changes only pre-execution admission; it does not preempt already-running inference, change backend-native batching or reserve additional memory.

Transient request memory is reserved only after execution admission, so queued requests do not claim transient RAM. When the global governor is configured, first-class chat/vision HTTP execution, resident transcription and evaluation samples share the same aggregate execution owner. Global admission does not create a second memory budget or enable automatic pressure eviction.

## Default generation settings

These defaults may be supplied through registry parameters or environment variables and are part of the resolved serving configuration when relevant:

| Key | Environment | Fallback |
| --- | --- | --- |
| `default_temperature` | `LOCAL_LLM_DEFAULT_TEMPERATURE` | `0.0` |
| `default_top_p` | `LOCAL_LLM_DEFAULT_TOP_P` | `0.8` |
| `default_top_k` | `LOCAL_LLM_DEFAULT_TOP_K` | `20` |
| `default_min_p` | `LOCAL_LLM_DEFAULT_MIN_P` | `0.0` |
| `default_repeat_penalty` | `LOCAL_LLM_DEFAULT_REPEAT_PENALTY` | `1.0` |

Request-level OpenAI-compatible generation values can still override applicable defaults for an individual inference request.

## Managed backend settings

| Key | CLI | Environment | Fallback |
| --- | --- | --- | --- |
| `llama_server_port` | `--llama-server-port` | `LOCAL_LLM_SERVER_PORT` | `8091` |
| `llama_server_bin` | `--llama-server-bin` | `LOCAL_LLM_SERVER_BIN` | `null` |
| `mlx_vlm_server_port` | `--mlx-vlm-server-port` | `LOCAL_LLM_MLX_VLM_SERVER_PORT` | `8092` |
| `mmproj_path` | `--mmproj-path` | — | `null` |

Paths and executable locations are private deployment details and are intentionally excluded from the public execution-identity response.

## Security-sensitive settings

| Key | Environment | Fallback | Rule |
| --- | --- | --- | --- |
| `trust_remote_code` | `LOCAL_LLM_TRUST_REMOTE_CODE` | `false` | enable only for an explicitly trusted model/source |
| `allow_remote_media` | `LOCAL_LLM_ALLOW_REMOTE_MEDIA` | `false` | remote HTTP(S) media remains fail-closed by default |

CORS and the administrative API are controlled at server startup rather than through `build_config`:

```bash
local-llm serve \
  --model nemotron-nano-4b-q8 \
  --enable-admin-api \
  --cors-origin http://127.0.0.1:3000
```

`--cors-origin` is repeatable. CORS is disabled by default. Enabling the admin API does not add authentication.

## Model selection and residency

The main selection options are:

```text
--model <registry-key>          start/select one configured model
--models <key> [<key> ...]     keep multiple runtimes resident
--default-model <key>          default route when request model is omitted
--model-path <path-or-ref>      direct model source override
--no-download                  fail rather than fetch a missing artifact
```

`--model-path` changes the runtime source but does not justify inventing missing revision, hash or quantization metadata. For evidence-grade identity, provide explicit owned metadata rather than relying on path/filename parsing.

## Identity metadata

The resolved configuration can carry the following identity evidence when supplied explicitly or through the registry:

- `quantization`;
- `artifact_sha256` / registry `sha256`;
- `artifact_revision` / registry `revision` / `hf_revision`;
- backend and resolvable backend version;
- allowlisted effective serving settings used to compute `runtime.config_digest`.

Missing identity remains missing. Local LLM Server does not claim a verified fingerprint simply because a filename happens to contain a quantization label.

See [`runtime-identity-api.md`](runtime-identity-api.md) for the public representation.

## Thinking-mode validation

Model registry metadata controls whether thinking is unsupported, always enabled or switchable. Invalid explicit combinations fail during configuration resolution rather than being silently ignored. Examples include trying to enable thinking for a model declared as non-thinking, or trying to disable it for a model declared as always-thinking.

## Reproducibility guidance

For comparisons or AI Performance Lab evidence campaigns, record at minimum:

- registry/model key and effective model ID;
- backend and backend version when available;
- artifact revision/hash/quantization when available;
- `/v1/runtime/identity` payload;
- relevant CLI/environment/registry settings;
- server package revision/version;
- hardware identity actually observed.

Do not treat two runs as the same execution configuration merely because the public model name matches.

## Inspecting the effective configuration safely

Use:

```bash
curl http://127.0.0.1:1235/v1/runtime/identity
```

The `runtime.config` object is generated from the same non-sensitive allowlist covered by `runtime.config_digest`. Private paths, download URLs and credentials are excluded by design.