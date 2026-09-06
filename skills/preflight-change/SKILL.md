---
name: preflight-change
description: Establish Korgis integration/release readiness from exact identity, affected docs, risk-selected gates and equivalent evidence reuse.
---
# Preflight Change
Refresh exact head/tree/live base, review the complete diff, make affected durable docs current, resolve risks -> gates -> profile/executor and select affected E2E. Reuse only equivalent trusted proof; route missing deterministic work via `remote-preflight`.

INTEGRATION requires all required automated gates/E2E and defers required Apple Silicon confirmation to release. RELEASE requires FULL plus every applicable required real-environment confirmation. Use `validate-change` diagnosis on failure. Return bounded reporting fields including failed/pending gates and remaining release obligations.
