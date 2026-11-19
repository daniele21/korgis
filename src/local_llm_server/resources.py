"""Resource observation contracts for resource-aware runtime admission.

The types in this module distinguish measured, estimated and unavailable values.
They intentionally do not perform admission or eviction; B2 owns that policy.
"""
from __future__ import annotations

import os
import platform
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Mapping, Protocol


class ResourceValueSource(str, Enum):
    MEASURED = "measured"
    ESTIMATED = "estimated"
    CONFIGURED = "configured"
    UNAVAILABLE = "unavailable"


class ResourcePressure(str, Enum):
    UNKNOWN = "unknown"
    NORMAL = "normal"
    ELEVATED = "elevated"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True)
class ResourceValue:
    value: int | float | None
    source: ResourceValueSource
    unit: str

    def __post_init__(self) -> None:
        if self.source is ResourceValueSource.UNAVAILABLE and self.value is not None:
            raise ValueError("unavailable resource values must use value=None")
        if self.source is not ResourceValueSource.UNAVAILABLE and self.value is None:
            raise ValueError("available resource values require a value")

    @property
    def available(self) -> bool:
        return self.value is not None

    @classmethod
    def unavailable(cls, unit: str) -> "ResourceValue":
        return cls(None, ResourceValueSource.UNAVAILABLE, unit)


@dataclass(frozen=True, slots=True)
class SystemResourceSnapshot:
    captured_at_monotonic: float
    platform: str
    total_memory_bytes: ResourceValue
    available_memory_bytes: ResourceValue
    process_rss_bytes: ResourceValue = field(default_factory=lambda: ResourceValue.unavailable("bytes"))
    accelerator_memory_bytes: ResourceValue = field(default_factory=lambda: ResourceValue.unavailable("bytes"))
    thermal_pressure: ResourceValue = field(default_factory=lambda: ResourceValue.unavailable("level"))
    swap_used_bytes: ResourceValue = field(default_factory=lambda: ResourceValue.unavailable("bytes"))


@dataclass(frozen=True, slots=True)
class RuntimeResourceProfile:
    runtime_id: str
    estimated_resident_bytes: ResourceValue = field(default_factory=lambda: ResourceValue.unavailable("bytes"))
    observed_resident_bytes: ResourceValue = field(default_factory=lambda: ResourceValue.unavailable("bytes"))
    observed_peak_bytes: ResourceValue = field(default_factory=lambda: ResourceValue.unavailable("bytes"))


@dataclass(frozen=True, slots=True)
class ResourceBudget:
    limit_bytes: int | None
    headroom_bytes: int = 0

    def __post_init__(self) -> None:
        if self.limit_bytes is not None and self.limit_bytes < 0:
            raise ValueError("limit_bytes must be >= 0 or None")
        if self.headroom_bytes < 0:
            raise ValueError("headroom_bytes must be >= 0")

    @property
    def usable_bytes(self) -> int | None:
        if self.limit_bytes is None:
            return None
        return max(0, self.limit_bytes - self.headroom_bytes)


class ResourceObserver(Protocol):
    def snapshot(self) -> SystemResourceSnapshot: ...


class StandardLibraryResourceObserver:
    """Best-effort host observer with fail-closed unavailable semantics.

    Linux memory is read from /proc. Other platforms retain unavailable fields
    until a focused adapter proves a trustworthy measurement source.
    """

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock

    def snapshot(self) -> SystemResourceSnapshot:
        total = ResourceValue.unavailable("bytes")
        available = ResourceValue.unavailable("bytes")
        rss = _read_process_rss()

        if platform.system().lower() == "linux":
            meminfo = _read_linux_meminfo(Path("/proc/meminfo"))
            if "MemTotal" in meminfo:
                total = ResourceValue(meminfo["MemTotal"] * 1024, ResourceValueSource.MEASURED, "bytes")
            if "MemAvailable" in meminfo:
                available = ResourceValue(meminfo["MemAvailable"] * 1024, ResourceValueSource.MEASURED, "bytes")

        return SystemResourceSnapshot(
            captured_at_monotonic=self._clock(),
            platform=platform.system().lower() or "unknown",
            total_memory_bytes=total,
            available_memory_bytes=available,
            process_rss_bytes=rss,
        )


def classify_memory_pressure(
    snapshot: SystemResourceSnapshot,
    *,
    elevated_fraction: float = 0.20,
    critical_fraction: float = 0.10,
) -> ResourcePressure:
    total = snapshot.total_memory_bytes.value
    available = snapshot.available_memory_bytes.value
    if not isinstance(total, (int, float)) or not isinstance(available, (int, float)) or total <= 0:
        return ResourcePressure.UNKNOWN
    fraction = available / total
    if fraction <= critical_fraction:
        return ResourcePressure.CRITICAL
    if fraction <= elevated_fraction:
        return ResourcePressure.ELEVATED
    return ResourcePressure.NORMAL


def _read_linux_meminfo(path: Path) -> Mapping[str, int]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    values: dict[str, int] = {}
    for line in lines:
        if ":" not in line:
            continue
        key, raw = line.split(":", 1)
        first = raw.strip().split(maxsplit=1)[0] if raw.strip() else ""
        try:
            values[key] = int(first)
        except ValueError:
            continue
    return values


def _read_process_rss() -> ResourceValue:
    # Linux statm reports resident pages; use only when the sysconf page size is
    # available. Other platforms deliberately return unavailable here.
    if platform.system().lower() != "linux":
        return ResourceValue.unavailable("bytes")
    try:
        resident_pages = int(Path("/proc/self/statm").read_text(encoding="utf-8").split()[1])
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
        return ResourceValue(resident_pages * page_size, ResourceValueSource.MEASURED, "bytes")
    except (OSError, ValueError, IndexError):
        return ResourceValue.unavailable("bytes")
