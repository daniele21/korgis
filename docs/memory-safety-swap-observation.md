# macOS swap observation

Status: integration candidate, deterministic-only evidence.

The macOS resource observer reads point-in-time swap usage via `sysctl
vm.swapusage`, reporting system-observed bytes (or unavailable on failure).
This is not a rate of swapping, a native memory-pressure notification,
predictive OOM evidence, or a measured amount reclaimed by any model unload.

The Apple Silicon system has unified memory. Swap metrics do not create a
separate accelerator VRAM pool. Korgis retains the existing `vm_stat`
approximation for host-available/reclaimable memory and the per-process RSS
metric; read errors never become zero. No resident model is automatically
unloaded; memory-safety claims remain dependent on 32 GiB representative
hardware validation (issue #239).
