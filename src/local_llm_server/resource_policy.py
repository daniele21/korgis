"""Product-facing resource policy configuration and public accounting state."""
from __future__ import annotations

import os
import platform
from dataclasses import dataclass
from typing import Mapping

from .resource_manager import ResourceManager, ReservationState
from .resources import ResourceBudget

_GIB = 1024 ** 3


@dataclass(frozen=True, slots=True)
class ResourcePolicySettings:
    memory_limit_bytes: int | None = None
    headroom_bytes: int = 0
    host_memory_guard_enabled: bool | None = None

    def __post_init__(self) -> None:
        if self.memory_limit_bytes is not None and self.memory_limit_bytes < 0:
            raise ValueError("memory_limit_bytes must be >= 0 or None")
        if self.headroom_bytes < 0:
            raise ValueError("headroom_bytes must be >= 0")
        if self.memory_limit_bytes is not None and self.headroom_bytes > self.memory_limit_bytes:
            raise ValueError("headroom_bytes cannot exceed memory_limit_bytes")

    @property
    def enabled(self) -> bool:
        return self.memory_limit_bytes is not None

    @property
    def budget(self) -> ResourceBudget:
        return ResourceBudget(
            limit_bytes=self.memory_limit_bytes,
            headroom_bytes=self.headroom_bytes,
        )

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> "ResourcePolicySettings":
        env = os.environ if environ is None else environ
        limit_bytes = _optional_bytes(
            env.get("LOCAL_LLM_MEMORY_LIMIT_BYTES"),
            env.get("LOCAL_LLM_MEMORY_LIMIT_GIB"),
            field="memory limit",
        )
        headroom_bytes = _optional_bytes(
            env.get("LOCAL_LLM_MEMORY_HEADROOM_BYTES"),
            env.get("LOCAL_LLM_MEMORY_HEADROOM_GIB"),
            field="memory headroom",
        )
        host_guard_raw = env.get("LOCAL_LLM_HOST_MEMORY_GUARD")
        host_guard = None
        if host_guard_raw is not None:
            lowered = host_guard_raw.strip().lower()
            if lowered not in {"1", "true", "yes", "on", "0", "false", "no", "off"}:
                raise ValueError("LOCAL_LLM_HOST_MEMORY_GUARD must be a boolean")
            host_guard = lowered in {"1", "true", "yes", "on"}
        return cls(
            memory_limit_bytes=limit_bytes,
            headroom_bytes=headroom_bytes or 0,
            host_memory_guard_enabled=host_guard,
        )


def build_resource_manager(
    settings: ResourcePolicySettings,
) -> ResourceManager | None:
    """Enforce configured memory and default live host admission on macOS."""
    use_host_guard = settings.host_memory_guard_enabled
    if use_host_guard is None:
        use_host_guard = platform.system() == "Darwin"
    if not settings.enabled and not use_host_guard:
        return None
    observer = None
    if use_host_guard:
        from .resources_macos import MacOSResourceObserver
        observer = MacOSResourceObserver()
    return ResourceManager(settings.budget, host_observer=observer)


def resource_policy_snapshot(
    settings: ResourcePolicySettings,
    manager: ResourceManager | None,
) -> dict[str, object]:
    """Return public configured/accounting state without process-private data."""
    reservations = manager.snapshot() if manager is not None else ()
    committed = sum(
        item.accounted_bytes
        for item in reservations
        if item.state is ReservationState.COMMITTED
    )
    reserved = sum(
        item.accounted_bytes
        for item in reservations
        if item.state is ReservationState.RESERVED
    )
    usable = settings.budget.usable_bytes
    remaining = None if usable is None else max(0, usable - committed - reserved)
    host_guard_enabled = manager is not None and manager.host_protection_enabled
    result = {
        "enabled": settings.enabled or host_guard_enabled,
        "memory_limit_bytes": settings.memory_limit_bytes,
        "headroom_bytes": settings.headroom_bytes,
        "usable_budget_bytes": usable,
        "committed_bytes": committed,
        "reserved_bytes": reserved,
        "remaining_bytes": remaining,
        "reservation_count": len(reservations),
        "policy_state": (
            "configured_and_host_guard" if settings.enabled and host_guard_enabled
            else "host_guard" if host_guard_enabled
            else "configured" if settings.enabled else "disabled"
        ),
    }
    if host_guard_enabled:
        result["host_memory_guard_enabled"] = True
        result["host_memory_admission"] = "observed_available_with_configured_safety_factor"
    return result


def _optional_bytes(
    raw_bytes: str | None,
    raw_gib: str | None,
    *,
    field: str,
) -> int | None:
    if raw_bytes and raw_gib:
        raise ValueError(f"configure {field} in bytes or GiB, not both")
    if raw_bytes:
        try:
            value = int(raw_bytes)
        except ValueError as exc:
            raise ValueError(f"invalid {field} byte value") from exc
        if value < 0:
            raise ValueError(f"{field} must be >= 0")
        return value
    if raw_gib:
        try:
            value = float(raw_gib)
        except ValueError as exc:
            raise ValueError(f"invalid {field} GiB value") from exc
        if value < 0:
            raise ValueError(f"{field} must be >= 0")
        return int(value * _GIB)
    return None
