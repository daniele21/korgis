# llama.cpp v1.0.0 — Korgis Integration

Status: active
Owner: Korgis managed llama-server/runtime, resource admission and runtime identity
Read when: integrating llama.cpp v1, reviewing compatibility, or planning Apple Silicon runtime validation
Last reviewed: 2026-11-13

## Goal

Make the stable upstream llama.cpp **v1.0.0** a verified, selectively usable Korgis managed llama-server backend, preserving the application-facing APIs, explicit runtime ownership, conservative resource admission, cleanup, privacy and existing models. Expose selected v1 runtime optimizations only behind tested, opt-in capabilities.

The successful outcome is a tested Korgis → external llama-server → GGUF model path that can be configured to use a pinned v1 binary without changing consumers, with a reversible upgrade and separate physical-device evidence.

## Source/target identity and decision

- Baseline for this plan: Korgis **dev** at `ddca60d7e4abb02597d0923cd6d21ec729e3e0c7` (2026-11-04); refresh live base before work.
- Stable upstream target: **ggml-org/llama.cpp v1.0.0**, tag SHA `2d8a32502909ca065c41c4cf5f2fb1c427c047fc`, published 2026-11-13. The matching nightly commit is b12360; upstream release notes link downloadable assets through b12359, so do not assume those assets reproduce the exact tag. Prefer a checksum/commit-attributed build of the precise v1 tag.
- The newer b12368 nightly existed on 2026-11-13 but is a **pre-release**; testing it is a separate experimental lane, not the stable default.
- Official release: https://github.com/ggml-org/llama.cpp/releases/tag/v1.0.0
- Runtime adoption policy: Korgis continues to select an *external* binary explicitly or by validated discovery; it does not silently download, replace or downgrade specialist runtimes. Preserve `LOCAL_LLM_SERVER_BIN` explicit authority and the PrismML model-specific fork requirement.

## Product impact

PRODUCT_LOCAL for the default upgrade: application clients and supported outcomes remain unchanged. Treat opt-in FP8/MoE features as separate scoped feature increments if material consumer behavior is introduced. User: application developers running GGUF models through Korgis. Job: dependable local inferencing and controlled multi-model memory behavior on Apple Silicon and other supported hosts.

Success means: compatible startup/inference/streaming/structured results, no leaked subprocess/listeners, correct admission/accounting and stable public runtime identity, before making any performance claim.

Non-goals:
- Reimplementing llama.cpp scheduler, KV internals, model tensor execution or ggml optimizations in Korgis.
- Automatically enabling MoE caching, FP8, SSD expert streaming, eviction or speculative decoding on existing profiles.
- Migrating `llama-cpp-python` native ABI merely because the external llama-server is upgraded.
- Adopting the redesigned upstream llama.cpp UI, adding its TTS endpoint to Korgis or changing public APIs in this workstream.
- Claiming lower memory use, successful swap reclamation or a performance gain without representative measurements.
- Moving external endpoint evaluation, comparisons or regressions into Korgis; Performance Lab owns those.

## Current state and compatibility hazards

1. Korgis `src/local_llm_server/llama_server_compat.py` currently labels builds above the v0.3 floor as `forward-compatible-v0.3`. It parses an attributable build/commit and emits CLI options that must be checked against v1; a numeric minimum alone is not a v1 validation.
2. Korgis supports managed `llama_server` subprocesses and also has an independent `llama_cpp` path via `llama-cpp-python>=0.3.0`. The external binary upgrade must not be conflated with the Python binding/package.
3. Dev adds experimental SSD MoE streaming with `--moe-stream-cache` and `--moe-stream-io-threads`, opt-in, with fail-closed help probing. Upstream v1 exposes a **different** MoE host expert cache (`--moe-cache-mib`, `--moe-cache-cpu-share`). Keep these capabilities distinct; if the v1 executable does not support the SSD flags, reject only that opt-in profile without silently ignoring it.
4. Upstream v1 has KV `f8_e4m3` (K/V) support, new MoE cache, security hardening and removal of deprecated C APIs / `--defrag-thold`. Verify CLI and model/hardware support before advertising features.
5. Korgis `memory_envelope.py` presently estimates weights, cache, backend overhead and existing experimental SSD stream cache conservatively. Do not discount GGUF model-file weight on theoretical cache savings or equate CPU-resident weights with extra physical RAM on Apple Silicon.
6. macOS pressure/swap observation and conservative blocking exist; auto pressure eviction remains disabled pending real evidence. A high swap-used counter is not by itself either proof of current pressure or an admission credit.
7. Loopback and local-first defaults remain the security boundary; Korgis is not an authenticated remote inference service.

## Invariants to preserve

- Artifact != configured model != resident runtime != default route.
- Model compatibility and backend executable are explicitly attributable; unsupported flags fail before process spawn.
- Process tree, ports, leases, requests/streams and shutdown retain one canonical owner and bounded cleanup, including error/cancellation paths.
- Measured, estimated, configured and unavailable evidence never collapse; path/secret/prompt/output data do not leak into public identity or portable reports.
- Memory headroom and global governor stay conservative; auto eviction disabled; managed backend doesn't oversubscribe runtime-local batching.
- No silent cloud/remote-media access, no weaker CORS, SSRF or tooling defaults, no opportunistic substitution of a different model or a specialist fork.
- Published validation is bound to exact head/tree/base and runtime binary source revision, checksum, model/quantization, context and Apple Silicon host configuration.

## Work graph and parallel writing boundaries

| ID | Observable work | Canonical write boundary | Depends | Parallel | State |
| --- | --- | --- | --- | --- | --- |
| LC1 | Record exact upstream v1 binary identity, supported CLI options and compatibility matrix vs dev's managed v0.3 profile; identify removed or changed flags and model-specific requirements | test evidence / temporary inventory; no production writes | — | yes | READY |
| LC2 | Add opt-in, explicit **verified-v1** compatibility profile; parse semver/build/commit without degrading existing profiles; validated feature-flag map, pinning/discovery and fail-closed command assembly | `llama_server_compat.py`, `tests/test_llama_server_compat*.py`, `tests/test_llama_server_config.py` | LC1 | no | BLOCKED |
| LC3 | Prove subprocess startup, health, text/vision requests, streaming, cancellation, timeout, unload, process-tree cleanup and resource-lease integrity with a v1 executable; preserve the PrismML path | `engine.py`, `process.py`, lifecycle/protocol tests only when change is required | LC2 | yes | BLOCKED |
| LC4 | Add opt-in **FP8 KV cache** and **v1 MoE host cache** only when runtime advertises and model/host admits them; keep SSD MoE streaming separate; incorporate conservative memory envelopes, status and identity | `memory_envelope.py`, resource/identity contracts and associated tests. CLI flag wiring is owned by LC2 to prevent concurrent edits | LC2 | yes | BLOCKED |
| LC5 | Verify Korgis API/security parity: OpenAI text interface, JSON/schema/tool responses, stream disconnect, CORS/SSRF/private URL defaults and API cancellation; add targeted regression tests where needed | product composition / HTTP policy and API/contract tests | LC2 | yes | BLOCKED |
| LC6 | Decide explicitly whether the independent `llama-cpp-python` backend can remain pinned; evaluate ABI/API and locking/build implications only if a real compatibility or packaging requirement demands an update | `pyproject.toml`, `uv.lock`, native-binding adapter/tests (only if needed) | LC1 | yes | BLOCKED |
| LC7 | Validate on representative Apple Silicon and hand off external model-endpoint comparison to Performance Lab, keeping execution fingerprint and evidence provenance separate | Korgis real-device evidence/runbook; Performance Lab evaluation is owned in its separate repository | LC3-LC6 | no | BLOCKED |
| LC8 | Execute exact-head integration preflight, affected E2E, documentation sync and reversible release; confirm FULL + applicable real-environment gates before dev -> main promotion | affected config/API/runtime docs, `docs/current-state.md`, CI/evidence, release packaging | LC3-LC7 | no | BLOCKED |

Start with **LC1**, then converge on **LC2** so LC3–LC5 can proceed with distinct write ownership. LC6 can independently audit the Python binding after LC1; do not touch dependency locks speculatively.

## Executable first slice: LC1

Input:
- Korgis dev and its tests/registry/config consumers.
- Exact llama.cpp v1.0.0 tag and its `llama-server --version` and `--help` output from a reproducibly identified executable.
- Existing validated v0.3 binary for baseline.

Acceptance:
1. Capture a path-free binary identity: source tag+SHA, binary SHA-256, effective build/commit, OS/arch/compiler where known.
2. Compare every Korgis-emitted flag (parallel, batching, unified KV, gpu layers, load mode, fit, threads, flash attention, cache K/V/RAM and experimental SSD MoE) with v1 help and an actual bounded dry startup.
3. Confirm how the v1 CLI exposes `--moe-cache-mib`, `--moe-cache-cpu-share` and `f8_e4m3`; identify exact validation constraints and what requires hardware proof.
4. Verify source/build distribution: semver release metadata vs b12359 binary assets vs b12360 matching commit. No download by Korgis at runtime.
5. Write an actionable compatibility matrix; keep v1 default inactive until LC2-LC3 prove parity. Falsifiable outcome: unsupported flags and probes produce explicit rejection before starting model inference.

## Acceptance and testing

**Deterministic contract/compatibility**
- Tests for v1 `--version` parsing, exact SHA attribution, semantic v1 vs v0.3 vs experimental and absent/unparseable binaries.
- Golden CLI command tests for v1 and legacy, plus negative cases for removed, unsupported, conflicting flags; explicit paths are authoritative.
- Unit/service tests: cancellation and streaming ownership through final byte; server and subprocess stop after normal/in-flight/error/failed-start paths; ports released, budget/leases correct.
- Resource tests for FP8 and MoE opt-ins, incomplete telemetry, conservative cache accounting, SSD flag behavior, low-memory rejection, swap/pressure handling.
- Security tests for local-only defaults, CORS, remote media, SSRF-sensitive inputs and tool/JSON failures; no unreviewed expansion of trusted backends.
- Representative model cases: existing small GGUF Qwen3.5/Spark for text; vision GGUF if supported; MoE model specifically for optional MoE cache. Never require actual model downloads in ordinary CI.

**Repository-owned validation**
- Start with affected tests, e.g. `uv run --frozen pytest tests/test_llama_server_compat.py tests/test_llama_server_compat_cache.py tests/test_engine_llama_server.py tests/test_engine_llama_server_v030.py tests/test_memory_envelope.py tests/test_moe_ssd_streaming.py tests/test_resource_policy.py tests/test_resources_macos.py -q` (adapt paths to current tree).
- Determine required profile/gates from `python3 scripts/select_validation_profile.py --base-ref <base> --head-ref <head>` and `.engineering/commands.json`; shared runtime/security/lifecycle is at least STRONG, dependency/lock/build and release can require FULL.
- Before PR integration into dev: exact-head required automated gates and affected critical API/Studio E2E (with residue verification); use repository-owned remote automation where local execution is unavailable. A draft PR does not claim readiness.
- Before dev -> main release: FULL, source/build identity, supported platform install/packaging checks, production model smoke and mandatory **REAL_ENVIRONMENT** Apple Silicon evidence. Never treat CI fakes as Mac proof.

**Representative Apple Silicon campaign**
- Capture baseline and v1 on the same Mac, same GGUF artifact/checksum, quantization, prompt/dataset, context, thread/gpu configuration and thermal/load conditions.
- Record load time, TTFT, prompt processing, decode tokens/s, output contract/quality, max resident/peak memory, available RAM, swap in/out, pressure, CPU/GPU use when attainable, unload/reload deltas, 2-model overlap and shutdown under load.
- Compare (a) default FP16/legacy cache, (b) FP8 KV opt-in, (c) MoE host-cache opt-in and, separately, (d) experimental SSD streaming only where supported. Preserve comparability and source provenance.
- Korgis owns runtime observations, safety and lifecycle. **Performance Lab** owns externally served endpoint evaluation, evidence, comparison and regression verdicts (https://github.com/daniele21/performance-lab). Do not copy its functionality to Korgis.
- Do not assert reclamation/swap safety or automatic eviction effectiveness without matching evidence.

## Rollback and adoption

- v1 is an *opt-in compatible binary/profile* initially; keep the previously validated external binary configurable by explicit path. Do not silently auto-downgrade to a different engine or fork on load failure.
- Roll back by selecting the prior attributable binary and disabling v1 feature flags, then verifying startup/unload/identity on the same stable application API.
- Promote v1 as recommended/default discovery only after deterministic regressions and representative Mac gates pass; mark v1-specific options unsupported on other backends.
- Binary provenance and feature support should be shown in existing path-free runtime identity/capability diagnostics; UI redesign is out of scope.

## Risks and decision gates

| Risk | Mitigation / gate |
| --- | --- |
| v1 breaks CLI semantics despite higher build number | LC1 explicit option matrix; LC2 verified-v1 profile and negative flag tests |
| Python binding breaks from native API removals | Keep v1 external subprocess integration separate; LC6 decide whether any Python update is needed |
| v1 MoE cache conflated with experimental SSD streaming | Separate opt-ins, flags, accounting and model/device evidence |
| FP8 changes numerical output or unsupported on host/model | Capability gate and compatibility/quality tests; no default enablement |
| Unbounded memory/swap or orphan llama-server instances | Existing global admission, timeout, in-flight shutdown and real pressure campaign |
| Upstream server expands remote/tool access | Explicit Korgis loopback and deny-by-default policy, SSRF/CORS/tool regression |
| Benchmark identity leaks private paths or compares mismatched runs | Existing path-free identity + Performance Lab execution fingerprints and compatibility gates |
| Released tag is confused with nightly asset build | Verify tag commit and exact binary digest before any claim |

## Integration points and next actions

- **LC1 -> LC2:** agree a typed version+capability inventory and CLI behavior; avoid a second owner for process discovery or runtime configuration.
- **LC2 -> LC4:** LC2 owns CLI flag assembly; LC4 owns resource/identity contracts. Coordinate config key definitions before parallel edits.
- **LC3/LC5 -> LC7:** generate stable endpoint and runtime identities that Performance Lab can consume, without owning comparison in Korgis.
- **LC8:** update affected configuration/getting-started/runtime-identity and current-state owners only when behavior is integrated, not merely designed.

## Resume checkpoint

- Planned source: Korgis dev `ddca60d7e4abb02597d0923cd6d21ec729e3e0c7`; upstream stable tag `v1.0.0` at `2d8a32502909ca065c41c4cf5f2fb1c427c047fc`.
- Verified from source: v0.3 compatibility floor and v0.3 forward-profile; experimental dev SSD cache; conservative memory envelope and swap/pressure observer; independent llama-cpp-python dependency.
- **Not verified:** actual v1 binary invocation on target Mac, Mac memory/speed effects, full v1 CLI parity, which v1 feature flags are supported in the selected distribution.
- Next discriminating action: LC1 version/help/dry-start matrix against a commit-attributed v1 binary. Refresh Korgis dev head/tree and target base when resuming.

## Completion

Mark tasks DONE only with attributable evidence. Full completion requires integrated code, consumers, lifecycle and security contracts, model tests, required exact-head automated checks, current durable docs and applicable representative Mac evidence. Then transfer durable truth into canonical docs and remove this temporary workstream plan by default, per Korgis policy.
