# llama.cpp v1.0.0 integration

Status: active
Owner: Korgis managed llama-server/runtime, resource admission and runtime identity
Last reviewed: 2026-11-14
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
| LC1 | Exact-tag binary compiled; version/CLI smoke PASS (no GGUF) | — | DONE |
| LC2 | Attributed profile, fail-closed CLI and separate v1 options module; deterministic tests PASS | LC1 | DONE |
| LC3 | Existing lifecycle owner preserved; fake-process startup/unload/error, streaming/API E2E PASS | LC2 | DONE |
| LC4 | Typed FP8/MoE opt-ins, conservative envelope/identity and invalid-mode tests PASS | LC2 | DONE |
| LC5 | Existing HTTP/security defaults preserved; FULL CI and API/Studio E2E PASS | LC2 | DONE |
| LC6 | Python binding 0.3.36 independently locked and Mac-native source built; lifecycle/CI PASS | LC1 | DONE |
| LC7 | Representative Apple Silicon GGUF/runtime/pressure + Performance Lab endpoint evidence | LC3–LC6 | BLOCKED |
| LC8 | Exact-head FULL preflight merged PR #262 to dev; release blocked by LC7 | LC1–LC5 | DONE |

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

## Follow-up: reproducible runtime installation and Python refresh

PR #262 merged to dev at `f0bc76e44d4d7ac04cefc8e64a73756c2dc4b7ce`,
with source and resulting merge trees verified identical.
Next slice installs exact-tag Metal builds only as an **explicit operator
action**: stage a versioned binary with native libraries, reject
overwrites, verify commit/CLI/sha256, preserve previous selection.
The macOS binary links internal libraries statically, then moves the complete
build/bin tree before checking its CLI: this catches non-relocatable macOS
`@rpath` dependencies. The pinned source build explicitly disables upstream Web UI downloads
(`LLAMA_BUILD_UI=OFF`, `LLAMA_USE_PREBUILT_UI=OFF`): the exact-tag build
otherwise falls back to moving `latest` assets, breaking reproducibility.
Hosted macOS arm64 deterministic build is required; it does not satisfy
the representative real-GGUF/pressure/reclamation gate LC7.

GitHub-hosted macOS arm64 uses workflow label `macos-15` (not the image name
`macos-15-arm64`). Installation takes an atomic root lock and refuses
concurrent operations; an unclean interruption leaves a fail-closed lock.

Latest PyPI `llama-cpp-python` on 2026-11-14 is 0.3.36; its bundled
llama.cpp revision `0c1e57098` predates upstream v1.0.0.
Only the independent in-process Python backend receives this binding
update. Check `uv lock --check`, Python 3.10–3.12, package smoke and
backend lifecycle compatibility before acceptance; no C ABI mixing.

## Self-hosted Mac native qualification

The source-owned `.github/ci/local-test.sh` ran on the existing trusted
`daniele21/local-runner-infra` Apple Silicon fleet at Korgis dev commit
`5e92604c5ca955afacc2c16c64e4ff6e00d489f7`:
[central run #43599039636](https://github.com/daniele21/local-runner-infra/actions/runs/43599039636),
`local-ci/mac=success`, **45 owner tests PASS**, exact upstream v1 Metal
binary and source-built Python 0.3.36, binary SHA-256 attributable. An earlier
Mac run failed before compilation because CMake was absent; verified portable
CMake bootstrap fixed this environment/toolchain failure.

The approved one-shot, nonactivating side-by-side installation on the Mac was
verified in [central run #43600645829](https://github.com/daniele21/local-runner-infra/actions/runs/43600645829),
**PASS**. The versioned binary's digest was
`edcbed3bee42714afb517717d75d047b241ede0cd1c304c7ef3f8f696ace7efc`;
Python 0.3.36 was installed in a separate noneditable environment.
The private runner retains `verification.json`; the path-free GitHub job
summary verifies source/binary identity, nonactivation and explicit unmeasured
claims. Earlier [stage run #43600231046](https://github.com/daniele21/local-runner-infra/actions/runs/43600231046)
finished native installation but failed only at an artifact upload because
GitHub Actions storage quota was exhausted; replaced with fail-closed receipt
verification, not with a weakened acceptance check.

**Unproven:** representative GGUF inference, Mac memory/swap/reclamation,
multi-runtime pressure, model quality and matched old-v1 performance.
No default runtime switch or automatic eviction is authorized.

## Attributed automated evidence

PR #262 at head `d52323ea728414bb195f8b806f8bdea87118ea1c` against
`dev` `ddca60d7e4abb02597d0923cd6d21ec729e3e0c7`:
- GitHub `CI` **FULL / integration PASS**, Python 3.10–3.12, security,
  package smoke, L1/L2, Playwright E2E and lint:
  https://github.com/daniele21/korgis/actions/runs/43527753569
- `Repository Health` **PASS**:
  https://github.com/daniele21/korgis/actions/runs/43527753797
- Real upstream `llama-server` **compiled from the pinned v1.0.0 tag**;
  source-commit and CLI-capability smoke **PASS**:
  https://github.com/daniele21/korgis/actions/runs/43527753754
  The shallow checkout prints `version: 1.0.0 (build 1, commit 2d8a325)`,
  showing why numeric build alone is not authoritative.
- These deterministic gates **do not** establish successful model inference,
  Mac memory reclamation, safe swap behavior or performance. That remains LC7.

## Resume checkpoint

PR #262 (v1 compatibility), #263 (pinned installer/Python 0.3.36),
#264 (self-hosted Mac lane) and #265 (portable CMake fallback) are merged
to dev. CI and native Mac validation/side-by-side installation PASS, linked
above. LC7 remains blocked: run bounded attributable GGUF model evidence on
representative Apple Silicon, compare previous/v1 runtimes under matching
workloads, verify memory/pressure/unload and use Performance Lab for
external endpoint comparison. Do not promote to main or enable eviction on
binary-build-only proof.

Complete only when code, contracts, consumers, docs, exact-head CI and required
real-device evidence agree. Transfer durable truth and delete this workstream
per repository policy; do not retain duplicate plans.
