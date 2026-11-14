# llama.cpp v1.0.0 integration

Status: active
Owner: Korgis managed `llama_server` runtime, resource admission and identity
Last reviewed: 2026-11-14
Read when: validating the llama.cpp upgrade or representative release evidence

## Outcome and boundary

Keep Korgis' existing OpenAI-compatible serving, memory guard, security,
runtime lifecycle and explicit external binary selection while supporting
attributable llama.cpp v1.0.0 and opt-in FP8 KV/MoE host cache settings.
Korgis owns execution, processes, admission and lifecycle. Performance Lab
(`daniele21/performance-lab`) owns external endpoint evaluation, comparison
and regression. Neither project may duplicate the other's authority.

Reference source: `ggml-org/llama.cpp` v1.0.0,
`2d8a32502909ca065c41c4cf5f2fb1c427c047fc` (released
2026-11-13). Its shallow-history Metal binary reports build 1; the
source commit and binary digest, not build count, establish identity.
A separate historical reference is v0.3.0,
`c1d0e7a004015f23bc0233470b747b596f29b264`.

## Non-negotiable invariants

- Artifact, configured model, resident process and default route are
  separate; explicit binary selection wins without hidden fallback.
- Unattributed versions or CLI flags fail closed. FP8, MoE cache and
  experimental SSD expert streaming never activate implicitly.
- Runtime ownership covers streams, leases, process trees, listeners,
  failures, cancellation, shutdown and retryable teardown.
- Conservative admission remains enabled; swap usage is not extra RAM.
  Automatic eviction remains disabled pending hardware proof.
- Public evidence excludes private paths, secrets, prompts and outputs.
  Measured, estimated and unavailable values remain distinct.
- Preserve model-specific forks, API/security defaults and independently
  pinned `llama-cpp-python`; no upstream v1 C-ABI assumption.

## Work graph

| ID | Outcome | Status |
| --- | --- | --- |
| LC1 | Exact source-tag build, executable attribution and CLI smoke | DONE |
| LC2 | Versioned compatibility profiles and fail-closed CLI assembly | DONE |
| LC3 | Existing process/stream/load/unload owner and deterministic E2E | DONE |
| LC4 | Typed FP8/MoE options, conservative estimates/identity | DONE |
| LC5 | HTTP/security compatibility, automated E2E and FULL gates | DONE |
| LC6 | Independent `llama-cpp-python 0.3.36` lock/native build | DONE |
| LC7 | Matched Mac memory/lifecycle and Performance Lab regression | PARTIAL — release blocking |
| LC8 | PR #262 integrated to `dev`; `main` promotion gate | DEV DONE / RELEASE BLOCKED |

PRs #262–#265 landed on `dev`; PR #271 fixes **shallow v0.3
build-1 attribution only for the pinned validated source commit**.
Unrecognized build-1 binaries and unattributable version output still
fail closed. Existing CLI options `--parallel` and supported
`--cache-type-{k,v}` are version-gated. FP8 `f8_e4m3` and host
`--moe-cache-mib`/`--moe-cache-cpu-share` remain explicit opt-ins;
host cache is distinct from experimental SSD expert streaming.

## Representative Apple Silicon evidence

Each evidence link identifies its own source SHA and procedure; an
installed old engine was **not** present in the [read-only inventory
#43629844010](https://github.com/daniele21/local-runner-infra/actions/runs/43629844010).
The newly built v0.3 binary is a *historical reference*, not the user's
previously installed runtime.

| Procedure | Real Mac result | Evidence |
| --- | --- | --- |
| Pinned v1 Metal + separate Python 0.3.36 build | PASS, 45 owner tests | [#43599039636](https://github.com/daniele21/local-runner-infra/actions/runs/43599039636) |
| Versioned nonactivating v1 stage | PASS | [#43600645829](https://github.com/daniele21/local-runner-infra/actions/runs/43600645829) |
| Unsafe 4.23 GB Nemotron admission preflight | Correctly refused; model not launched | [#43603651690](https://github.com/daniele21/local-runner-infra/actions/runs/43603651690) |
| v1 specialist GGUF direct inference | PASS; measured peak server RSS 1,008,205,824 bytes | [#43610869367](https://github.com/daniele21/local-runner-infra/actions/runs/43610869367) |
| Managed Korgis HTTP, streaming, unload + independent Python GGUF | PASS twice | [#43629147770](https://github.com/daniele21/local-runner-infra/actions/runs/43629147770), [#43629484166](https://github.com/daniele21/local-runner-infra/actions/runs/43629484166) |
| Exact-source historical v0.3 Metal build | PASS; not activated | [#43630661665](https://github.com/daniele21/local-runner-infra/actions/runs/43630661665) |
| Paired v0.3/v1 Korgis-managed lifecycle, 3 cycles each | **6/6 PASS**, entire workflow/receipt verification PASS | [#43633636676](https://github.com/daniele21/local-runner-infra/actions/runs/43633636676) |

Same model in all matched runs: Qwen3.5-0.8B Q4_K_M, 527,502,816
bytes, SHA256
`f5b14da98939b60bbe1019a964eba656407e1e0b64f1fe3003ff6d650e93bfec`,
verified model revision
`7925ccdc665d4efdb1034791e6b553e11128e6f8`.
Paired managed run at Korgis candidate
`7043a17ffd443f4b11e1f4534fda72fc59527def`, context 512,
8 GPU layers, 4 threads, temperature 0, 16 generated tokens.
Mac host 32 GiB; admission configured with conservative 1.25 GiB
resident estimate.

Six interleaved cycles `v0.3,v1,v1,v0.3,v0.3,v1` each completed
HTTP chat, SSE streaming, admin unload and resource-accounting zero.
Every cycle ended `cold` with both listeners closed, zero surviving
native processes, no force-kill and no host reserve crossing.
Swap used remained 3,142,057,984 bytes before/after each cycle.
Descriptive **median across 3 cycles per engine**:

| Observation | v0.3.0 | v1.0.0 |
| --- | ---: | ---: |
| Cold startup/readiness | 1.294 s | 1.298 s |
| Completion request elapsed | 0.133 s | 0.123 s |
| First SSE event | 32 ms | 26 ms |

These are short-model lifecycle timings, **not** statistically
established prefill/decode throughput, peak-RSS/thermal measurements,
swap in/out activity, memory-pressure reclamation or model-quality
equivalence. First comparison attempts
[#43632142390](https://github.com/daniele21/local-runner-infra/actions/runs/43632142390)
and [#43632364870](https://github.com/daniele21/local-runner-infra/actions/runs/43632364870)
failed before inference from shallow v0.3 build-1 rejection;
[#43632684066](https://github.com/daniele21/local-runner-infra/actions/runs/43632684066)
isolated real `--version` identities. The first repaired six-cycle
run [#43633363555](https://github.com/daniele21/local-runner-infra/actions/runs/43633363555)
passed execution but its final workflow summary attempted to execute
the receipt path and failed; the final #43633636676 corrected that
harness-only error. Preserve earlier FAIL classifications.

## Korgis-only runtime qualification (no external evaluation)

This slice records **Korgis runtime-owner** evidence only. It does not
change Performance Lab's ownership or waive a release-required external
evaluation; no changes were made to Performance Lab.

[Native observation #43635780313](https://github.com/daniele21/local-runner-infra/actions/runs/43635780313):
physical Mac, Korgis `df034929505dbcc10e6dc6d318c092dd496d0c5b`,
same verified 0.8B GGUF, four interleaved cycles per engine,
96 actual completion tokens/request, HTTP chat + SSE + unload.
**8/8 PASS** with zero configured accounting, cold health and no
owned listeners/processes left. Observed medians v0.3/v1:
startup **1.300/1.302s**, HTTP completion **0.5795/0.533s**,
effective end-to-end rate about **165.7/180.1 tokens/s**, and
*sampled* native-server peak RSS **784,826,368/783,482,880 bytes**.
Host-wide swapin/swapout page-counter deltas were **0/0** in
each cycle; swap used was unchanged. These short-request numbers
include HTTP/prefill and cannot be called native decode throughput,
continuous peak memory or representative large-model behavior.

[4B read-only preflight #43636178878](https://github.com/daniele21/local-runner-infra/actions/runs/43636178878):
a cached Qwen3.5-4B Q4_K_M was hash-identified
(`00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4`,
2,740,937,888 bytes). Conservative preflight required
9,776,843,072 bytes of host headroom versus 9,034,792,960
bytes observed; **not ready**, model **not launched**.
This is the inventory script's safety heuristic, not evidence of
canonical Korgis model admission, fit or performance.
Do not lower the headroom threshold to force a larger-model run.

**Real v1 client-disconnect/lease-cleanup gate not exercised:**
[initial run #43636399217](https://github.com/daniele21/local-runner-infra/actions/runs/43636399217)
and [diagnostic rerun #43636531152](https://github.com/daniele21/local-runner-infra/actions/runs/43636531152)
both refused before model load by canonical host admission.
The second receipt separated a normal coarse pressure observation
from a ResourceManager rejection: 8,043,675,648 measured reclaimable
bytes versus a 6 GiB OS reserve plus 1.5 × 1.25 GiB admitted
estimate (8,455,716,864 bytes required). No streaming,
cancellation, unload-under-lease or process-level fault was
observed in these attempts. Do not classify the blocked procedure
as lifecycle failure or bypass host admission to obtain a PASS.

## Outstanding LC7 / release contract

- Korgis: representative repeated HE-2 reclamation, RES-2 admission,
  RRG-5 overlapping/transient pressure/stop-under-load with bounded
  resource receipts. Collect actual peak RSS and available swap in/out,
  memory pressure and post-unload observations. Include representative
  larger GGUF if admitted; never lower safety headroom to force PASS.
- Performance Lab: run versioned identical externally served workloads
  against both Korgis-managed endpoints, with compatible fingerprints,
  exact model/hardware/config, warmup/repetitions, TTFT, prefill/decode,
  error rates, quality and regression classification. Missing telemetry
  stays unavailable; never infer throughput from SSE chunk counts.
- Distinguish baseline, candidate, head/tree/base, native binary SHA and
  model hash; retain failed/interrupted observations.
- Before `dev -> main`: `FULL` deterministic release validation and
  applicable REAL_ENVIRONMENT acceptance, current durable docs, fresh
  base, zero residue and rollback evidence. No product-safety or
  performance superiority claims until those gates pass.

Rollback is **explicitly** select the previous attributable binary,
turn off v1-only flags, verify API/health/inference/unload/cleanup and
preserve the user's chosen default executable. No silent replacement.

## Resume checkpoint

Korgis implementation and short-model Mac lifecycle proof are complete
on `dev`; LC7 is **not** release complete. Prioritize matched external
Performance Lab runs and representative heavier/reclamation evidence
without reimplementing benchmark ownership inside Korgis.
See `docs/device-evidence-runbook.md` for canonical HE-2/RES-2/RRG-5
procedures, `docs/current-state.md` for integrated state, and
`.engineering/commands.json` for validation. Finalize/delete this
workstream only when LC7 evidence and release disposition agree.
