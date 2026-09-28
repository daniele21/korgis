# Deterministic product E2E tests

This suite starts the real FastAPI/middleware/UI stack behind a real loopback socket, but uses deterministic in-process inference instead of production models. It proves assembled software behavior without pretending to be Apple Silicon/model/backend evidence.

Two lanes share the run-owned fixture:

1. Chromium covers user-visible control-plane journeys.
2. An external HTTP client covers application-consumer contracts without DOM or product-internal calls.

Internal combinatorial failure branches remain at the lowest sufficient Python test level; E2E adds only assembled-boundary evidence.

## Evidence modes

`.engineering/e2e.json` is canonical. UI presence does not imply video.

- `control-plane-status-and-navigation` — `SCREENSHOTS`;
- `evaluation-review-and-repeatability` — `SCREENSHOTS`;
- `chat-inference-and-recovery` — `FULL_MEDIA`, because progress/failure/recovery is sequential;
- `model-runtime-management` — `FULL_MEDIA`, because residency/lifecycle visibility is sequential;
- `application-api-consumer-contract` and `owned-lifecycle-cleanup` — `ASSERTIONS`.

`playwright.config.js` therefore runs ordinary journeys with screenshots and no video, while tests tagged `@full-media` run in the video-enabled project. `verify_media_evidence.js` reads the E2E contract and fails when a mapped passing journey lacks the evidence required by its declared mode.

## Deterministic fixture

The fixture owns two synthetic runtime identities:

- `e2e-switchable` / `org/e2e-switchable` returns `42` and is the default text runtime;
- `e2e-alt` / `org/e2e-alt` returns `84` and exposes chat, vision-language and transcription tasks.

Reserved probes such as `[slow-status]`, `[backend-error]` and `[parallel-probe]` exercise progress, backend failure/recovery and deterministic cross-runtime overlap. They are test probes, not performance measurements.

## What hosted E2E does not prove

Hosted CI does not load a production model, execute production llama.cpp/MLX on Apple Silicon, prove native allocation reclamation/unified-memory pressure, or establish production latency/throughput/quality/thermal/power behavior. Those claims remain owned by representative/target Apple Silicon evidence and `docs/device-evidence-runbook.md`.

## Run-owned lifecycle

`fixture_runner.py` creates an isolated temporary root and run identity, owns the child server and forwards bounded shutdown. The server validates the ownership marker before using mutable evaluation state. The runner removes only its proven root and verifies root/listener cleanup.

`verify_residue.py` is an independent post-run verifier and never deletes residue. CI runs it with `if: always()` so cleanup is checked after both success and failure.

## Privacy-safe evidence retention

The deterministic fixture must not ingest user models, prompts, outputs or private corpora. During execution, Playwright may create screenshots/videos needed by the selected evidence modes. CI retains only the synthetic PNG/WebM artifacts required by those modes plus `ui-e2e-media-manifest.json`; it does **not** publish raw Playwright traces or JSON error reports.

On failure, `prepare_failure_evidence.py` creates a separate allow-listed manifest containing only run/source identity, relative E2E test file, test title, status and duration. Error text, stdout/stderr, traces, page content and absolute paths are excluded. Evidence retention is bounded.

## Local execution

Canonical setup/E2E intent is in `.engineering/commands.json`:

```bash
npm ci --ignore-scripts --no-audit --no-fund
npx playwright install chromium
npm run test:e2e
node tests/e2e/verify_media_evidence.js test-results/playwright-results.json .engineering/e2e.json
python tests/e2e/verify_residue.py
```

Dependency audit is a separate security gate; it is not part of every E2E iteration. On Ubuntu CI Chromium uses `npx playwright install --with-deps chromium`.
