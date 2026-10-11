# Experimental ApplicationProfile schema and validation (M8.1–M8.2)

Status: experimental
Document type: feature-contract
Owner: runtime-and-platform
Canonical scope: feature.application-profiles
Read when: shaping declarative application intent and capability validation
Last reviewed: 2026-10-11

## Boundary

The canonical server-side ApplicationProfile contract lives in local_llm_server.core.application_profile. It binds a named application/profile and task roles to **exact configured registry keys**. It deliberately has no implicit model selection, download, activation, process construction, filesystem scan or persistent state.

This first slice covers M8.1 versioned profile vocabulary and M8.2 capability-aware validation. It does **not** yet implement M8.3 resource planning, M8.4 Studio, M9 residency automation, M10 request priorities or M12 stable external SDK/profile contracts.

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
- Validation requires an explicit registry mapping but no resident runtimes. Resource-fit is reported unavailable rather than guessed.
- Residency and priority intent do not load, evict, pin, reorder, preempt, cancel or download anything.
- Diagnostic output is restricted to non-secret role, model key and capability metadata, and does not contain filesystem paths or user content.
- The independently installable Python SDK's experimental korgis-client-bindings-v1 is a **client-side compatibility convenience**, not this canonical M8 contract. API exposure and a compatibility/migration path are required before public M12 stabilization.

## Acceptance and follow-up

Unit regression lives at tests/test_application_profile.py. M8.3 must reuse the ResourceManager, memory envelopes and evidence source labels for a deterministic, non-mutating planner. M8.4 then exposes validated intent/plan in Studio without lifecycle side effects.

Before shipping a stable application-profile API, test the canonical route/admission parity with actual HTTP composition and resolve schema compatibility with the standalone consumer SDK.
