# llama.cpp v1.0.0 integration

Status: active
Owner: Korgis managed llama-server/runtime, resource admission and runtime identity
Read when: implementing or reviewing llama.cpp v1 upgrade or Apple Silicon proof

## Outcome

Allow Korgis to run attributable, externally installed llama.cpp v1.0.0
through its **existing** managed llama-server boundary, without breaking public
API, memory admission, runtime lifecycle or supported models. Make new FP8 KV
and host MoE-cache optimizations optional and fail closed.

Baseline: Korgis `dev` `ddca60d7e4abb02597d0923cd6d21ec729e3e0c7`;
refresh on resume. Target: `ggml-org/llama.cpp` tag `v1.0.0` at
`2d8a32502909ca065c41c4cf5f2fb1c427c047fc` (build b12360),
released 2026-11-13.
https://github.com/ggml-org/llama.cpp/releases/tag/v1.0.0.
Later nightlies are *not* automatically accepted as this tag.
Upstream release links b12359 assets, which are not an exact-tag proof:
verify source and binary SHA/build before claiming tested v1.

## Product boundary and exclusions

PRODUCT_LOCAL: existing app developers retain OpenAI-compatible Korgis API and
explicit GGUF runtime control, with safer version selection and optional
v1 settings. Korgis owns serving, processes, admission, identity, security;
Performance Lab owns externally served endpoint evaluation/evidence/comparison
(https://github.com/daniele21/performance-lab).

Do not replace the specialist inference engine, introduce a second process or
resource manager, auto-enable optimizations, accept remote access by default,
automatically switch Python native ABI, or import upstream llama.cpp UI/TTS.
The separately pinned `llama-cpp-python` path must be assessed independently.
Preserve PrismML's model-specific executable selection.

## Invariants

- Model artifact, configuration, resident runtime and default routing remain
  distinct. Explicit binary path wins; unverified flags fail before launch.
- Runtime owner cleans process tree, listeners, leases and streams, including
  interrupted requests, failed startups, unload/reload and shutdown.
- Cache/weight estimates remain conservative; Apple Silicon CPU/GPU share RAM.
  Measured, estimated and unavailable are not conflated; swap use is not
  headroom credit. Auto pressure eviction remains disabled pending proof.
- No remote media or unsafe tools by default; no path, secret, raw prompt or
  output appears in public identity/evidence.
- Source/tag/binary SHA, model digest, context, hardware, head/tree/base and
  E2E environment stay attributable to every claim.

## Work graph

| ID | Outcome and owner | Depends on | Status |
| --- | --- | --- | --- |
| LC1 | Exact upstream binary/help/version/flag matrix (evidence, no runtime writes) | — | ACTIVE |
| LC2 | Attributable v1 profile, fail-closed CLI and option helper: `llama_server_compat.py`, `llama_server_v1_options.py`, adapter tests | LC1 source review | ACTIVE |
| LC3 | Engine, process lifecycle, streaming/cancellation and cleanup tests: `engine.py`, `process.py` if justified | LC2 | BLOCKED |
| LC4 | FP8/MoE host cache options, conservative envelope and identity: `config.py`, `memory_envelope.py`, `runtime_identity.py`, tests | LC2 interface | ACTIVE |
| LC5 | API and security regressions: HTTP/product policy and direct tests only | LC2 | BLOCKED |
| LC6 | Separate Python-binding ABI/package compatibility decision; no speculative dependency bump | LC1 | READY |
| LC7 | Matched Apple Silicon execution + Performance Lab endpoint regression evidence | LC3–LC6 | BLOCKED |
| LC8 | Exact-head preflight, docs, rollback and dev/main promotion gates | LC3–LC7 | BLOCKED |

Parallel LC3 (lifecycle), LC4 (memory/identity), LC5 (API/security) and LC6
(Python dependency audit) have separate write ownership after LC2 interface
converges. **LC2** exclusively owns common CLI assembly; **LC4** owns resource
estimates and runtime identity. Do not duplicate option policy in two modules.

## Current executable slice

LC1 + LC2 + LC4 initial deterministic slice:
- From upstream `common/arg.cpp` v1 tag, confirm
  `--moe-cache-mib`, `--moe-cache-cpu-share` and FP8 `f8_e4m3` KV type;
  verify against actual `llama-server --version` and `--help` before marking
  LC1 DONE. Record removed/changed old CLI options; no `--defrag-thold`.
- Attribute v1 by build+commit (not monotonic build number alone); preserve
  older v0.3 profile and model-specific forks. v1-only feature inputs must be
  validated and supported by chosen executable, off by default.
- MoE host-cache and experimental SSD `--moe-stream-cache` are **distinct**
  incompatible options. No theoretical memory discount or implicit CPU
  memory credit. FP8 output fidelity remains to be checked on real models.

Tests: `tests/test_llama_server_v1.py`,
`tests/test_llama_server_compat.py`,
`tests/test_llama_server_config.py`,
`tests/test_moe_ssd_streaming.py`,
`tests/test_memory_envelope.py` and identity/resource/lifecycle consumers.
Use repository `scripts/select_validation_profile.py` and
`.engineering/commands.json`; STRONG minimum for runtime/lifecycle/security,
FULL if lock/build/selector or release affected.

## Integration, real evidence and rollback

At PR -> dev: current head/tree/base, full diff, affected docs, required
automated gates and API/Studio E2E, zero residue. Deterministic missing gates
run through repository-owned remote CI. Apple Silicon confirmation is
DEFERRED_TO_RELEASE, not relabelled as deterministic PASS.

At dev -> main: FULL and applicable REAL_ENVIRONMENT evidence for production
GGUF/macOS memory. Same Mac/model SHA/quantization/context/prompt, old vs new
binary; measure load time, TTFT, prefill, tokens/s, resident/peak memory, swap
activity/pressure, runtime overlap, errors, unload/reclamation and shutdown.
Distinguish normal/FP8/MoE cache/experimental SSD cases. Performance Lab owns
comparable endpoint evaluation; Korgis owns lifecycle/resource evidence.
No unmeasured memory/speed/reclamation claim.

Rollback selects the **previous attributable** external binary explicitly,
disables v1 flags, verifies health/inference/unload and unchanged app API.
Never silently replace the user's chosen executable. Docs:
`docs/configuration-reference.md`, `docs/runtime-identity-api.md`,
`docs/device-evidence-runbook.md` and `docs/current-state.md` only when
their contract/state actually changes.

## Resume checkpoint

At workstream creation, source inspection confirmed existing v0.3 floor,
experimental SSD MoE flags, native Python backend split and conservative swap
policy. A draft v1 adapter/options/envelope implementation is under active CI;
**no actual v1 GGUF inference, Mac benchmark, successful v1 CLI dry-run or safe
reclamation has yet been proven**. Refresh branch identity and inspect CI logs
before advancing. Next discriminating action: exact-tag executable flag probe,
then lifecycle tests on an actual v1 server.

Complete only when code, contracts, consumers, docs, exact-head CI and required
real-device evidence agree. Transfer durable truth and delete this workstream
per repository policy; do not retain duplicate plans.
