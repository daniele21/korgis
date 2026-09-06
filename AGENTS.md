# Korgis — Coding Agent Guide

Korgis is a local-first AI control plane/evaluation harness exposing stable text, vision and transcription boundaries while specialist engines own inference. The server owns runtime lifecycle, resource admission/scheduling, privacy policy, execution identity, observability and reproducible evaluation.

## Durable invariants

- Artifact, configured model, resident runtime and default route are distinct states.
- Unsupported task/modality combinations and remote media fail closed unless explicitly enabled.
- Public runtime identity is path-free and excludes secrets/prompts/outputs/private paths.
- Measured, estimated, configured and unavailable evidence remain distinct.
- Hardware/performance/reclamation claims require representative Apple Silicon evidence; deterministic CI never becomes target-hardware proof.
- Pressure eviction stays disabled until its evidence gate is satisfied; streaming/cancellation claims require real protocol support.
- Build version, source revision and unique build identity remain distinct; project processes/listeners/temp/evidence are bounded and cleaned.

## Ownership

| Change | Owner | Direct consumers / proof |
| --- | --- | --- |
| Public task/capability contract | `src/local_llm_server/core/` | composition/adapters/contract tests |
| Runtime/residency/lifecycle | runtime/product runtime owners | resource/scheduler/eviction tests |
| HTTP product policy | `src/local_llm_server/product_composition.py` | API/middleware/product tests |
| Runtime identity/evidence | `runtime_identity*` | identity API/evidence tests |
| Evaluation | `evaluation*` | API/Studio/evaluation tests |
| Browser acceptance | `tests/e2e/` | `.engineering/e2e.json` + Studio source |
| Product UI | `design/ux-contract.json` | brand/static UI/design skill |
| Real-device evidence | `docs/device-evidence-runbook.md` | Apple Silicon evidence owners |
| Build/release | `deploy.sh`, `release.sh` | commands/workflows |

Follow applicable scoped `AGENTS.md`; extend the canonical owner before parallel state/policy and inspect material consumers for shared boundaries.

## Read by task

| Task | Read now |
| --- | --- |
| Pure docs/copy | affected source/links; `docs/README.md` only if ownership unclear |
| Behavior/bug/contract | `skills/structured-change/SKILL.md`, `skills/validate-change/SKILL.md`, relevant commands |
| Material UI | above + `skills/design-product-experience/SKILL.md`, relevant `design/*` |
| Integration/release | `skills/preflight-change/SKILL.md`, commands, affected `.engineering/e2e.json` |
| Missing deterministic remote gate | `skills/remote-preflight/SKILL.md` |
| Persistent multi-session work | `skills/plan-workstream/SKILL.md` + active plan; finalize with `skills/finalize-workstream/SKILL.md` |

## Delivery and evidence

- **ITERATION**: focused owner-local falsification; no exact-head/full-diff/docs/publication ceremony per edit.
- **INTEGRATION** (`PR -> dev`): coherent outcome, current affected docs, exact candidate/base, required automated gates and affected deterministic Studio/API E2E. Material UI/UX integration journeys require `FULL_MEDIA`. Required Apple Silicon evidence is explicit but `DEFERRED_TO_RELEASE`.
- **RELEASE** (`dev -> main`): `FULL` plus release-critical package/E2E/security/L1-L2 and every applicable required real-environment confirmation.

`scripts/select_validation_profile.py` resolves risks -> concrete gates -> profile. Profiles are shorthand; selector/global workflow/dependency machinery fails safe FULL. Reuse evidence only when head/tree/base/gates/profile/material E2E identity remain equivalent. Missing local tooling never makes the user the fallback runner.

## Context, diagnosis and completion

`.engineering/documentation-policy.json` owns bounded reading routes. Use `python3 scripts/verify_agent_context.py --route bug --format json`, optionally `--path`/`--workstream`; routes estimate context cost, not validation scope.

For meaningful work state observable outcome, owner, preserved invariants and proof. Classify failures before patching; each failed repair needs a falsifiable hypothesis. After two failed repairs with the same signature, change diagnostic strategy and gather discriminating evidence before a third. On resume refresh head/tree/base; checkpoints are pointers, not current-source proof.

Before integration update affected canonical docs. Transfer durable truth and deferred release obligations before deleting completed plans. Never weaken runtime/privacy/evidence invariants, expose private state, bypass cleanup or overclaim deterministic evidence as Apple Silicon proof.
