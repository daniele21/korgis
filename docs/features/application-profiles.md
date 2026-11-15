# Experimental ApplicationProfile validation and resource preview (M8.1–M8.3)

Status: experimental
Document type: feature-contract
Owner: runtime-and-platform
Canonical scope: feature.application-profiles
Read when: shaping declarative application intent and capability validation
Last reviewed: 2026-11-15

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

### Read-only capacity advisory (experimental)

The plan response also exposes `capacity_advisory`. It reuses the same configured budget and resident estimates instead of introducing an independent admission policy:

- `configured_remaining_before_bytes` and `configured_remaining_after_bytes` show the remaining **configured** resource budget (not free physical RAM). When the ledger is absent/unattributed or the planned estimate is incomplete, unavailable margins remain `null`. A negative after-margin is a shortfall, not permission to load.
- `known_configured_shortfall_bytes` is the positive excess of the known lower bound over the configured budget, or `null` when there is no configured budget.
- `host` contains only path-free, point-in-time measurements from the existing ResourceManager host observer, when configured; measured available bytes are **not** extended by swap-used bytes. Host pressure can be derived or native-event sourced, and `fit` stays `unverified` even when pressure reads normal.
- `storage.planned_models_catalog_artifact_bytes` sums declared sizes of **distinct models that would load**. It is not an estimate of bytes to download, disk installation overhead or SSD expert streaming. Unknown catalog sizes make this total unavailable; download bytes and storage fit remain explicitly unavailable/unverified.
- `storage.available_disk_bytes` observes available bytes on the **configured model-cache directory volume** only when that directory already exists and can be queried; an absent or inaccessible directory is `unavailable`. It measures the filesystem currently containing the configured directory and cannot establish whether an intended external volume was mounted. No path is returned. The configured model-cache volume is not necessarily the volume used by externally installed runtime binaries, scratch space, alternative model paths or existing GGUFs. This measurement never authorizes downloads or marks a model as fitting on disk.
- `streaming_memory_discount_applied=false` is deliberate: experimental expert streaming does not bypass conservative residency accounting.
- `admission_preview` invokes the **existing ResourceManager admission policy read-only**, accounting for configured limits, pending reservations and the same conservative observed host-pressure/headroom checks that real `reserve()` uses. It uses the full configured additional residency estimate, not a partial lower bound, and never calls `reserve()`, `commit()`, or model loading. It reports `rejected_at_snapshot`, `not_rejected_at_snapshot`, `unverified` (incomplete estimates/ledger or no enforceable decision), or `not_applicable` (no new warm/pinned residency), with the canonical path-free reason. `load_authorized` is **always false**.
- An admission preview conservatively treats the set of planned warm/pinned cold loads as one aggregate growth request; sequential activation might behave differently. Both the preview and the displayed host observation are point-in-time reads and can differ if memory changes in between. Neither provides a durable reservation or predicts actual backend peak RSS.

This diagnostic does not perform a memory/disk fit decision. A load still requires authoritative host admission and representative-device evidence for stronger fit, performance or reclamation claims. `on_demand` roles are deferred and excluded from current planned residency, not pre-approved for later loads.

## Studio single-model capacity check (M8.4 bounded slice)

Models & Runtimes includes a read-only **Check capacity** action in a selected model detail. It constructs a temporary one-role, warm-intent ApplicationProfile using the exact configured model key and the server-provided task descriptor, then calls the existing admin-only plan endpoint. No profile is stored; no model is started or stopped. The UI displays configured-budget decision separately from live-policy admission preview, with a canonical reason, measured-vs-unavailable host RAM, pressure and free bytes on the configured model-cache volume. Disk fit and observed peak memory stay unverified. A non-rejected snapshot never authorizes loading. Results persist across UI refreshes as timestamped evidence until the selected model changes; 404/422 failures explain recovery without leaking server-side details. This is not yet the full multi-role ApplicationProfile editor or hardware optimization recommender.

## Acceptance and follow-up

Unit regression lives at tests/test_application_profile.py. M8.3 uses deterministic ResourceManager/MemoryEnvelope snapshots; tests cover mixed residency, duplicate roles, incomplete estimates, over-budget lower bounds, no budget, unaccounted runtimes and path-free API responses. M8.4 still needs a non-mutating Studio UI.

Before shipping a stable application-profile API, test the canonical route/admission parity with actual HTTP composition and resolve schema compatibility with the standalone consumer SDK.
