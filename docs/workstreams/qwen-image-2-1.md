# Qwen Image 2.1 Local Generation

Status: active
Owner: Korgis runtime and public inference API
Read when: implementing or coordinating local image-generation support

## Goal

Make `qwen-image-2.1` a first-class Korgis resident runtime that can generate local images through a stable, capability-gated `POST /v1/images/generations` API while preserving existing LLM/VLM/ASR behavior.

## Non-goals

- Image editing in the first vertical slice.
- Transparent/RGBA generation in the first vertical slice.
- Claiming Apple Silicon performance, memory reclamation or production readiness without representative device evidence.
- Reusing chat-completion semantics for image generation.
- Adding image-generation execution to normal CI or silently downloading the model during tests.

## Invariants

- Artifact, configured model, resident runtime and default route remain distinct states.
- Image generation is a distinct task with text input and image output; it is not inferred from VLM capability.
- Chat requests sent to an image-only runtime fail closed before backend invocation.
- Image generation uses the existing runtime lifecycle/lease boundary and remains bounded by `max_concurrent_requests`.
- Public responses expose image bytes/metadata, never local model or filesystem paths.
- Model/backend settings remain registry/config driven.
- Deterministic tests use fake pipelines and cannot be cited as Apple Silicon hardware evidence.

## Work graph

| ID | Work | Owns/writes | Depends on | Parallel | State |
| --- | --- | --- | --- | --- | --- |
| QI-1 | Canonical image-generation capability contract | `src/local_llm_server/core/*`, capability tests | — | yes | ACTIVE |
| QI-2 | Diffusers model source + runtime backend | registry/config/model_sources/backend + unit tests | QI-1 contract shape | yes | ACTIVE |
| QI-3 | OpenAI-compatible image generation HTTP route | modular product API + route tests | QI-1, QI-2 interface | no | BLOCKED |
| QI-4 | Registry entry + docs/package extra | registry, pyproject/lock, API/config docs | QI-1, QI-2 | yes | READY |
| QI-5 | Integration validation + experiments handoff | CI/evidence/docs | QI-1..QI-4 | no | BLOCKED |
| QI-6 | Image editing / RGBA follow-up | future image contract | QI-5 | no | BLOCKED |

Allowed states: `READY`, `ACTIVE`, `BLOCKED`, `DONE`.

## Current executable slice

`QI-1 + QI-2`

Acceptance:

- a registry entry can truthfully declare `image_generation`, text input and image output;
- the capability descriptor rejects chat for that entry;
- a fake Diffusers pipeline produces PNG bytes through a dedicated image-generation engine;
- missing optional image dependencies fail with an actionable install message;
- no real model is loaded in deterministic tests.

Validation:

- `uv run --frozen pytest tests/test_capabilities.py tests/test_core_contracts.py tests/test_registry_validation.py tests/test_config_multimodal.py tests/test_image_generation_engine.py -q`
- `uv run --frozen ruff check src/ tests/ --select E9,F63,F7,F82`

## Integration points

- `TaskType.IMAGE_GENERATION` and `CapabilityDescriptor` are the canonical task/capability boundary.
- `DiffusersImageEngine.generate_image(...)` is the backend interface consumed by the public route.
- `ProductRuntimeManager.lease_runtime(...)` remains the lifecycle/concurrency owner.
- `POST /v1/images/generations` is the public application-facing boundary used by experiments.

## Durable documentation destinations

- `docs/http-api-reference.md`: image-generation endpoint and response semantics.
- `docs/configuration-reference.md`: Diffusers image runtime settings.
- `docs/current-state.md`: integrated support and remaining real-device evidence.
- `README.md`: only after the capability is integrated and validated.
- tests/contracts: executable truth.

## Completion

The workstream is complete only when code, capability declarations, registry/config, HTTP behavior, deterministic tests, package dependencies, integration validation and durable docs agree. Representative Apple Silicon execution remains an explicit release evidence obligation unless performed and recorded separately.
