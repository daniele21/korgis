# Request Resource Telemetry v1

Status: implementation complete; REAL_ENVIRONMENT pending
Owner: runtime observability
Last reviewed: 2026-10-05
Read when: implementing per-request CPU/RAM evidence, application-facing inference evidence, or consuming Korgis telemetry

## Goal

Expose privacy-safe, source-qualified resource evidence for each local inference request so applications can compare model quality, latency and local resource cost without implementing their own runtime sampler.

## Product and delivery classification

- Product: PRODUCT_FEATURE
- Delivery: ITERATION -> INTEGRATION
- Validation: STRONG
- Execution: AGENT_LOCAL for deterministic contracts, REMOTE_AUTOMATED when local tooling is unavailable, REAL_ENVIRONMENT for representative hardware claims

## Non-goals

- Do not claim exact GPU-only memory attribution where the platform cannot measure it.
- Do not infer unavailable metrics or turn missing evidence into zero.
- Do not make automatic pressure eviction depend on the new sampler.
- Do not expose prompts, outputs, private paths, hostnames or process identifiers.
- Do not treat sampled process telemetry as universal production or cross-device performance proof.

## Invariants

- Measured, estimated, configured and unavailable evidence remain distinct.
- Runtime identity, mutable status, configured resource policy and per-request resource use remain separate contracts.
- Sampling failure never fails inference; it degrades evidence explicitly.
- Cache hits are not reported as fresh inference resource consumption.
- Concurrent/in-process work must not be presented as exclusively attributable to one request.
- Request telemetry ownership ends on success, failure, timeout or cancellation.
- OpenAI-compatible clients may ignore Korgis additive evidence without breaking.

## Target contract

For non-streaming inference, Korgis adds a bounded `korgis` object:

```json
{
  "korgis": {
    "evidence_version": "korgis-request-evidence-v1",
    "request_id": "opaque-id",
    "execution_source": "inference",
    "resources": {
      "memory": {
        "baseline_bytes": 0,
        "peak_bytes": 0,
        "end_bytes": 0,
        "peak_delta_bytes": 0
      },
      "cpu": {
        "average_percent": 0.0,
        "peak_percent": 0.0
      },
      "sampling": {
        "interval_ms": 100,
        "sample_count": 0,
        "errors": 0,
        "cpu_observation_ms": 0.0
      },
      "attribution": {
        "scope": "korgis_process_tree",
        "quality": "process_global"
      },
      "sources": {
        "memory": "ps_process_tree_rss_excluding_sampler",
        "cpu": "ps_process_tree_cpu_time_delta_excluding_sampler"
      }
    }
  }
}
```

All values may be unavailable when they cannot be measured truthfully.

## Work graph

| ID | Work | Owns/writes | Depends on | Parallel | State |
| --- | --- | --- | --- | --- | --- |
| KT-1 | Freeze resource-evidence vocabulary and privacy/attribution rules | this workstream, API docs | — | yes | DONE |
| KT-2 | Implement bounded process-tree sampler and aggregation contract | `resource_telemetry.py`, unit tests | KT-1 | yes | DONE |
| KT-3 | Attach request evidence to non-streaming chat completions and cache semantics | `server.py`, server tests | KT-1 | yes | DONE |
| KT-4 | Link latest resource snapshot into runtime evidence / canonical metrics | `live_evidence.py`, `completion_metrics.py`, tests | KT-2, KT-3 | no | DONE |
| KT-5 | Extend streaming/failure/cancellation evidence ownership | streaming middleware / tests | KT-2 | yes | DONE |
| KT-6 | Update HTTP/resource integration docs and evidence consumers | docs/consumers | KT-3, KT-4 | yes | DONE |
| KT-7 | Deterministic strong validation | repository selectors/gates | KT-2..KT-6 | no | DONE |
| KT-8 | Representative-device CPU/RAM and sampler-overhead evidence | device evidence campaign | KT-7 | no | REAL_ENVIRONMENT PENDING |

## Current executable slice

`KT-8`; automated implementation and release-profile validation are complete

Acceptance:

- CPU percentages are computed from process CPU-time deltas over the request sampling window, not lifetime `%CPU` reported by `ps`;
- process-tree samples aggregate baseline/peak/end RAM, peak delta, average/peak CPU and sampling metadata;
- sampling errors degrade evidence rather than inference;
- successful non-streaming inference returns `korgis-request-evidence-v1`;
- cache hits identify `execution_source=cache` and do not reuse historical resource measurements as fresh consumption;
- no prompt/output/private-path/PID leaks into resource evidence.

Validation:

- targeted telemetry unit tests;
- targeted server/cache contract tests;
- repository validation selector before integration.

## Integration points

- `InferenceMetrics.resource_snapshot_id` is the canonical linkage point to request resource evidence.
- `/api/v1/resources` remains configured budget/accounting state, not measured request telemetry.
- `/v1/runtime/identity` remains stable execution identity.
- `/status` remains mutable activity state.
- `/api/v1/evidence` may expose the latest privacy-safe resource snapshot after KT-4.

## Representative-device evidence

Deterministic tests prove schema, aggregation, cleanup and failure semantics only. Claims that CPU/RAM figures reflect real backend behavior require a representative-device run covering warm/cold requests, different prompt sizes, repeated requests and overlap/concurrency. Apple unified-memory values must remain source-qualified and must not be relabelled as GPU-only memory without a trustworthy source.

## Durable documentation destinations

- `docs/http-api-reference.md`: application-facing evidence contract
- `docs/runtime-status-reference.md`: boundary between status and request evidence
- `docs/resource-regression-contract.md`: configured accounting vs measurement
- `docs/current-state.md`: integrated state and remaining representative evidence
- tests/contracts: executable truth

## Resume checkpoint

- base: `main@55372cabe0add38e3391974d85e8ad429cf7b3ab`
- branch: `agent/request-resource-telemetry-v1`
- validation PR: #224 retargeted to `main` while remaining draft so the diff matches the branch origin and release/full selectors can run without implying merge readiness.
- confirmed: existing resource manager is configured accounting; current canonical inference metrics already expose `resource_snapshot_id`; no CPU/RAM request sampler exists on main.
- automated candidate evidence: `agent/request-resource-telemetry-v1@a9095d730ee99def7a03b20f7acc5b7a909c74e0`, CI run 683 selected release/FULL and passed all required gates; Repository Health run 375 PASS.
- later documentation-only synchronization commits invalidate exact-head evidence until the final rerun completes; do not reuse run 683 as exact-head proof for a newer commit.
- deferred: representative Apple Silicon CPU/RAM behavior and sampler-overhead evidence only.
- next discriminating action: execute the RTE-1 representative-device procedure, retain bounded privacy-safe evidence and update durable current state with the result.

## Completion

Automated implementation is complete. Full completion remains blocked only by the declared REAL_ENVIRONMENT RTE-1 evidence. After that evidence is accepted, transfer the conclusion to `docs/current-state.md` and retire this workstream by default.
