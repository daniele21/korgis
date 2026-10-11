# Experimental ApplicationProfile validation and resource preview (M8.1–M8.3)

Status: experimental
Document type: feature-contract
Owner: runtime-and-platform
Canonical scope: feature.application-profiles
Read when: shaping declarative application intent and capability validation
Last reviewed: 2026-10-11

## Boundary

The canonical server-side ApplicationProfile contract lives in local_llm_server.core.application_profile. It binds a named application/profile and task roles to **exact configured registry keys**. It deliberately has no implicit model selection, download, activation, process construction, filesystem scan or persistent state.

M8.1 and M8.2 cover the canonical vocabulary and capability validation. M8.3 adds a bounded, non-mutating *configured-residency* planning preview. M8.4 Studio, M9 residency automation, M10 request priorities and M12 stable integration remain future work.

## Experimental schema

    {
      "schema_version": "korgis-application-profile-v1",
      "application_id": "closedroom",
      "profile_id": "meeting",
      "roles": {
        "analysis": {
          "model": "nemotron-nano-4b",
          "task": "structured_generation",
          "residency": "on_demand",
          "priority": "interactive"
        }
      }
    }

Each role requires model, task and residency. Priority is optional and defaults to interactive; realtime and background are legal **intents**, not active scheduling guarantees. Warm roles may declare idle_ttl_seconds in 1..86400, again without activating any TTL policy.

Task names come directly from core.contracts.TaskType: chat, structured_generation, vision_language, transcription, image_generation. Supported tasks and structured-output features are checked using core.capabilities.descriptor_from_registry_entry, the same facts that underpin HTTP capability admission.

## Semantics

- Unknown keys, unsupported versions, unknown models, tasks and capabilities are rejected before mutation.
- A model_id alias is not a valid implicit substitute for a registry key. Unknown means invalid, not fallback.
- Validation requires an explicit registry mapping but no resident runtimes. Plain validation reports resource-fit as unavailable; the separate plan endpoint computes a conservative configured budget preview.
- Residency and priority intent do not load, evict, pin, reorder, preempt, cancel or download anything.
- Diagnostic output is restricted to non-secret role, model key and capability metadata, and does not contain filesystem paths or user content.
- The independently installable Python SDK's experimental korgis-client-bindings-v1 is a **client-side compatibility convenience**, not this canonical M8 contract. API exposure and a compatibility/migration path are required before public M12 stabilization.

## Resource planning preview (M8.3)

An admin-enabled Korgis exposes POST /api/v1/applications/profiles/validate and POST /api/v1/applications/profiles/plan. The body is the exact JSON profile above. Administrative endpoints remain disabled without --enable-admin-api and current local HTTP serving has no built-in authentication. Avoid exposing them beyond a trusted loopback boundary.

The planner reuses the registry, runtime/config resolution, resident MemoryEnvelope, ResourceManager reservation snapshot and ResourcePolicySettings. It does **not** load, download, reserve, evict, preempt, or mutate anything. Active intended residency is the union of pinned/warm roles; cold on_demand is deferred, not assumed safe for future concurrent requests. Multiple roles pointing to one model count once.

The result is one of:
- rejected: a known minimum residency requirement already exceeds the configured usable budget;
- uncertain: absent budget, ledger attribution or complete memory envelope prevents a fit decision;
- plausible: all currently intended additional resident footprints have complete *configured estimates* that fit the configured budget. This is **never a measured-memory, peak-concurrency, thermal or hardware-safety guarantee**.

Per-model evidence labels distinguish registry artifact size, configured/composed estimate, unavailable observed resident/peak and optional configured request memory. Specific per-request transient envelopes and historical compatible observed resident/peak calibration are **not integrated** in this first preview. The endpoint never returns model file paths, prompts, output or secrets.

Because planning is snapshot-based and non-reserving, later resource contention can invalidate a plausible result. ResourceManager remains the sole resource-admission authority during actual load/inference.

## Acceptance and follow-up

Unit regression lives at tests/test_application_profile.py. M8.3 uses deterministic ResourceManager/MemoryEnvelope snapshots; tests cover mixed residency, duplicate roles, incomplete estimates, over-budget lower bounds, no budget, unaccounted runtimes and path-free API responses. M8.4 still needs a non-mutating Studio UI.

Before shipping a stable application-profile API, test the canonical route/admission parity with actual HTTP composition and resolve schema compatibility with the standalone consumer SDK.
