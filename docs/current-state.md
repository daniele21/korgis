# Current State

Status: active
Last reviewed: 2026-09-27

Local LLM Server is a local-first multi-backend **runtime control plane for local AI applications**, with Local LLM Studio as its browser control plane. Hosted CI proves deterministic software contracts; hardware- and human-dependent claims require matching real evidence.

## Engineering baseline

- Standard: `daniele21/repo-template-sw` **0.8.0**, revision `6677c5349d64ea6d935f1b460d03a47c236821bc`.
- Target maturity: **L2** across Python, local-AI, TypeScript, macOS and `product-ui`.
- Repository-side L2 acceptance: `d528b6c5b676e705e7ccf24800929da6d5534203`.
- Representative Apple Silicon evidence is accepted for TH-E1, EV-3, HE-2, RES-2 and RRG-5.
- Bounded manual accessibility and representative-user usability evidence is accepted for tested source `a29e77c1ce4e65294440cfe4fc47e33c92173096`.
- Full L2 evidence is complete; the current action is final FULL publication preflight and promotion to `main`.

## Accepted deterministic surface

Repository Health blocks on architecture, resource, lifecycle, security, repeatability, built-surface, product-experience, design-system, documentation and real-evidence-bridge fitness. Separate gates cover performance regression, artifact lifecycle, security, package smoke, Python 3.10/3.11/3.12, Ruff, Playwright and zero residue.

Runtime hardening remains fail-conservative: routing/accounting persists until teardown succeeds; failed teardown stays retryable; resident and transient work share one configured memory budget; queued work reserves no transient memory; optional global execution admission bounds cross-runtime compute while preserving runtime semaphores/backend batching. Streams retain ownership through the final byte. Missing memory evidence stays unavailable.

Managed `llama_server` uses the attributable llama.cpp `v0.3.0` feature floor by default. Explicit executable selection is authoritative. Local LLM Server maps admitted concurrency to `--parallel`; llama.cpp owns runtime-local batching/KV. Automatic pressure eviction remains disabled.

Automated E2E proves deterministic browser/API journeys and installed-wheel behavior independently from representative hardware. New production-backend, performance, native cancellation, thermal/power or cross-device claims still require matching representative evidence.

## Accepted real evidence

The 2026-08-31 representative Mac campaign accepted:

- TH-E1 thinking OFF and ON-hidden;
- EV-3 two comparable attribution-safe 10-sample, seed-0 reasoning-OFF runs;
- HE-2 two compatible verified 3-cycle reclamation reports;
- RES-2 bounded admit/account/infer/unload/reject behavior;
- RRG-5 two compatible reports covering two-model residency, transient overlap, clean accounting and shutdown-under-load.

Post-stop memory deltas remain observational. `automatic_eviction_recommendation=not_provided`, `reclamation_safety_claim=false` and `production_safety_claim=false`.

Product experience evidence is retained in:

- `docs/evidence/manual-accessibility-2026-08-31.json`;
- `docs/evidence/representative-usability-2026-08-31.json`;
- `docs/evidence/product-ui-evidence-summary-2026-08-31.json`.

All required checks/journeys are acceptance-ready with no blocking finding. No raw prompt/output/private machine content is retained.

## Active workstreams

- Qwen Image 2.1 local generation: capability, BF16 Diffusers runtime, MFlux Q8 profile,
  image HTTP admission and `/v1/images/generations` are integrated. QI-8 now has a versioned
  representative-device runner and deterministic multi-file checkpoint verification. The runner
  can retain startup/generation latency, process-RSS/available-memory observations, runtime
  identity and local output artifacts without serializing prompt text/base64/private paths.
  **No representative Q8 Mac report has been accepted yet**, so Apple Silicon memory-fit,
  performance, thermal, reclamation and production-safety claims remain unproven.
  See `docs/workstreams/qwen-image-2-1.md`.

## Durable references

- `docs/architecture.md`
- `.engineering/e2e.json`
- `docs/resource-regression-contract.md`
- `docs/performance-regression.md`
- `docs/repeatability-contract.md`
- `docs/product-experience-validation.md`
- `docs/device-evidence-runbook.md`
