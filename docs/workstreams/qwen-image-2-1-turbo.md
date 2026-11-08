# Qwen Image 2.1 Turbo via Korgis — Development Workstream

Status: READY (planning only; no implementation/evidence claimed)
Date: 2026-11-08
Owner: Korgis runtime + image-generation public API
Target integration: `dev` after baseline reconciliation; no direct model changes on `main`
Consumer: `daniele21/experiments/experiments/image-generation-benchmark`

## 1. Observable outcome

From one Korgis installation, an operator can download, verify, start, generate with, inspect and stop **Qwen Image 2.1 Turbo GGUF Q4_K_M** through the existing Korgis lifecycle and `POST /v1/images/generations`. The default request uses the model's 8-step Turbo schedule, not the 20-step standard Qwen Image 2.1 profile. The output has attributable runtime/artifact/sampling evidence and is consumable by Experiments without an alternative inference service.

User-visible success:
1. `local-llm models` lists the new, separate `qwen-image-2.1-turbo-gguf-q4km` registry identity.
2. `local-llm download qwen-image-2.1-turbo-gguf-q4km` downloads and validates all three artifacts; `--no-download` fails closed on an incomplete bundle.
3. `local-llm serve --model qwen-image-2.1-turbo-gguf-q4km --enable-admin-api --no-download` starts one owned resident `sd-server` and the API returns a valid image for a 1024x1024 seed-42 request.
4. Evidence identifies Turbo vs standard 2.1, exact artifact revisions/hashes, backend identity, effective sample steps/CFG/Euler/Turbo sigma schedule, output dimensions, latency and any *observed* memory statistics (unknown is not zero).
5. Existing standard Qwen 2.1 and non-image clients continue to work; shutdown releases the owned process and does not kill unrelated processes.

This is a **local-generation workstream**. A common Korgis gateway for OpenAI/Gemini requests is a separate cross-repo phase; do not make a cloud gateway, editing, UI redesign, or MLX rewrite a prerequisite for the local Turbo proof.

## 2. Verified starting point and critical branch gate

- `main` already contains a resident `stable_diffusion_cpp_image` implementation, a managed 3-artifact Qwen Image 2.1 GGUF Q4_K_M profile, capability gating, queue/resource admission, and `/v1/images/generations`.
- `dev` and `main` have **diverged** (comparison on 2026-11-08: main 49 commits ahead of dev; dev 17 ahead of main). In particular, the full GGUF registry and some compatibility/observability improvements on main are not all present on dev.
- Do **not** assume the main behavior is already on dev; do not blindly overwrite dev or bulk-replace it with main. Before K1, refresh exact refs and reconcile the required GGUF owner code/contract/tests into dev through a focused, separately validated change. Identify consumer impacts and avoid unrelated release changes.
- Existing `docs/workstreams/qwen-image-2-1.md` describes standard Qwen 2.1, not Turbo. Keep that workstream and all its model identities intact.

Canonical code owners to inspect: `src/local_llm_server/models_registry.yaml`, `registry.py`, `model_sources.py`, `stable_diffusion_cpp_image_engine.py`, `stable_diffusion_cpp_compat.py`, `image_generation_request.py`, `image_generation_api.py`, `core/contracts.py`, `config.py`, `tests/test_stable_diffusion_cpp_image_engine.py`, `tests/test_image_generation_api.py`.

## 3. Upstream requirements / model provenance

Source:
- https://huggingface.co/abenzerps/Qwen-Image-2.1-Turbo-Quantized
- https://github.com/leejet/stable-diffusion.cpp/blob/master/docs/qwen_image_2.1.md
- https://github.com/leejet/stable-diffusion.cpp/blob/master/examples/server/api.md

Initial candidate bundle (pin exact HF revision and verify per-file SHA-256 **before declaring complete**):
- diffusion: `abenzerps/Qwen-Image-2.1-Turbo-Quantized` / `qwen-image-2.1-turbo-Q4_K_M.gguf` (about 4.05 GB);
- text encoder: same source / `text_encoders/qwen3vl_8b_int8_convrot.safetensors` (about 9.35 GB);
- VAE: same source / `vae/qwen_image_2.1_vae_bf16.safetensors` (about 0.68 GB).

These are approximately 14.08 GB **of files**, not expected peak RAM. Never substitute a Qwen 2.1 standard text encoder for the Turbo recommended encoder without a separate compatibility/quality check. Reuse verified preexisting local artifacts where possible; do not require duplicate multi-GB copies. Check the Qwen Research License and do not imply commercial-use rights.

Official Turbo generation baseline:
- 1024x1024 (multiples of 32), output PNG, seed 42 for initial proof;
- Euler, guidance/CFG 1.0, **8 actual denoising steps**;
- exact sigma values: `[1.0, 0.978453, 0.95418, 0.926626, 0.89508, 0.845148, 0.704534, 0.414568, 0.0]`;
- no added Lightning/Turbo LoRA; no hidden prompt enhancement; no negative-prompt quality claims at CFG 1.

The native `sd-server` API accepts `sample_params.custom_sigmas`. Its nonempty list overrides the generated schedule and defines `len(sigmas)-1` steps. Pin and feature-check the actual deployed server binary. Do not silently fall back to a generic 8-step scheduler.

## 4. Delivery slices (one observed outcome per PR)

| ID | State | Deliverable | Owner / bounded files | Depends on | Proof |
| --- | --- | --- | --- | --- | --- |
| K0 | READY | Reconcile the GGUF serving prerequisite from main to dev without losing dev-specific changes | GGUF runtime/registry/tests only | none | exact-head diff + existing Qwen Q4 deterministic gates |
| K1 | READY | Register separate Turbo Q4_K_M bundle, provenance, reliable download/no-download and license metadata | `models_registry.yaml`, `model_sources.py`, `registry.py`, model/download tests | K0 | registry validation; fake download SHA/partial-bundle tests |
| K2 | READY | Make the Turbo sigma schedule effective in resident sd-server jobs, with strict validation and evidence | `stable_diffusion_cpp_image_engine.py`, `config.py`, optional typed core contract, backend tests | K0 | exact JSON `custom_sigmas` in native job; invalid/mismatch rejection; standard Q4 unchanged |
| K3 | READY | Stable API/capability/policy integration for Turbo and safe resource/lifecycle behavior | `image_generation_request.py`, `image_generation_api.py`, runtime/identity tests | K1+K2 | successful fake 1K API response + fail-closed and lease/teardown tests |
| K4 | READY | Controlled representative Mac E2E and performance evidence, then documentation handoff | real-runtime evidence, `docs/*` and runbook | K3 | downloadable image artifact, correct effective config, cold/warm latency, resource observation, clean stop |
| K5 | READY | Optional second Turbo quantization/MLX follow-up only if benchmark justifies it | new registry/backend workstream, not K0-K4 | K4 | separately identified, measured comparison |

### K0 — integration gate first

1. Compare exact `dev` and `main` heads/trees and inspect changes in native image serving, registry, resource telemetry and tests.
2. Port/merge only necessary GGUF prerequisites with deliberate conflict resolution; protect other dev work.
3. Run target tests and inspect source-owned CI evidence. Freeze the reconciled baseline SHA before Turbo implementation.
4. Update this plan checkpoint with the baseline SHA/target SHA, remaining conflicts or blockers.

### K1 — registry + artifact ownership

1. New key `qwen-image-2.1-turbo-gguf-q4km` with `stable_diffusion_cpp_image`; identity must differ from `qwen-image-2.1-gguf-q4km`.
2. Declare three artifact roles (diffusion/text_encoder/vae), exact file paths, pinned HF commit revision, verified expected SHA-256 for each. If the SHA cannot be verified, keep the slice blocked rather than inventing it.
3. Reuse current resolver/downloader, complete-bundle readiness and `--model-path` bundle-root semantics. Do not create a second download system.
4. Keep memory admission conservative: artifact byte counts are not proven runtime demand. Reuse existing offload/disk-backed settings as a starting profile, then validate on Mac.

### K2 — sampling correctness (the main functional change)

Prefer a **model-specific config preset** (e.g. typed/configured `image_custom_sigmas`) owned by Korgis. The benchmark should not have to send raw native options. Extend the general sd-server adapter only as far as needed:
- `sample_params.custom_sigmas` is added for this profile; no sigma key on standard-model requests unless configured.
- Require finite, nonnegative, monotonically descending values ending in 0, with at least two values; reject step-count disagreement and impossible bounds **before native inference**.
- Turbo default is 8 steps, CFG 1, Euler. Request overrides must not silently change the fixed baseline; either explicitly reject conflicting overrides or mark a distinct noncanonical experiment.
- Preserve effective requested and native sampling parameters plus named preset/version/digest in safe evidence. No private file paths.
- Prefer a trusted native `sd-server` capability/version check; unsupported sigma handling fails explicitly, never falls back to generic scheduler.

Do not expand the public API with arbitrary user-supplied sigma lists unless a real consumer needs that feature and input/security policy is separately validated.

### K3 — HTTP/runtime/lifecycle

- Keep `TaskType.IMAGE_GENERATION` / capability declaration, existing queue/global-governor, per-runtime lease, transient admission and managed `sd-server` subprocess.
- Require a resident named image runtime; unsupported model/task, missing artifacts, invalid dimensions/steps/sigmas and incompatible native binary fail before or at a bounded boundary.
- Test repeated prompts on one resident runtime, seeded generation, timeout/cancellation, process error and cleanup; no unowned process termination.
- Assert that API response and `/v1/runtime/identity` expose sufficient safe provenance to reproduce a run: correct Turbo identity, backend/version, component provenance, effective settings, bounded device information.

### K4 — real Mac evidence + handoff

Start with one seed-42 prompt and 1024x1024 result (not a 512px quality proxy). Preserve output artifact and raw metadata locally. Run subsequent warm generations and a clean shutdown to measure:
- cold model-load time vs warm generation time (report separately);
- wall-clock API request time and backend generation time when available;
- system/owned-process memory and pressure, explicitly identifying attribution limits on Apple Silicon unified memory;
- actual step count/preset, reproducibility attempts, error/timeouts, cleanup residuals.

Optionally run 512px smoke **only** as a hardware-readiness check; label it as such. Do not infer speed, RAM fit, thermal safety or quality from fake CI fixtures.

## 5. Test matrix and required gates

Deterministic / PR validation:
- registry accepts Turbo metadata but rejects missing artifact role or invalid checksums;
- multi-artifact download and `--no-download` behavior, interrupted/partial bundle and no silent alternate source;
- native job payload contains exact `custom_sigmas`, 8 steps, Euler and CFG 1;
- NaN/Inf, negative, nonmonotonic, wrong terminal sigma, mismatch of count vs steps all rejected;
- standard Qwen GGUF and MFlux paths keep current behavior;
- capability/HTTP 400 or typed errors for invalid requests; no hidden cloud fallback;
- image artifact decoding, safe metadata, backend failures, retries, cancellation and owned process teardown;
- replay multiple requests on one resident runtime; existing text/VLM/ASR tests unaffected.

Suggested scoped commands after K0 reconciles baseline:
`uv run --frozen pytest tests/test_registry_validation.py tests/test_config_multimodal.py tests/test_model_sources_sdcpp_image.py tests/test_stable_diffusion_cpp_compat.py tests/test_stable_diffusion_cpp_image_engine.py tests/test_image_generation_api.py -q`
`uv run --frozen ruff check src/ tests/ --select E9,F63,F7,F82`

For each PR, use `scripts/select_validation_profile.py`, source-owned `.engineering/commands.json`, required CI/API E2E and zero-residue checks. A test fixture passing is not representative hardware evidence. Real Mac validation is a tracked release obligation. Do not require large model downloads or paid API calls in normal PR CI.

## 6. Sequencing / parallelism

`K0 -> (K1 || K2) -> K3 -> K4 -> Experiments smoke`.

K1 and K2 may proceed in parallel **after** settling the common schema, with nonoverlapping ownership (registry/source vs sampler/engine). Converge into one small runtime slice before hardware validation. K5 is explicitly deferred.

PR naming proposal:
- `feat/qwen-turbo-baseline-reconcile`
- `feat/qwen-turbo-registry-bundle`
- `feat/qwen-turbo-sampling`
- `feat/qwen-turbo-api-runtime`
- `test/qwen-turbo-device-evidence`

## 7. Cross-repo acceptance / non-goals

Experiments must only call Korgis for this local arm; it owns prompts, seed/repetition matrix, artifacts and scoring. The first model key to map is `qwen-image-2.1-turbo-gguf-q4km`. Do not duplicate sd-server orchestration in Experiments.

The later user goal of *all requests through Korgis* (also GPT Image and Nano Banana) requires a separate Korgis cloud-provider gateway with explicit outbound/provider policy; not part of K0-K4. Do not treat an API model as a resident local runtime or book its RAM usage as local memory.

Non-goals: image editing, transparent backgrounds, MFlux Turbo as a prerequisite, UI redesign, speculative hardware claims, automatic fallback to cloud, shipping copyrighted model weights in Git, or permitting commercial use based only on technical support.

## 8. Resume checkpoint (refresh before implementation)

As of 2026-11-08:
- Verified: main has standard GGUF route and bundle; dev differs; stable-diffusion.cpp native API supports `sample_params.custom_sigmas`; upstream Turbo recommended settings are published.
- Not verified: target Mac memory fit, precise native binary version/feature compatibility, checkpoint per-artifact SHA, actual Q4 Turbo output and quality.
- First discriminating next step: **K0 reconcile GGUF baseline from main to dev**, then validate the exact sampler JSON using a fake native server before any large download.
- Record next checkpoint with exact source/base HEAD+tree SHAs, confirmed tests, rejected hypotheses and outstanding device/release evidence.
