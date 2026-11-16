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

- Experimental Python SDK (#227), auth (#233) and M8.1–M8.2
  ApplicationProfile parser/validator (#230) do not establish a stable
  application embedding API. M8.3 dry-run and optional validate/plan
  endpoints (#231) do **not** prove actual memory admission.
  M8.3 capacity/admission/storage previews (#277–#279) and the M8.4
  single-model Studio check (#280) are read-only, not device-fit proof.
  Multi-role M8.4, M9–M10, M12 and embedding authorization/ownership
  (#229) remain pending.
  ClosedRoom migration is consumer-owned.
- llama.cpp v1.0.0 (#262) is an **opt-in external** runtime with explicit
  FP8/MoE features. Independent Python 0.3.36 is **not** v1 C API and
  does not auto-activate. Real Korgis-managed HTTP/stream/unload plus
  separate Python GGUF canaries PASS twice
  ([#43629147770](https://github.com/daniele21/local-runner-infra/actions/runs/43629147770),
  [#43629484166](https://github.com/daniele21/local-runner-infra/actions/runs/43629484166)).
  Exact-tag historical v0.3 vs v1 managed lifecycle also passes
  **6/6 physical-Mac cycles**
  ([#43633636676](https://github.com/daniele21/local-runner-infra/actions/runs/43633636676))
  after shallow-build version attribution fix (#271, merged to dev).
  Subsequent Korgis-only resource telemetry
  [#43635780313](https://github.com/daniele21/local-runner-infra/actions/runs/43635780313)
  passes 8/8 managed 0.8B cycles with observed native RSS,
  96-token HTTP timings and zero sampled swap in/out.
  The 4B read-only safety preflight [#43636178878](https://github.com/daniele21/local-runner-infra/actions/runs/43636178878)
  refuses launch; v1 streaming-disconnect follow-ups also stopped
  before model startup on canonical host admission (#43636531152).
  None establishes pressure-safe production reclamation;
  LC7 **release-blocked**.
  See `docs/workstreams/llama-cpp-v1-integration.md`.

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

## Release state (2026-11-15 UTC)

`dev@34f8e12` passed exact-head RELEASE/FULL
[#43772076129](https://github.com/daniele21/korgis/actions/runs/43772076129)
and native CLI/Metal
[#43772076059](https://github.com/daniele21/korgis/actions/runs/43772076059).
Mac [4B #43770979460](https://github.com/daniele21/local-runner-infra/actions/runs/43770979460)
refused at 7.55 GB reclaimable / 9.78 GB required with a different
cached quantization. The exact-head
[0.8B canary #43773079533](https://github.com/daniele21/local-runner-infra/actions/runs/43773079533)
refused **before download/inference** at 7.34 GB available vs
8.46 GB floor; 5.60 GB swap used is not additional free RAM.
Earlier SSE startup #43651722308 refused cleanly; live SSE abort,
HE-2, RES-2, RRG-5 and Experiments external comparison are pending.
`main` unchanged; LC7 release-blocked. No safety margin reduced.

## 2026-11-16 verification update

- Current `dev@427de51d` deterministic RELEASE/FULL [#43773565474](https://github.com/daniele21/korgis/actions/runs/43773565474), CLI/Metal [#43773565398](https://github.com/daniele21/korgis/actions/runs/43773565398), native memory-pressure [#43773565558](https://github.com/daniele21/korgis/actions/runs/43773565558), Repository Health [#43773565483](https://github.com/daniele21/korgis/actions/runs/43773565483) and `local-ci/mac` [#43773579801](https://github.com/daniele21/local-runner-infra/actions/runs/43773579801) are **PASS**; these are not LC7 representative-hardware acceptance.
- On [approved 0.8B physical-Mac canary #43773079533, attempt 2](https://github.com/daniele21/local-runner-infra/actions/runs/43773079533), **host admission refused before download/inference**: reclaimable estimate 8,053,571,584 bytes versus unchanged 8,455,716,864-byte requirement; swap already used 5,529,864,765 bytes is not additional available RAM. Managed HTTP and independent Python inference were both `not_run`. This was the unchanged runtime source `dev@34f8e12`; newer `dev@427de51d` changed docs only.
- Classify as **environment/host-capacity blocked**, not a native regression. Do not repeat identical tests without substantially improved memory headroom; retain fail-conservative policy. Actual SSE cancellation, LC7 HE-2/RES-2/RRG-5 and Experiments external endpoint comparison remain **PENDING**. `main` remains unchanged.

## Durable references

`docs/architecture.md`, `.engineering/e2e.json`,
`docs/device-evidence-runbook.md`, `docs/resource-regression-contract.md`,
`docs/performance-regression.md`, `docs/repeatability-contract.md`,
and `docs/product-experience-validation.md`.
