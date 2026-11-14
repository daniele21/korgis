# Current State

Status: active
Last reviewed: 2026-11-14

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
  M8.4 Studio, M9–M10 and M12 are incomplete; production embedding
  requires loopback authorization and owned process/listener proof (#229).
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
  larger-model and Performance Lab comparison evidence outstanding.
  No `dev -> main` promotion until FULL and REAL_ENVIRONMENT gates.

## Reconciled branches and blocked release (2026-11-14)

PR #274 merged `main` history and `dev` behavior without dropping
either branch; `dev` at `a002a182e955` preserves host-memory
admission, v1 attribution/opt-ins, specialist per-model binary floors,
Qwen Image memory controls, RSS/CPU observation and embedded sessions.
The Mac runner retains separate lightweight and pinned native lanes.
FULL release CI [#43647301180](https://github.com/daniele21/korgis/actions/runs/43647301180)
and native CLI/Metal [#43647301201](https://github.com/daniele21/korgis/actions/runs/43647301201)
PASS. A current source-pinned
[read-only Mac preflight #43649055434](https://github.com/daniele21/local-runner-infra/actions/runs/43649055434)
refused actual 0.8B Korgis admission and 4B inventory headroom;
the cached 4B digest also differs from the registered artifact.
No v1 release reclamation, multi-resident shutdown or SSE-cancel
proof was obtained. LC7 remains **blocking**, as does external
endpoint regression under its separate owner; `main` is unchanged.

## Durable references

`docs/architecture.md`, `.engineering/e2e.json`,
`docs/device-evidence-runbook.md`, `docs/resource-regression-contract.md`,
`docs/performance-regression.md`, `docs/repeatability-contract.md`,
and `docs/product-experience-validation.md`.
