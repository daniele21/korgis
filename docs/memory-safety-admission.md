# Host-pressure admission and memory-safety alerts

Status: integration candidate on `dev`; NOT a production Mac memory-safety claim.
Owner: runtime ResourceManager and resource observation policy.
Product risk: avoid starting new expensive work while Apple Silicon is under memory pressure.

Korgis measures reclaimable host pages via `vm_stat`; the fraction of estimated
available host memory is classified normal (>20%), elevated (10%-20%) or critical
(<=10%) by the existing `classify_memory_pressure` contract. These are
**derived thresholds**, not Darwin memory-pressure notifications, swap rates or
an Apple-certified OOM predictor. They must not be labelled as native signals.

When the macOS host guard is enabled, new resident/transient reservations are
rejected on elevated or critical derived pressure, and the existing 2-6 GiB OS
reserve / 1.5x projected growth gate still applies on normal pressure. A failed
observation rejects rather than pretending there is spare memory. No resident
runtime is unloaded automatically, no inference is cancelled and no configured
explicit model/context parameters are changed.

The read-only `/api/v1/policies` administrative endpoint and the resource
policy snapshot include `host_memory` when the guard is active: pressure,
available/total bytes, swap bytes where supported, alert severity, source and
`automatic_pressure_eviction_enabled=false`. This is public-safe aggregate
system status; no PIDs, model paths, user content or hardware serial identifiers.
An unavailable measurement remains null/unknown, not zero/normal.

Follow-ups before enabling pressure-triggered eviction: native macOS pressure
notification integration, background sampling with bounded lifetime, workload
lease-safe cancellation/TTL, model-specific post-unload reclamation measurement,
and representative 32 GiB Apple Silicon real-hardware evidence (issue #239).
Hosted tests are deterministic evidence only. Observe Mac pressure, swap and
memory peaks both at idle and during requests; do not infer that admission
alone guarantees the machine cannot suffer OOM.
