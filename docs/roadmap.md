# Control-plane roadmap

Status: active
Owner: repository
Read when: selecting the next capability milestone or understanding release dependencies
Last reviewed: 2026-10-11

This file owns milestone sequencing only. Detailed product intent belongs in [`implementation-plan.md`](implementation-plan.md), active implementation planning/progress belongs in `docs/workstreams/`, and integrated/blocker/next truth belongs in [`current-state.md`](current-state.md).

## Objective

Evolve Local LLM Server from a trustworthy multi-backend local inference control plane into a **resource-aware Local AI Application Runtime**: infrastructure that lets one application operate several specialist local AI runtimes on constrained hardware through explicit lifecycle, resource, scheduling, privacy and execution-identity contracts.

The product loop remains:

`Build -> Run locally -> Observe -> Measure -> Compare -> Improve`

The application-runtime sequence is:

`Application intent -> resource plan -> explicit residency -> bounded execution -> reproducible evidence`

## Current 0.4 / L2 milestone summary

| Milestone | State | Remaining release outcome |
| --- | --- | --- |
| M0 Documentation governance | DONE | representative-device truth reconciled and completed runtime workstreams retired |
| M1 Trustworthy request/runtime foundation | DONE | representative thinking OFF/ON-hidden evidence accepted on the target Mac |
| M2 Resource-aware runtime | DONE | bounded resource smoke and repeated two-resident lifecycle/accounting evidence accepted; automatic pressure eviction remains a separate future policy decision |
| M3 Multi-task control plane | PARTIAL | broaden specialist backend/device evidence beyond current text/vision/audio contracts where product scope requires it |
| M4 Evidence-grade observability | DONE | verified compatible representative hardware evidence accepted without promoting memory deltas into safety claims |
| M5 Control-plane UX | DONE | bounded manual accessibility and representative-user usability evidence accepted on the tested product revision |
| M6 Evaluation harness | DONE | repeated compatible post-convergence 10-sample/seed-0 reasoning-OFF evidence accepted |
| M7 Product-grade candidate | READY | final exact-head FULL publication preflight and promotion to `main` |

## Current active workstreams

There are no active L2 completion workstreams. The hardware/runtime and human product-evidence tranches are complete; `docs/device-evidence-runbook.md` and `docs/product-experience-validation.md` remain the durable procedures for reproducing or extending those evidence classes.

## Accepted 0.4 release dependencies

The 2026-08-31 representative Mac evidence accepted:

- real thinking OFF/ON-hidden behavior for the exercised target runtime;
- repeated compatible evaluation under exact post-convergence workload identity;
- verified compatible reclamation observations under the conservative no-safety-promotion contract;
- bounded resource-policy admit/account/release/reject behavior;
- repeated two-model residency, concurrent execution accounting, cleanup and shutdown-under-load ownership.

The 2026-08-31 bounded product-experience evidence accepted:

- the six required manual accessibility checks with no blocking finding;
- the four required representative-user usability journeys with no high/critical finding.

Those results remain scoped to the tested source revision and exercised environment. They do **not** authorize automatic pressure eviction or establish cross-device thermal/performance/production-safety claims.

## M7 product-grade candidate gate

Promotion toward `main` requires all applicable items below to agree with the exact candidate head:

1. blocking deterministic CI green on the integration head;
2. no known P0/P1 regression in supported product entrypoints;
3. thinking execution/exposure semantics truthful for advertised capabilities and representative target-runtime evidence retained;
4. structured-output success returns clean final application content and malformed output fails explicitly rather than being silently repaired;
5. Evaluation uses canonical backend preparation, records materially relevant request settings and has representative repeated-run evidence;
6. release/evidence runs identify exact artifact/backend/config/environment strongly enough for the claim being made;
7. representative task/backend/device smoke retained where compatible hardware exists;
8. configured resource admission/accounting behavior validated without inducing uncontrolled memory pressure;
9. manual accessibility/responsive/destructive-action review for primary UX;
10. README/API/config/evidence documentation aligned with actual supported behavior and observed evidence;
11. experimental/evidence-pending claims remain explicit;
12. automatic pressure eviction remains disabled unless a later dedicated evidence/policy decision explicitly changes that boundary.

All evidence-dependent items above are satisfied for the current candidate scope. Promotion still requires the final FULL deterministic preflight on the exact publication head/merge result.

---

# Post-0.4 application-runtime program

The application-runtime positioning builds on the trustworthy 0.4/L2 control plane. It must not weaken current exact-model, privacy, resource-accounting or evidence invariants in order to make the product appear more automatic.

The program is intentionally sequenced from **declarative intent and planning** to **automated lifecycle**. The server should understand an application before it starts making policy decisions on the application's behalf.

## Milestone summary

| Milestone | State | Product outcome |
| --- | --- | --- |
| M8 Application Profiles & resource planner | PARTIAL | application can declare exact specialist roles and inspect a validated/dry-run resource plan without loading everything |
| M9 Explicit residency & on-demand lifecycle | PLANNED | profile can express pinned/warm/on-demand intent and trigger safe explicit load/unload policy under existing ownership guarantees |
| M10 Workload-aware scheduling | PLANNED | role/task priorities, deadlines and bounded cross-runtime execution make multi-model application workloads predictable |
| M11 Apple Silicon reference runtime | PLANNED | application-runtime lifecycle/resource behavior is evidenced on representative Apple Silicon configurations with claim-scoped support data |
| M12 Developer integration & application-runtime candidate | PLANNED | external applications can adopt the runtime through stable versioned profile/integration contracts and pass the end-to-end product gate |

No M8-M12 item is implemented merely because its target contract is documented here.

M8.1–M8.2 have an experimental core schema/zero-resident validator (PR #230). The M8.3 configured-residency dry-run and opt-in admin API are a candidate separate slice; Studio M8.4, measured peak/concurrency planning and the stable M12 external contract remain incomplete. The separately packaged Python SDK (PR #227) has narrower experimental client bindings and a developer-only managed launcher.

## M8 — Application Profiles & resource planner

### Goal

Move from model-centric manual orchestration toward a declarative application/workload contract while preserving exact model semantics.

### M8.1 — Versioned profile vocabulary

Define the first `ApplicationProfile` schema with:

- application/profile identity;
- exact configured model/runtime references;
- semantic roles/tasks such as transcription, reasoning and vision;
- residency intent (`pinned`, `warm`, `on_demand`);
- workload priority intent;
- optional idle TTL for eligible warm runtimes;
- application resource budget/headroom overrides only where policy permits;
- explicit schema version.

Acceptance:

- malformed profiles fail before runtime mutation;
- unknown models/tasks/capabilities fail explicitly;
- roles never imply hidden model substitution;
- model registry ownership remains separate from profile composition;
- deterministic schema and compatibility tests exist.

### M8.2 — Capability-aware profile validation

Validate every role against the same canonical task/capability source used by product request entrypoints.

Acceptance:

- transcription cannot target a runtime without explicit transcription support;
- vision cannot be inferred from backend name or filename;
- profile validation and request validation cannot disagree about the same capability fact;
- validation can run with zero resident runtimes.

### M8.3 — Resource planner / dry run

Add a non-mutating planning path that explains whether a requested profile plausibly fits the configured device/application envelope.

Planner evidence should distinguish:

- configured memory budget and safety headroom;
- current resident committed bytes;
- transient request envelope where known;
- per-runtime artifact size;
- estimated model footprint and source/confidence;
- compatible observed historical resident/peak footprint where available;
- unknown/unmeasured evidence;
- intended post-activation residency set;
- explicit capacity gaps and reasons.

Acceptance:

- dry run does not load or unload models;
- `unknown` cannot be promoted into `fits`;
- estimates and observations remain different evidence classes;
- plan output is deterministic for a frozen configuration/state;
- diagnostics are privacy-safe and consumable by API and Studio.

### M8.4 — Studio profile read/validate/plan UX

Expose profiles first as a safe inspection/planning workflow rather than immediately adding lifecycle automation.

Acceptance:

- every role visibly maps to its exact configured model;
- invalid/capacity-constrained roles explain why;
- source labels distinguish configured/estimated/observed/unavailable data;
- opening or editing a profile cannot mutate residency implicitly.

## M9 — Explicit residency & on-demand lifecycle

### Goal

Let a validated profile drive bounded lifecycle actions without turning resource pressure into hidden model switching.

### M9.1 — Residency intent semantics

Implement exact semantics for:

- `pinned` — expected to remain resident while the profile is active and excluded from automatic policy candidacy;
- `warm` — may remain resident while useful and may become eligible for explicit idle TTL/LRU policy;
- `on_demand` — cold is normal and activation occurs only through a supported explicit request/profile path.

Acceptance:

- intent is visible in runtime/profile status;
- default route identity and residency remain distinct;
- manual unload remains explicit;
- active leases always protect in-flight work.

### M9.2 — Explicit load-on-demand

Add a bounded cold-runtime activation path for application roles.

Acceptance:

- resource reservation occurs before expensive load;
- concurrent requests cannot duplicate/conflict runtime ownership;
- one bounded deadline covers control-plane-owned waits;
- load failure returns a typed result and never silently switches model;
- startup failure cleans all proven owned partial state.

### M9.3 — Idle residency policy

Connect warm residency to explicit TTL/LRU policy only for eligible idle runtimes.

Acceptance:

- pinned and leased runtimes are never candidates;
- eviction reason/decision is observable;
- unload failure leaves canonical ownership/accounting truthful and retryable;
- policy never rewrites application-role or default-model identity.

### M9.4 — Pressure automation remains gated

No pressure-triggered automatic eviction is enabled as a side effect of M9. A future pressure policy needs a separate decision, evidence plan and representative validation.

## M10 — Workload-aware scheduling

### Goal

Make one-device multi-model applications predictable when text, vision, transcription and background work overlap.

### M10.1 — Priority vocabulary

Introduce a small backend-neutral workload vocabulary such as:

- `realtime`;
- `interactive`;
- `background`.

Priority affects control-plane admission/order only; it does not imply backend preemption or cancellation capabilities that do not exist.

### M10.2 — Role/task concurrency policy

Allow profile policy to constrain concurrency by role/task/runtime without duplicating backend-native batching.

Acceptance:

- global execution governor and runtime semaphore remain separate owners;
- queue bounds are explicit;
- waiting work does not reserve expensive transient resources prematurely;
- cross-runtime fairness remains deterministic/testable.

### M10.3 — Deadlines and cancellation propagation

Unify bounded waiting across control-plane stages and propagate cancellation only where the underlying path truthfully supports it.

Acceptance:

- queued/expired/rejected/running/cancelled states remain distinguishable;
- worker streaming/cancellation is not claimed on buffered or non-cancellable paths;
- disconnect and shutdown preserve leases/accounting/cleanup.

### M10.4 — Application-centric observability

Add privacy-safe attribution of queue/resource/lifecycle state to application profile and role.

Acceptance:

- operator can answer which role is resident, running, waiting or resource-constrained;
- runtime identity still exposes the exact model/runtime/configuration behind the semantic role;
- prompt/output content remains outside normal telemetry.

## M11 — Apple Silicon reference runtime

### Goal

Turn Apple Silicon from a development environment into the first **reference-grade application-runtime target**.

### M11.1 — Dynamic-runtime ownership review

Decide which supported production paths require process isolation for truthful dynamic load/unload ownership and which in-process paths remain acceptable with weaker reclamation semantics.

Acceptance:

- ownership decision is documented per backend path;
- in-process close is never promoted into a reclamation guarantee;
- process-isolated paths prove bounded child cleanup and no orphan ownership.

### M11.2 — Representative application-profile campaign

Exercise realistic multi-role combinations on representative Apple Silicon hardware that is actually available.

Minimum evidence:

- one profile with at least three specialist roles;
- repeated cold -> intended residency -> concurrent workload -> idle/unload -> shutdown cycles;
- resident/transient resource-accounting overlap;
- planner predictions retained beside observations;
- exact artifact/backend/config/hardware identity.

### M11.3 — Support/evidence matrix

Publish a bounded matrix by:

- hardware class exercised;
- backend/runtime family;
- task/model combination;
- lifecycle/resource evidence available;
- performance evidence available;
- unsupported/unmeasured claims.

Acceptance:

- deterministic CI and representative-device evidence remain separate;
- no cross-device extrapolation is implicit;
- thermal/performance/general safety claims require their own evidence.

### M11.4 — Planner calibration loop

Use compatible representative observations to improve footprint estimates/confidence without converting device-specific history into universal truth.

## M12 — Developer integration & application-runtime candidate

### Goal

Make the positioning usable by an external application without requiring it to know backend implementation details.

### M12.1 — Stable application-profile contract

Promote the profile from target/experimental schema to a versioned compatibility surface only after M8-M11 semantics are proven.

Acceptance:

- schema version and migration/deprecation policy exist;
- unknown version/field behavior is explicit;
- materially relevant profile identity can be frozen into diagnostics/evidence.

### M12.2 — Application integration surface

Provide a concise integration path using the existing HTTP boundary plus a thin local SDK/helper only where it removes control-plane boilerplate.

Target flow:

```text
configure profile
start/connect to control plane
validate + plan
activate profile
send task requests
inspect state + identity
shutdown cleanly
```

Acceptance:

- external application never needs backend worker ports;
- OpenAI-compatible text integration remains available;
- task-specific APIs remain truthful;
- SDK/helper cannot become a second policy implementation.

### M12.3 — Reference application examples

Ship small integration examples demonstrating realistic specialist combinations, preferably:

- transcription + reasoning;
- reasoning + vision.

Examples demonstrate orchestration/lifecycle/resource behavior and do not become full end-user applications.

### M12.4 — Application-runtime Studio UX

Reframe primary Studio surfaces around:

- active application/profile;
- role health/readiness;
- resource envelope and plan;
- resident/cold runtimes;
- queue/workload state;
- actionable failure/recovery;
- execution identity/evidence.

### M12.5 — Candidate acceptance

The application-runtime candidate is acceptable only when a representative external application can:

1. define at least three explicit specialist roles;
2. validate task/capability compatibility before mutation;
3. obtain a non-mutating resource plan;
4. start from zero resident runtimes;
5. activate intended residency through explicit policy;
6. run concurrent cross-role work within bounded resource/scheduler policy;
7. receive explicit capacity failures rather than OOM-based flow control;
8. preserve active work from unsafe unload and return to a clean lifecycle state;
9. freeze path-free exact execution identity;
10. complete equivalent deterministic E2E and representative Apple Silicon journeys, with those evidence classes reported separately.

## Dependency graph

```text
M7 trustworthy 0.4/L2 baseline
          |
          v
M8 profile schema + validation + dry-run planner
          |
          v
M9 explicit residency + load-on-demand
          |
          v
M10 workload-aware scheduling
          |
          +------------------+
          |                  |
          v                  v
M11 Apple Silicon evidence   developer/API stabilization
          |                  |
          +--------+---------+
                   v
       M12 application-runtime candidate
```

Product-target design can proceed while M7 publication closes, but executable M8+ work should start from the promoted/fresh baseline rather than destabilizing the current candidate.

## Deliberate non-priorities

These areas are not differentiators for the application-runtime program and should not consume roadmap priority without a concrete application need:

- largest model catalog/model-discovery experience;
- generic desktop chat features;
- generic RAG/agent/MCP framework functionality;
- reimplementation of backend tensor kernels;
- backend count as a goal;
- arbitrary distributed multi-node serving;
- default internet-facing multi-tenant operation;
- hidden cloud fallback or quality-based model substitution.

## Later optional capabilities

After M12, real application demand may justify:

- embeddings, reranking or speech synthesis as explicit task families;
- authenticated LAN/shared-device mode without weakening loopback defaults;
- explicit configured fallback chains with evidence-preserving route identity;
- richer cold/warm experiment orchestration;
- energy/power/thermal observations where representative instrumentation exists;
- stable visual regression for high-risk Studio surfaces;
- formalized compatibility/deprecation policy for historical direct module-level `server:app` use.

## Evidence boundary

Automated tests establish deterministic contracts and make representative testing reproducible. Representative Apple Silicon campaigns establish only the procedures, runtime/model combinations and environments actually exercised.

Positive real-device observations are descriptive evidence. Negative, mixed and inconclusive evidence must also be retainable. One representative device does not establish a cross-device or production-safety claim.

The application profile/planner adds one explicit rule: **a plan is not an observation**. Configured budgets, estimates, historical observations and current measurements remain distinct through API, UI and evidence.

## Maintenance rule

- update this roadmap only when milestone/release sequencing changes;
- update `implementation-plan.md` when product category/intent/target contracts change;
- update `current-state.md` when the integrated baseline, blockers or next executable step changes;
- create active workstreams only when a milestone enters substantial executable coordination;
- when a workstream completes, transfer durable behavior/decisions to owning docs/tests, update current state/roadmap, and delete the completed workstream by default.
