# Representative device evidence runbook

Status: active procedure
Owner: local-llm-server
Read when: executing TH-E1, EV-3, HE-2, RES-2, RRG-5 or QI-8 on the representative Mac
Last reviewed: 2026-08-30

This runbook turns hardware-dependent evidence into repeatable commands. Private model paths stay local. Validators emit bounded public-safe summaries and never promote repository maturity or automatic-eviction policy automatically.

## Scope and safety

Run from the converged `dev` branch after installing the current environment.

```bash
MODEL="<model-key>"
MODEL_PATH="<absolute-path-to-model.gguf>"
EVIDENCE_DIR="$HOME/.local-llm-server/evidence/$(date +%F)-l2"
mkdir -p "$EVIDENCE_DIR"
```

Rules:

- use the same exact GGUF for all comparable runs;
- do not commit private model paths or raw inference content;
- serialize heavy evidence campaigns on one Mac unless the procedure explicitly requires multi-model concurrency;
- do not induce OOM, critical OS pressure or automatic eviction;
- retain negative, mixed and inconclusive observations;
- do not lower safety/repetition thresholds merely to force a successful result;
- do not generalize one-device or small-sample observations into production-safety/model-quality claims.

## 0. Verify the exact artifact

Before HE-2 and preferably before all comparable runs:

```bash
local-llm verify-artifact "$MODEL" --model-path "$MODEL_PATH"
```

The receipt remains machine-local. Single-file artifacts are hashed directly; multi-file model
directories are hashed through a deterministic manifest of sorted relative paths, file sizes and
per-file SHA-256 digests. Public evidence may expose only the aggregate digest/size and verification
grade, never the private path or private manifest stamps. If the verified artifact changes, the
cached receipt is invalidated.

## 1. Start the representative runtime

TH-E1 and EV-3 use the same running server:

```bash
local-llm serve \
  --model "$MODEL" \
  --model-path "$MODEL_PATH" \
  --backend llama_cpp \
  --enable-admin-api
```

Keep this server isolated from other heavy model loads while the comparable runs execute.

## TH-E1 — explicit thinking OFF/ON without retaining output

In a second terminal run the packaged capture bridge:

```bash
python -m local_llm_server.l2_evidence_bridge capture-thinking \
  --base-url http://127.0.0.1:8000 \
  --model "$MODEL" \
  --output "$EVIDENCE_DIR/thinking-campaign.json"
```

The command itself sends the same deterministic local workload twice:

- explicit `enable_thinking=false`, `show_thinking=false`;
- explicit `enable_thinking=true`, `show_thinking=false`.

It retains only bounded request-policy flags, HTTP/result state, whether normal application content contains a `<think>` boundary, and the public runtime identity. It does **not** retain the prompt, output, raw response or private paths.

TH-E1 is acceptance-ready only when both requests complete with normal content and neither hidden response mixes reasoning boundaries into normal application content. Typed failures are still retained truthfully but do not become a fabricated successful campaign.

## EV-3 — repeatable post-convergence evaluation

With the same verified resident runtime, execute the exact OFF workload twice.

### OFF run A

```bash
curl -sS http://127.0.0.1:8000/api/v1/evaluation/runs \
  -H 'Content-Type: application/json' \
  -d "{
    \"model\": \"$MODEL\",
    \"test_set_id\": \"general-purpose\",
    \"test_set_version\": \"1.0.0\",
    \"sample_count\": 10,
    \"seed\": 0,
    \"reasoning_policy\": \"off\"
  }" > "$EVIDENCE_DIR/evaluation-off-a.json"
```

### OFF run B

```bash
curl -sS http://127.0.0.1:8000/api/v1/evaluation/runs \
  -H 'Content-Type: application/json' \
  -d "{
    \"model\": \"$MODEL\",
    \"test_set_id\": \"general-purpose\",
    \"test_set_version\": \"1.0.0\",
    \"sample_count\": 10,
    \"seed\": 0,
    \"reasoning_policy\": \"off\"
  }" > "$EVIDENCE_DIR/evaluation-off-b.json"
```

The final bundle validator checks that the two reports have:

- `general-purpose` `v1.0.0`, exactly ten samples and seed `0`;
- identical test-set identity and sample selection;
- explicit requested/effective reasoning `off`;
- compatible verified runtime fingerprint;
- complete results where every failed sample carries an explicit error code.

An optional ON evaluation is a separate experiment and is not part of the minimum L2 bundle:

```bash
curl -sS http://127.0.0.1:8000/api/v1/evaluation/runs \
  -H 'Content-Type: application/json' \
  -d "{
    \"model\": \"$MODEL\",
    \"test_set_id\": \"general-purpose\",
    \"test_set_version\": \"1.0.0\",
    \"sample_count\": 10,
    \"seed\": 0,
    \"reasoning_policy\": \"on\"
  }" > "$EVIDENCE_DIR/evaluation-on.json"
```

Do not describe score deltas as global model-quality improvement.

## HE-2 — two verified 3-cycle reclamation reports

Stop the long-running server before starting isolated reclamation workers. Artifact verification must already have succeeded.

```bash
local-llm evidence-reclamation \
  --model "$MODEL" \
  --model-path "$MODEL_PATH" \
  --backend llama_cpp \
  --cycles 3 \
  --max-tokens 32 \
  --settle-seconds 2 \
  --no-download \
  --output "$EVIDENCE_DIR/reclamation-a.json"
```

```bash
local-llm evidence-reclamation \
  --model "$MODEL" \
  --model-path "$MODEL_PATH" \
  --backend llama_cpp \
  --cycles 3 \
  --max-tokens 32 \
  --settle-seconds 2 \
  --no-download \
  --output "$EVIDENCE_DIR/reclamation-b.json"
```

Then run the conservative reviewer without exploratory overrides:

```bash
local-llm evidence-review \
  "$EVIDENCE_DIR/reclamation-a.json" \
  "$EVIDENCE_DIR/reclamation-b.json" \
  --min-reports 2 \
  --min-complete-cycles 6 \
  --output "$EVIDENCE_DIR/reclamation-review.json"
```

Do **not** use `--allow-exploratory-identity` for HE-2 acceptance. The final bundle validator recomputes the review from the two raw reports and rejects a stored review that disagrees with the conservative recomputation.

The observed recovery state may be positive, negative, mixed or inconclusive. No automatic-eviction recommendation or production-safety claim is inferred.

## RES-2 — bounded real-device resource-policy smoke

Run the dedicated macOS safety-gated procedure:

```bash
python -m local_llm_server.resource_policy_smoke \
  --model "$MODEL" \
  --model-path "$MODEL_PATH" \
  --backend llama_cpp \
  --max-tokens 8 \
  --headroom-gib 0.5 \
  --success-margin-gib 0.5 \
  --host-safety-gib 2.0 \
  --output "$EVIDENCE_DIR/resource-policy-smoke.json"
```

The runner refuses execution when measured macOS available memory is below the model estimate plus configured success and host-safety margins. Do not lower the safety margin merely to force a pass.

A complete report must show safe admission, positive committed accounting while resident, one HTTP inference, zero committed/reserved accounting after unload, green/cold health, insufficient-budget rejection before backend construction and `automatic_eviction_exercised=false`.

## RRG-5 — repeated multi-model lifecycle, concurrency and pressure evidence

RRG-5 is an **additional runtime-governor campaign**, not a retroactive extension of the minimum L2 bundle above. Run it after RRG-1..RRG-4 are integrated and with no other heavy local-AI workload competing for the Mac.

Choose two distinct configured model keys that can legitimately be resident at the same time. For GGUF validation of the modernized RRG-2 path, prefer the validated `llama_server` v0.3 feature floor; do not silently switch the backend if the deployment under test uses another supported runtime.

```bash
MODEL_A="<first-model-key>"
MODEL_A_PATH="<absolute-path-to-first-model>"
MODEL_B="<second-model-key>"
MODEL_B_PATH="<absolute-path-to-second-model>"
REQUEST_ESTIMATE_MIB="<configured-or-calibrated-transient-total-per-request>"
RRG5_DIR="$HOME/.local-llm-server/evidence/$(date +%F)-rrg5"
mkdir -p "$RRG5_DIR"

local-llm verify-artifact "$MODEL_A" --model-path "$MODEL_A_PATH"
local-llm verify-artifact "$MODEL_B" --model-path "$MODEL_B_PATH"
```

`REQUEST_ESTIMATE_MIB` is intentionally required and has no runner default. Use the explicit transient total owned by the deployment (`resource_request_estimate_bytes`) or a conservative value chosen for an exploratory calibration run. Do not tune it downward merely to make the campaign fit in memory. The report keeps this configured accounting separate from measured RSS/available-memory observations.

Run two otherwise identical reports:

```bash
python -m local_llm_server.multi_model_device_evidence \
  --model-a "$MODEL_A" \
  --model-b "$MODEL_B" \
  --model-a-path "$MODEL_A_PATH" \
  --model-b-path "$MODEL_B_PATH" \
  --backend llama_server \
  --request-estimate-mib "$REQUEST_ESTIMATE_MIB" \
  --cycles 2 \
  --max-tokens 8 \
  --headroom-gib 0.5 \
  --success-margin-gib 0.5 \
  --host-safety-gib 2.0 \
  --settle-seconds 2 \
  --output "$RRG5_DIR/multimodel-a.json"
```

```bash
python -m local_llm_server.multi_model_device_evidence \
  --model-a "$MODEL_A" \
  --model-b "$MODEL_B" \
  --model-a-path "$MODEL_A_PATH" \
  --model-b-path "$MODEL_B_PATH" \
  --backend llama_server \
  --request-estimate-mib "$REQUEST_ESTIMATE_MIB" \
  --cycles 2 \
  --max-tokens 8 \
  --headroom-gib 0.5 \
  --success-margin-gib 0.5 \
  --host-safety-gib 2.0 \
  --settle-seconds 2 \
  --output "$RRG5_DIR/multimodel-b.json"
```

The runner is safety-gated before backend construction. Its usable configured budget includes both resident estimates, `global_max_running × request_estimate`, and the success margin; measured available host memory must additionally satisfy the host-safety margin. A safety refusal is written as evidence and exits non-zero.

Each complete cycle must demonstrate:

- both verified artifacts become simultaneously resident with attributable runtime fingerprints;
- two different runtimes receive HTTP inference concurrently;
- two transient reservations overlap in the shared `ResourceManager` ledger;
- the optional global governor bounds cross-runtime execution while backend batching remains backend-owned;
- the most pressured sampled macOS point is retained without inducing artificial critical pressure;
- the pressure policy is evaluated **dry-run only** and `automatic_eviction_exercised=false`;
- both runtimes unload and configured resident/transient accounting returns to zero;
- post-stop `available_memory` and parent-process RSS remain observational values, not a pass/fail reclamation threshold;
- for managed subprocess backends, aggregate RSS of owned backend processes is measured while they exist and only owner count/source are retained after teardown; raw PIDs are never serialized.

The campaign also runs a lease-safe shutdown-under-load phase. One runtime lease is deliberately held past a bounded shutdown timeout: that runtime must remain `FAILED`, owned and accounted while the idle peer may stop. After the lease is released, a second shutdown must clear the remaining runtime and configured accounting. This tests lifecycle ownership without killing an engine that is still leased.

After two reports, run the conservative compatibility/repetition reviewer:

```bash
python -m local_llm_server.multi_model_evidence_review \
  "$RRG5_DIR/multimodel-a.json" \
  "$RRG5_DIR/multimodel-b.json" \
  --min-reports 2 \
  --min-complete-cycles 4 \
  --output "$RRG5_DIR/multimodel-review.json"
```

A `sufficient_observation_set` means only that the same attributable models/runtime procedure repeatedly exercised identity, transient overlap, cleanup and bounded shutdown successfully. RSS and available-memory deltas remain in the review as raw observations. The reviewer deliberately emits `automatic_eviction_recommendation=not_provided` and `reclamation_safety_claim=false`.

Do not enable automatic pressure eviction solely because the reviewer is sufficient. A future policy decision must inspect the retained memory/pressure observations and define a separate acceptance contract; negative or mixed RRG-5 memory behavior is a valid outcome, not a reason to weaken the procedure.

## QI-8 — Qwen Image 2.1 MFlux Q8 representative observations

QI-8 is separate from the general L2 bundle above. It establishes attributable observations for
the quantized image-generation runtime; it does **not** convert one Mac run into a generic
performance or memory-fit claim.

Install the optional MFlux runtime, download the model, and use a clean converged `dev` checkout:

```bash
python -m pip install -r requirements/image-mlx.txt
local-llm download qwen-image-2.1-mflux-q8

QI8_DIR="$HOME/.local-llm-server/evidence/$(date +%F)-qwen-image-q8"
mkdir -p "$QI8_DIR"

local-llm evidence-image \
  --profile qwen-image-2.1-mflux-q8-smoke-v1 \
  --output-dir "$QI8_DIR"
```

The campaign automatically reuses a still-valid artifact receipt or explicitly verifies the
multi-file checkpoint before server startup. The built-in profile owns the prompt, dimensions,
steps, seed, repetitions, sampling interval, listener and safety guard; those values are not
hardcoded in the runner.

The preflight requires:

- macOS on Apple Silicon;
- clean `dev` source identity;
- loopback-only listener with the configured port free;
- an evidence directory outside the repository;
- measured available host memory;
- available memory at least equal to the configured resident-memory lower bound plus the profile
  safety margin.

That last condition is only an **exploratory refusal guard**. The resident lower bound can be
incomplete, and passing it does not prove that the runtime fits a given memory tier.

The evidence directory contains:

```text
image-evidence.json
image-evidence-server.log
images/
  generation-01.png
  generation-02.png
```

The JSON report retains:

- aggregate verified artifact digest/size/kind and file count;
- source revision and path-free runtime identity;
- server startup/load wall time;
- per-generation wall latency and Korgis-reported backend latency;
- requested dimensions/steps/seed via the versioned profile identity;
- generated artifact relative path, byte size and SHA-256;
- sampled peak Korgis process RSS and minimum available host memory during load/generation;
- explicit false claim flags for memory fit, performance, reclamation safety and production safety.

The JSON does **not** retain the prompt text, generated base64, private model path, PID or hostname.
The generated image files and server diagnostic log remain local evidence and should not be
committed wholesale.

A complete report means only that the configured procedure executed cleanly with attributable
identity and observations. It does not make the following statements true:

- "Qwen Image 2.1 Q8 fits every Mac with X GB";
- "this is the expected latency on other Apple chips";
- "memory returns safely to baseline after unload";
- "the runtime is production-safe".

Those require a separate acceptance/repetition contract over representative reports.

## 5. Validate the complete minimum L2 hardware bundle

After TH-E1, EV-3, HE-2 and RES-2 exist, run one deterministic review over the local directory:

```bash
python -m local_llm_server.l2_evidence_bridge validate-hardware-bundle \
  --directory "$EVIDENCE_DIR" \
  --output "$EVIDENCE_DIR/l2-device-bundle-summary.json"
```

The command exits successfully only when the minimum L2 evidence contracts are complete. It:

- verifies explicit TH-E1 OFF/ON-hidden evidence;
- uses the canonical evaluation comparison logic for EV-3;
- recomputes the HE-2 conservative review from both reclamation reports;
- verifies the RES-2 admit/account/release/reject invariants;
- emits no input paths, prompts or model outputs in the summary;
- never changes `.engineering/baseline.json` or authorizes automatic eviction.

RRG-5 reports are reviewed separately with `multi_model_evidence_review` and are not silently folded into this minimum L2 validator.

A non-zero exit is evidence of an incomplete/incompatible bundle, not a reason to edit thresholds until green.

## Evidence completion checklist

The minimum L2 evidence directory contains:

```text
thinking-campaign.json
evaluation-off-a.json
evaluation-off-b.json
reclamation-a.json
reclamation-b.json
reclamation-review.json
resource-policy-smoke.json
l2-device-bundle-summary.json
```

The separate RRG-5 directory contains:

```text
multimodel-a.json
multimodel-b.json
multimodel-review.json
```

Source evidence directories may contain private evaluation/model content and must not be committed wholesale. Durable repository truth should use only bounded public-safe conclusions, compatible identities/fingerprints and validated summaries needed for reproducibility.
