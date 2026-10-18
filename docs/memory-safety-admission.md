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
and representative 32 GiB Apple Silicon real-hardware evidence (issues #239, #244).
Hosted tests are deterministic evidence only. Observe Mac pressure, swap and
memory peaks both at idle and during requests; do not infer that admission
alone guarantees the machine cannot suffer OOM.

## Continuous host sampling (experimental, `dev`)

During the server ASGI lifespan, Korgis starts one bounded observation-only
monitor when host memory guarding is configured. It samples the existing
ResourceManager/macOS observer at 5-second intervals, keeps at most 16
public-safe alert transitions, and uses the existing hysteretic residency policy
to count idle eviction *candidates*, without unloading any model. The monitor
stops before server teardown, and command calls have a 2-second timeout each.
`GET /api/v1/policies` exposes `host_pressure_monitor` (status, bounded
history, policy state and candidate count); it continues to expose
`host_memory` and falls back to a fresh read until the first sample.

Samples still use measured `vm_stat` counters and a **derived** pressure
classification, not Apple's native Dispatch memory pressure notifications.
No independent native event source is claimed. No backend model/KV cache
trimming, active-job cancellation, pinned eviction or background unload is enabled;
there is no validated production-grade prevention of OOM. Issue #244 tracks
native events, backend-specific cooperative relief and Apple Silicon evidence.

On a sustained **critical** episode, the monitor may clear the server's own
thread-safe, completed-response cache exactly once after hysteresis triggers.
This is a safe, limited application-cache trim, **not** an MLX/llama.cpp
weights/KV trim and never proof that RSS or unified-memory pressure decreased.
It preserves active streams and model ownership and exposes
`response_cache_trim_count` and a bounded action event. A failed cache clear
does not trigger unload or infinite retry.

## Native Darwin pressure events (opt-in engineering preview)

Korgis includes a source-only Swift bridge at
`scripts/macos/memory_pressure.swift`. On a Mac with Swift toolchain installed,
compile it explicitly with `swiftc -O scripts/macos/memory_pressure.swift -o
/path/to/korgis-memory-pressure` and set
`LOCAL_LLM_MACOS_PRESSURE_HELPER=/path/to/korgis-memory-pressure` before launching
Korgis. The path must be absolute and executable. Korgis does **not** compile,
download or discover executable code at runtime. This preview is not part of
the currently published wheel's native executable artifacts.

The child uses Apple's `DispatchSource.makeMemoryPressureSource` and emits
only `ready`, `normal`, `warning` or `critical` lines. It is tied to the
app server lifespan: parent stdin EOF terminates it, and shutdown reaps only
the owned child. No helper configured -> derived vm_stat observation remains
the only source; native state shows `not_configured` (or `unsupported`).
If the helper dies, the last native event is cleared, never treated as a
new `normal` event.

The canonical ResourceManager combines the last live native event with its
derived pressure signal conservatively; the more severe observed level wins.
Native `normal` cannot override low memory headroom or bypass unknown memory
measurements. An event updates admission immediately; derived pressure is still
refreshed on load/commit and by the existing 5-second sample loop. The admin
policy payload marks `native_dispatch_event` when a native event is decisive,
and otherwise reports its derived source; optional `native_last_event` and
`derived_pressure` fields expose provenance without leaking process paths.
These events are transitions, not a guaranteed initial measurement. There is
no live-event claim unless the macOS helper is compiled, launched and
validated on physical Apple Silicon. Automatic model eviction remains disabled.

Engineering acceptance still requires packaged/signed helper, source/build
provenance, representative Mac crash/SIGKILL and memory-pressure response
tests (#239, #244) before production enablement.

### Deterministic Mac compilation gate

`.github/workflows/macos-memory-pressure-smoke.yml` compiles the Swift source
on GitHub-hosted macOS and checks that the executable emits `ready` and exits
on parent stdin EOF. This gate does NOT create memory pressure or validate
`normal/warning/critical` delivery on a 32-GiB Apple Silicon device.
The helper remains explicit opt-in until package/signature and hardware
ownership evidence is accepted.
