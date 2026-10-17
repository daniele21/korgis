"""macOS resource observation adapter.

Apple Silicon uses unified memory, so this adapter never invents a separate GPU
VRAM pool. Total/available host memory and process RSS are derived from public
OS counters; unsupported measurements remain explicitly unavailable.
"""
from __future__ import annotations

import math
import os
import re
import subprocess
import time
from collections.abc import Callable

from .resources import ResourceValue, ResourceValueSource, SystemResourceSnapshot

CommandRunner = Callable[[tuple[str, ...]], str]


class MacOSResourceObserver:
    def __init__(
        self,
        *,
        run_command: CommandRunner | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._run_command = run_command or _default_run_command
        self._clock = clock

    def snapshot(self) -> SystemResourceSnapshot:
        total = _read_total_memory(self._run_command)
        available = _read_available_memory(self._run_command)
        rss = read_process_rss(os.getpid(), run_command=self._run_command)
        swap_used = _read_swap_used(self._run_command)
        return SystemResourceSnapshot(
            captured_at_monotonic=self._clock(),
            platform="darwin",
            total_memory_bytes=total,
            available_memory_bytes=available,
            # Apple Silicon shares system/unified memory. Reporting a separate
            # accelerator pool would double-count capacity and mislead B2.
            accelerator_memory_bytes=ResourceValue.unavailable("bytes"),
            process_rss_bytes=rss,
            thermal_pressure=ResourceValue.unavailable("level"),
            swap_used_bytes=swap_used,
        )


def read_process_rss(
    pid: int,
    *,
    run_command: CommandRunner | None = None,
) -> ResourceValue:
    """Read RSS for one owned macOS PID without treating failure as zero."""
    if isinstance(pid, bool) or pid <= 0:
        return ResourceValue.unavailable("bytes")
    runner = run_command or _default_run_command
    try:
        raw = runner(("ps", "-o", "rss=", "-p", str(pid))).strip()
        value = int(raw) * 1024
    except (OSError, ValueError, subprocess.SubprocessError):
        return ResourceValue.unavailable("bytes")
    if value < 0:
        return ResourceValue.unavailable("bytes")
    return ResourceValue(value, ResourceValueSource.MEASURED, "bytes")


def _read_total_memory(run_command: CommandRunner) -> ResourceValue:
    try:
        raw = run_command(("sysctl", "-n", "hw.memsize")).strip()
        value = int(raw)
    except (OSError, ValueError, subprocess.SubprocessError):
        return ResourceValue.unavailable("bytes")
    if value <= 0:
        return ResourceValue.unavailable("bytes")
    return ResourceValue(value, ResourceValueSource.MEASURED, "bytes")


def _read_available_memory(run_command: CommandRunner) -> ResourceValue:
    """Estimate reclaimable host memory from vm_stat page counters.

    ``Pages free``, ``Pages inactive`` and ``Pages speculative`` are OS-measured
    counters. Their sum is an operational approximation for immediately free or
    reclaimable memory, not a claim about a separate GPU memory budget.
    """
    try:
        raw = run_command(("vm_stat",))
    except (OSError, subprocess.SubprocessError):
        return ResourceValue.unavailable("bytes")

    page_size = _parse_page_size(raw)
    if page_size is None:
        return ResourceValue.unavailable("bytes")

    counters = _parse_vm_stat_counters(raw)
    required = ("Pages free", "Pages inactive", "Pages speculative")
    if not all(key in counters for key in required):
        return ResourceValue.unavailable("bytes")
    pages = sum(counters[key] for key in required)
    return ResourceValue(pages * page_size, ResourceValueSource.MEASURED, "bytes")


def _read_swap_used(run_command: CommandRunner) -> ResourceValue:
    """Read macOS swap usage without interpreting cumulative swap as current pressure.

    `vm.swapusage` reports approximate values with binary K/M/G/T units.
    Missing or malformed counters stay unavailable: zero is never invented.
    """
    try:
        raw = run_command(("sysctl", "vm.swapusage"))
    except (OSError, KeyError, subprocess.SubprocessError):
        return ResourceValue.unavailable("bytes")

    match = re.search(r"\\bused\\s*=\\s*(\\d+(?:\\.\\d+)?)\\s*([KMGT])B?\\b", raw, flags=re.IGNORECASE)
    if match is None:
        return ResourceValue.unavailable("bytes")
    used = float(match.group(1))
    if not math.isfinite(used) or used < 0:
        return ResourceValue.unavailable("bytes")
    factor = {"K": 1, "M": 2, "G": 3, "T": 4}[match.group(2).upper()]
    return ResourceValue(int(used * 1024 ** factor), ResourceValueSource.MEASURED, "bytes")


def _parse_page_size(raw: str) -> int | None:
    match = re.search(r"page size of\s+(\d+)\s+bytes", raw, flags=re.IGNORECASE)
    if not match:
        return None
    value = int(match.group(1))
    return value if value > 0 else None


def _parse_vm_stat_counters(raw: str) -> dict[str, int]:
    counters: dict[str, int] = {}
    for line in raw.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        digits = value.strip().rstrip(".")
        try:
            counters[key.strip()] = int(digits)
        except ValueError:
            continue
    return counters


def _default_run_command(command: tuple[str, ...]) -> str:
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=True,
        timeout=5,
    )
    return result.stdout
