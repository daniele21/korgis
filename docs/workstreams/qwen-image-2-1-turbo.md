# Qwen Image 2.1 Turbo — Korgis

Status: active
Last reviewed: 2026-11-08
Owner: Korgis local image-generation runtime
Read when: implementing, validating or reviewing Qwen Turbo GGUF inference

## Outcome

Run `qwen-image-2.1-turbo-gguf-q4km` through existing Korgis download, residency,
scheduler and `POST /v1/images/generations` contracts, with an exact eight-step
Turbo schedule. Standard Qwen Image 2.1 remains a different selectable model.
No local inference service is owned by Experiments.

## Invariants

- The new runtime is local only: no cloud fallback, new scheduler or ad-hoc child process.
- Three required artifacts have pinned Hugging Face revision + SHA-256: Turbo
  Q4_K_M diffusion, ConvRot INT8 Qwen3VL text encoder and BF16 VAE.
- Native `sd-server` receives exact `sample_params.custom_sigmas` for 8 steps,
  Euler and CFG 1; incompatible overrides fail before native submission.
- Korgis keeps the subprocess lease, request limits, safe path-free provenance
  and owned teardown. Artifact size is not claimed as memory use.
- Model license is Qwen Research License (research/evaluation).
- Fake-server tests are not evidence of Apple Silicon generation performance.

## Delivery state

| Slice | State | Evidence / next gate |
| --- | --- | --- |
| K0 — reconcile required GGUF parts from main into dev | DONE | PR #258, hosted CI and Repository Health passed |
| K1 — pinned separate Turbo artifact bundle | DONE | PR #259 deterministic registry tests included in green CI |
| K2 — model-specific exact Turbo sigma preset | DONE | PR #259 native fake-job/override tests included in green CI |
| K3 — config, API and lifecycle integration | DONE | PR #259 config + API contract tests included in green CI |
| K4 — representative Mac proof | BLOCKED | K1-K3 CI green; real model artifacts, compatible sd-server and representative Mac evidence pending |
| K5 — Q8 or MLX variants | READY | Only after K4 + benchmark evidence |

Independent source streams K1 and K2 were developed on separate branches,
combined into feature PR [#259](https://github.com/daniele21/korgis/pull/259).
The full design is recorded in planning PR [#257](https://github.com/daniele21/korgis/pull/257).

## Native reference & configuration

- Checkpoint: `abenzerps/Qwen-Image-2.1-Turbo-Quantized`, pinned to
  `24d33ddeffd21374e8b04a84f93eb73a645fc74e`.
- Runtime: `stable_diffusion_cpp_image`; model key
  `qwen-image-2.1-turbo-gguf-q4km`.
- Initial request: 1024×1024, PNG, seed 42. Model defaults: eight steps,
  Euler, CFG 1, `qwen-image-2.1-turbo-v1` custom sigma schedule.
- Built-in startup profile uses disk-backed diffusion weights, CPU
  text encoder and tiled VAE as a conservative starting point, not
  a proven memory target.

## Validation

Deterministic commands:

```bash
uv run --frozen pytest \
  tests/test_registry_validation.py \
  tests/test_config_multimodal.py \
  tests/test_stable_diffusion_cpp_compat.py \
  tests/test_stable_diffusion_cpp_image_engine.py \
  tests/test_qwen_image_21_turbo_registry.py \
  tests/test_qwen_image_21_turbo_sampling.py \
  tests/test_qwen_image_21_turbo_integration.py \
  tests/test_image_generation_api.py -q
uv run --frozen ruff check src/ tests/ --select E9,F63,F7,F82
```

CI / Repository Health on PR #259 passed at commit `138b0a9ffce19b3877a9ce01c02efec4896d5869`: 916 Python tests, lint and guards green. After CI is green, the Mac gate must verify a real PNG output from one resident
Korgis model, correct runtime/config/seed/sigma provenance, cold/warm timings,
bounded resource observation, no unexpected downloads, and clean process teardown.
Do not interpret unit tests as Mac memory-fit, production readiness or visual
quality proof. An unverified/unsupported native `custom_sigmas` path must fail,
not silently revert to a generic scheduler.

## Consumer handoff

Experiments adds a separate Qwen Turbo arm to its existing image-generation
benchmark. It must call the Korgis endpoint, record actual seed/provenance and
keep prompt suites, evaluation, artifacts and dashboard in Experiments.
OpenAI/Gemini routing through Korgis is a later distinct capability.

Sources:
- https://huggingface.co/abenzerps/Qwen-Image-2.1-Turbo-Quantized
- https://github.com/leejet/stable-diffusion.cpp/blob/master/examples/server/api.md
