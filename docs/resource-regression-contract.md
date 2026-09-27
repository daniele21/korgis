# Deterministic resource regression contract

Status: active
Owner: runtime-and-platform
Canonical scope: current.resource-regression
Last reviewed: 2026-09-28

L2 hosted CI proves only configured/Python-owned resource-ledger behavior that is stable without a model or accelerator. `.engineering/resource-regression.json` maps each claim to an exact test and preserves explicit non-claims.

The gate repeatedly exercises successful reserve/commit/release cycles, repeated rejected admission, and bounded retained Python heap after a warm-up using `tracemalloc`. Each completed lifecycle must leave the `ResourceManager` ledger empty.

Resident runtimes and transient active requests are distinct reservation kinds in the same global ledger and usable budget. Chat/VLM and image-generation HTTP requests enter transient admission only after scheduler/global-governor execution admission; queued work therefore reserves no transient memory. A transient reservation that would exceed already-accounted resident/transient bytes must be rejected before inference execution. Streaming requests retain transient accounting until their body iterator completes or is cancelled; releasing an HTTP response object before the stream is consumed is not sufficient evidence of request completion.

Memory envelopes are configured estimates, not measurements. Resident accounting can combine attributable model/projector artifact size with explicitly configured backend/context/cache/safety budgets; transient accounting can use an explicit total or configured request components. Image generation does not derive transient RAM from width/height alone; without configured or measured evidence its request-memory envelope remains unknown. Missing evidence remains unavailable and envelope completeness is exposed separately from known lower-bound bytes. No formula based only on `ctx_size` is treated as measured or trustworthy KV memory.

This evidence does **not** prove native backend memory reclamation, Apple unified-memory or accelerator reclamation, RSS return-to-baseline, or automatic pressure-eviction safety. Those remain representative-device claims owned by the hardware evidence workstream.

Run `python3 scripts/verify_resource_regression.py` for contract integrity; normal pytest executes the behavioral and heap regression tests. Shared Repository Health integration remains L2-10.
