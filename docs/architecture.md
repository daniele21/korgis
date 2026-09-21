# Current architecture

Status: active
Document type: architecture
Owner: runtime-and-platform
Canonical scope: current.architecture
Read when: changing runtime boundaries, composition roots, resource ownership, trust/data flow or backend integration
Last reviewed: 2026-09-21

This document owns the **current integrated architecture** of Local LLM Server. [`architecture-evolution-plan.md`](architecture-evolution-plan.md) remains the target/migration document; [`current-state.md`](current-state.md) owns operational progress and evidence blockers.

## System boundary

Local LLM Server is a local-first control plane around specialist inference engines. Product/application code integrates through stable HTTP/Python boundaries; backend adapters execute models. The control plane owns policy and lifecycle around those engines rather than reimplementing inference.

```text
application / Local LLM Studio / evaluator
                  |
                  v
       supported product HTTP stack
                  |
     canonical request + policy boundary
                  |
       scheduler / runtime resolution
                  |
     resident runtime lease + resources
                  |
        backend engine / worker path
                  |
      normalized output + evidence
```

The public product composition is the supported assembled boundary. Direct historical module-level compatibility surfaces must not become a second product-policy implementation.

## Composition roots

| Boundary | Current owner | Responsibility |
| --- | --- | --- |
| Product assembly | `src/local_llm_server/product_composition.py` | supported HTTP policy/middleware and control-plane composition |
| Public/runtime HTTP | `src/local_llm_server/server.py` plus modular API modules | compatibility schemas/routes and runtime-facing application assembly |
| Control plane | `src/local_llm_server/control_plane_api.py` | model/runtime/evaluation administrative workflows |
| Public identity | `src/local_llm_server/identity_api.py` | versioned path-free execution identity |
| CLI | `src/local_llm_server/cli.py` | model operations, serve lifecycle and evidence/verification entrypoints |
| Studio | `src/local_llm_server/static/` | local web control plane consuming server-owned contracts |

`server.py` still contains compatibility responsibilities that the evolution plan intends to split. New features should attach to the smallest current owner rather than increasing that compatibility concentration.

## Canonical request and capability boundary

`src/local_llm_server/core/` owns backend-neutral task/request/capability vocabulary. Supported product entrypoints validate media/task/capability combinations before backend execution.

Important invariants:

- a model key/model ID is resolved explicitly; there is no silent model substitution;
- text, vision and transcription support comes from declared capabilities, not filename/backend guessing;
- unsupported task/modality combinations fail before expensive backend work;
- structured final application output is kept separate from hidden reasoning;
- reasoning execution and reasoning visibility are separate controls when the runtime supports switching.

## Runtime ownership and residency

`runtime.py` and `product_runtime_manager.py` own live runtime state and routing.

```text
configured artifact/model
        |
        v
resource admission / load
        |
        v
resident runtime ---- optional pinned state
        |
        +---- active lease(s) protect in-flight work
        |
        +---- explicit default-route selection
        |
        v
lease-safe unload / cold state
```

Configured, downloaded, resident, pinned and default-route states remain distinct. Zero resident runtimes is a valid healthy control-plane state when the surrounding product contract permits it.

Managed subprocess runtimes receive a concrete positive loopback port selected from the configured base while excluding both ports reserved by known runtimes and listeners already bound on the host. A configured port is an allocation base, not proof that Local LLM Server owns that listener. Load/reload reserve the selected endpoint while backend construction is in flight so concurrent lifecycle operations cannot publish conflicting owners.

The in-process engine path and process-isolated worker/evidence path have different reclamation guarantees. Worker exit can be an ownership boundary; in-process cleanup must not be described as proven memory reclamation without representative evidence.

## Resource and scheduling boundary

- `resource_manager.py` owns the single configured memory budget/headroom ledger, resident/transient reservation, admission, accounting and release.
- `request_scheduler.py` composes optional per-runtime priority-aware admission with optional global execution admission for supported HTTP inference. Missing workload metadata defaults to `standard`; it does not own backend batching or memory accounting.
- `scheduler_policy.py` owns the workload-class vocabulary (`interactive`, `standard`, `batch`, `background`), base priorities and deterministic wait aging. Aging raises effective priority one point per queued second so lower-priority work eventually competes with fresh higher-priority work.
- `global_execution_governor.py` owns the optional bounded cross-runtime execution pool. It selects the highest effective workload priority, uses runtime round-robin to break equal-priority ties, and mirrors each runtime's configured concurrency only as an eligibility bound so global slots are not consumed by work that would immediately block on the runtime semaphore; the semaphore remains the final per-runtime safeguard.
- first-class resident transcription consumes the same attached global governor before transient-memory reservation and runtime lease, so chat/vision and ASR participate in one cross-runtime execution bound.
- `residency_eviction.py` owns deterministic explicit LRU/TTL candidate selection.
- `residency_pressure.py` owns pressure-policy evaluation and hysteresis.

Global execution admission is explicit and opt-in; the control plane does not silently reduce server concurrency. Per-runtime queueing remains independently opt-in. Workload priority affects only queued admission: it never preempts already-running inference and does not replace backend-native batching. When both admission layers are configured, one pre-execution timeout budget spans both waits. Requests waiting only for execution capacity do not reserve transient memory. Streaming requests retain acquired execution slots until their response body completes or is cancelled.

Automatic pressure-triggered eviction remains disabled. The accepted representative-device evidence demonstrates repeated multi-model ownership/accounting and bounded shutdown behavior but deliberately does not provide an automatic-eviction recommendation or reclamation/production-safety claim. Any future pressure-triggered automatic action therefore requires a separate explicit evidence and policy decision. Resource-policy code must never silently substitute a different model or evict an actively leased runtime as an admission side effect. Global execution admission does not imply that an already-running in-process backend can always be interrupted.

## Backend boundary

`engine.py` and worker adapters translate the backend-neutral request into specialist runtime calls. Current backend families include llama.cpp/`llama-cpp-python`, MLX text, managed `llama-server`, MLX VLM and explicit transcription runtimes.

Backend-specific model objects, caches, chat templates and invocation details belong behind adapters. Public capability and output semantics must not depend on a caller knowing backend internals.

Managed `llama-server` is selected explicitly or discovered under the attributable llama.cpp feature-floor contract. A positive build+commit identity may be reused for the exact same executable within one owning process, keyed by filesystem identity; replacement or upgrade of the executable invalidates that proof and requires revalidation. Failed or unattributable version probes are never cached as positive evidence. This avoids treating repeated native version initialization as part of every resident-model construction while preserving fail-closed binary attribution.

Worker streaming and in-flight cancellation remain unsupported unless a true incremental/cancellable worker protocol is implemented and validated. Buffered output must not be relabelled streaming.

## Artifact and model-source flow

Registry/model-source code resolves local or explicitly requested remote artifacts before runtime start. Artifact identity, configured model identity and resident execution identity remain distinct.

Hugging Face snapshot resolution is used for applicable MLX/VLM model sources and is an explicit optional dependency path. Remote model code is a trust decision, not an automatic convenience fallback.

Verified artifact receipts can contribute exact SHA-256 evidence to runtime identity/hardware evidence. A filename or model label alone is not integrity evidence.

## Observability and execution identity

Metrics/evidence modules distinguish token/chunk counts, queue wait, TTFT, prefill/decode timing, throughput and task-specific transcription evidence. Values retain source/availability semantics; unavailable is not zero.

`GET /v1/runtime/identity` exposes stable, path-free execution identity suitable for evaluators. `/status` exposes mutable runtime activity. They answer different questions and must not be merged into one semantic contract.

Public identity must not expose model paths, credentials, prompts, outputs, hostnames or dynamic request counters.

## Evaluation boundary

`evaluation*.py` owns deterministic/custom test-set validation, execution, persistence and comparison. Evaluation uses the canonical request preparation path and freezes effective reasoning/runtime identity where available.

A deterministic evaluation score is evidence about that configured test run. Compatibility mismatches or incomplete identity remain explicit; the system must not infer a universal “better model” verdict from incompatible evidence.

## Trust and network flow

The security/data contract is owned by [`../SECURITY.md`](../SECURITY.md).

Default flow:

```text
local client -> 127.0.0.1 Local LLM Server -> local backend/model
```

Loopback is the default trust boundary. Binding outside loopback, enabling administrative APIs, remote media or remote model code broadens that boundary explicitly and requires an operator-owned trust/authentication decision.

There is no silent remote inference fallback.

## Sensitive-data flow

Prompts, outputs, uploaded media, transcripts and evaluation data can be sensitive. Normal public identity and diagnostic surfaces avoid raw content/private paths. E2E fixtures use synthetic data. Evidence retention is claim-scoped rather than an implicit archive of user content.

Model caches/downloads are user-owned durable data unless a command explicitly says otherwise. Generic repository cleanup must not delete them.

## Ephemeral-resource ownership

Project-owned processes, listeners, temporary directories and browser/evaluation fixture state require a run/owner boundary. Cleanup must cover success, failure, timeout, cancellation, interrupt and partial initialization and must refuse broad deletion when ownership is not proven.

The E2E lifecycle implementation owns its synthetic evaluation root and loopback fixture listener. The worker/runtime lifecycle owns child processes it starts. Post-cleanup verification is part of the target operating contract.

## Build/release boundary

Build version, unique build ID and source revision are separate identities. Build/release scripts own staging, promotion, manifest/checksum/build-delta generation and retention; GitHub Releases own published release storage.

Successful artifacts must not be overwritten in place and published tags/releases must not be force-moved. The canonical lifecycle contract is `.engineering/commands.json` once the repo-template integration is complete.

## Durable decision and feature routing

- `docs/adr/` contains accepted architectural decisions whose rationale/tradeoffs need a durable record.
- `docs/features/` contains independently readable current feature behavior with no better API/operations owner.
- active implementation coordination belongs only in `docs/workstreams/`.
- completed workstreams transfer durable truth and are deleted by default; Git history preserves implementation chronology.

## Evidence boundary

Deterministic unit/integration tests, browser E2E, real-runtime smoke and representative-device evidence are different evidence classes.

Hosted CI proves deterministic contracts and assembled product journeys. It does **not** prove real model quality, cross-device Apple Silicon memory behavior, throughput, thermal behavior or backend stability on untested target environments. The accepted 2026-08-31 representative Mac evidence covers only the minimum L2 runtime bundle and the repeated RRG-5 multi-model ownership/accounting procedure described in `docs/current-state.md`; `docs/device-evidence-runbook.md` owns reproduction/extension procedures. Human accessibility/usability evidence remains a separate product-experience class.

## Change routing

When changing a shared contract, inspect the current owner, direct consumers, test fakes and applicable tests. Update this document only when current architectural ownership/trust/resource flow changes. Update the evolution plan only when the intended target/migration changes; update current state/workstreams only for progress/evidence state.
