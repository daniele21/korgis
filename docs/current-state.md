# Current State

Status: active
Last reviewed: 2026-11-16

Korgis / Local LLM Server is a local-first multi-backend AI runtime
control plane; Local LLM Studio provides browser control. Hosted tests
prove deterministic contracts, never representative hardware claims.

## Engineering baseline

- Standard `daniele21/repo-template-sw` 0.10.0
  (`f392e4ded5cd3ec5efbf806934789c6cb9f10ded`); L2 Python,
  local-AI, TypeScript, macOS and product-UI.
- Repository-side L2 accepted at `d528b6c5b676e705e7ccf24800929da6d5534203`.
  Representative TH-E1, EV-3, HE-2, RES-2 and RRG-5 accepted
  2026-08-31 for that tested source and scope.
- Bounded manual accessibility/usability evidence accepted for
  `a29e77c1ce4e65294440cfe4fc47e33c92173096`;
  see `docs/evidence/product-ui-evidence-summary-2026-08-31.json`.

## Integrated runtime contracts

Repository Health and CI cover architecture, resources, security,
lifecycle/repeatability, build, package, documentation, Python 3.10–3.12,
Ruff, Playwright and zero residue. Inference remains fail-conservative:
runtime leases and route accounting survive failed teardown; streams
retain ownership to the last byte. Resident/transient memory share
one budget; queued work reserves none, and unknown host observations
never become zero. Explicit `llama-server` binary selection wins;
Korgis maps admitted concurrency to `--parallel` while llama.cpp owns
native batching/KV. Automatic pressure eviction stays **disabled**.

macOS has parent-death supervision, bounded swap/derived-pressure
observation, guarded admission and optional native Swift observer.
Pressure may trim Korgis response cache, not authorize automatic
model eviction; post-stop memory deltas remain observational.

## Active developer integration (`dev`)

- Experimental SDK/auth (#227/#233), ApplicationProfiles (#230/#231)
  and M8 capacity previews (#277–#280) are not a stable embedding API
  or proof of device fit. Multi-role M8.4, M9–M10, M12 and authorization
  (#229) remain pending; ClosedRoom migration is consumer-owned.
- llama.cpp v1 (#262) remains **opt-in**, with explicit FP8/MoE flags;
  independent Python 0.3.36 is not its C API. Real HTTP/stream/unload
  plus Python canaries [PASS twice](https://github.com/daniele21/local-runner-infra/actions/runs/43629484166);
  historical v0.3/v1 lifecycle [6/6 PASS](https://github.com/daniele21/local-runner-infra/actions/runs/43633636676);
  [8/8 managed resource cycles](https://github.com/daniele21/local-runner-infra/actions/runs/43635780313)
  report bounded RSS/HTTP observations. Neither larger-model
  [admission refusal](https://github.com/daniele21/local-runner-infra/actions/runs/43636178878)
  nor refused SSE startup qualifies production reclamation;
  LC7 **release-blocked**. See
  `docs/workstreams/llama-cpp-v1-integration.md`.

## Evidence boundaries

Earlier 2026-08-31 device procedures accepted TH-E1 thinking policy,
EV-3 repeatable evaluations, HE-2 reclamation, RES-2 admit/unload/reject
and RRG-5 concurrent residence and shutdown-under-load for their
particular baseline. Their conclusions do **not** transfer to the new
llama.cpp v1 hardware candidate. `automatic_eviction_recommendation`
remains `not_provided`, and both `reclamation_safety_claim` and
`production_safety_claim` remain `false`.

## Active workstreams

- Qwen Image 2.1: BF16 Diffusers, MFlux Q8 and shared image API
  integrated. Separate stable-diffusion.cpp Q4_K_M GGUF runtime,
  text encoder and VAE, verified downloads and bounded subprocess
  lifecycle pass deterministic validation. Native peak memory,
  latency, thermal and reclamation remain unproven; next is
  benchmark/representative device evidence.
  See `docs/workstreams/qwen-image-2-1.md`.
- llama.cpp v1: release-critical LC7 memory/reclamation,
  larger-model and Experiments endpoint comparison evidence outstanding.
  No `dev -> main` promotion until FULL and REAL_ENVIRONMENT gates.

## Release state (2026-11-16 UTC)

Previous `dev@427de51d` deterministic
[RELEASE/FULL #43773565474](https://github.com/daniele21/korgis/actions/runs/43773565474),
[CLI/Metal #43773565398](https://github.com/daniele21/korgis/actions/runs/43773565398),
[memory-pressure #43773565558](https://github.com/daniele21/korgis/actions/runs/43773565558),
[Repository Health #43773565483](https://github.com/daniele21/korgis/actions/runs/43773565483),
and [local-ci/mac #43773579801](https://github.com/daniele21/local-runner-infra/actions/runs/43773579801)
are **PASS**, but do not qualify LC7 hardware behavior.
Current `dev@18c8d05d` [FULL #43824698200](https://github.com/daniele21/korgis/actions/runs/43824698200)
**FAILS**: pre-existing `diskcache` security exception expired
2026-11-15. See [security issue #286](https://github.com/daniele21/korgis/issues/286).
Do not bypass the audit or auto-extend the exception.

Physical-Mac [4B preflight #43770979460](https://github.com/daniele21/local-runner-infra/actions/runs/43770979460)
refused with 7.55 versus 9.78 GB required, plus GGUF hash mismatch.
The approved [0.8B canary #43773079533](https://github.com/daniele21/local-runner-infra/actions/runs/43773079533)
was refused **before download/inference** twice on identical runtime source
`dev@34f8e12`: attempt 1 had 7.34 GB versus 8.46 GB required;
2026-11-16 attempt 2 had 8,053,571,584 versus 8,455,716,864
bytes, with 5,529,864,765 bytes already used swap. This is an
**environment/host-capacity block**, not an observed native regression.
No safety margin or foreign process changed. Stop repeated retries without
new headroom. SSE abort, HE-2/RES-2/RRG-5 and Experiments endpoint
comparison remain **PENDING**; `main` unchanged.

## Durable references

`docs/architecture.md`, `.engineering/e2e.json`,
`docs/device-evidence-runbook.md`, `docs/resource-regression-contract.md`,
`docs/performance-regression.md`, `docs/repeatability-contract.md`,
and `docs/product-experience-validation.md`.
