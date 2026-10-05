# Active workstreams

This directory contains only substantial active work that needs explicit dependency, ownership and state coordination.

Use the `repo-template-sw` lifecycle:

- one workstream file owns both plan and progress;
- use only `READY`, `ACTIVE`, `BLOCKED`, `DONE` for executable slices;
- parallel work must have explicit non-conflicting write boundaries or a defined integration point;
- durable behavior belongs in owning docs/tests, not permanently in workstream plans;
- when acceptance is satisfied, transfer durable truth, update `docs/current-state.md`, and delete the completed workstream by default.

Do not create separate plan/progress/status files for the same workstream. Git history owns implementation history.

## Active

- [Request Resource Telemetry v1](request-resource-telemetry-v1.md) — per-request CPU/RAM evidence with explicit attribution and application-facing inference telemetry.
- [Qwen Image 2.1 Local Generation](qwen-image-2-1.md) — first-class local image-generation capability, Diffusers runtime, API integration and validation.
