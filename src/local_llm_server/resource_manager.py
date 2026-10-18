"""Resource reservation and admission foundation.

This module manages configured budget accounting only. It does not claim that a
runtime can reclaim memory; representative-device evidence owns reclamation.
Resident runtimes and active requests share one ledger so individually safe
owners cannot collectively exceed the configured usable budget.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from enum import Enum
from math import ceil
from typing import TYPE_CHECKING

from .resources import ResourceBudget, ResourcePressure, ResourceValueSource, classify_memory_pressure

if TYPE_CHECKING:
    from .resources import ResourceObserver


class AdmissionDecision(str, Enum):
    ADMIT = "admit"
    REJECT = "reject"
    UNKNOWN = "unknown"


class ReservationState(str, Enum):
    RESERVED = "reserved"
    COMMITTED = "committed"


class ReservationKind(str, Enum):
    RESIDENT = "resident"
    TRANSIENT = "transient"


@dataclass(frozen=True, slots=True)
class ResourceReservation:
    reservation_id: str
    requested_bytes: int
    accounted_bytes: int
    state: ReservationState
    kind: ReservationKind = ReservationKind.RESIDENT


@dataclass(frozen=True, slots=True)
class AdmissionResult:
    decision: AdmissionDecision
    requested_bytes: int
    committed_bytes: int
    reserved_bytes: int
    usable_budget_bytes: int | None
    reason: str


class ResourceManager:
    """Thread-safe configured-budget ledger shared by all memory owners."""

    def __init__(
        self,
        budget: ResourceBudget,
        *,
        host_observer: ResourceObserver | None = None,
    ) -> None:
        self._budget = budget
        self._host_observer = host_observer
        self._reservations: dict[str, ResourceReservation] = {}
        self._lock = threading.RLock()
        # Last event from a live, supervised macOS Dispatch source (if opted in).
        # A value of None is unknown, not normal.
        self._native_pressure: ResourcePressure | None = None

    @property
    def host_protection_enabled(self) -> bool:
        return self._host_observer is not None

    def update_native_pressure(self, pressure: ResourcePressure | None) -> None:
        """Accept a verified event only while the native source is alive.

        Clearing on helper exit is required: a stale normal event must not
        suppress a later measured host-pressure warning.
        """
        if pressure is not None and pressure is ResourcePressure.UNKNOWN:
            raise ValueError("unknown is not a native pressure transition")
        if pressure is not None and not isinstance(pressure, ResourcePressure):
            raise TypeError("native pressure must be ResourcePressure or None")
        with self._lock:
            self._native_pressure = pressure

    def _effective_pressure(self, derived: ResourcePressure) -> ResourcePressure:
        with self._lock:
            native = self._native_pressure
        if native is None:
            return derived
        # A native NORMAL *transition* is not evidence that a missing
        # vm_stat measurement is safe. Preserve unknown unless the event
        # carries a warning/critical signal that should block new work.
        if derived is ResourcePressure.UNKNOWN and native is ResourcePressure.NORMAL:
            return ResourcePressure.UNKNOWN
        rank = {ResourcePressure.UNKNOWN: -1, ResourcePressure.NORMAL: 0,
                ResourcePressure.ELEVATED: 1, ResourcePressure.CRITICAL: 2}
        return native if rank[native] > rank[derived] else derived

    def host_memory_status(self) -> dict[str, object] | None:
        """Public-safe observed host status; read-only, never an eviction command.

        The pressure classification is a heuristic derived from reclaimable
        host pages, NOT a native macOS memory-pressure notification.
        """
        observer = self._host_observer
        if observer is None:
            return None
        try:
            snapshot = observer.snapshot()
        except Exception:
            snapshot = None

        if snapshot is None:
            with self._lock:
                native = self._native_pressure
            pressure = self._effective_pressure(ResourcePressure.UNKNOWN)
            status = {
                "pressure": pressure.value,
                "source": (
                    "native_dispatch_event" if native is not None
                    and pressure is native else "unavailable"
                ),
                "available_bytes": None,
                "total_bytes": None,
                "swap_used_bytes": None,
                "alert": _host_pressure_alert(pressure),
                "automatic_pressure_eviction_enabled": False,
            }
            if native is not None:
                status["native_last_event"] = native.value
            return status

        def measured(value: object) -> int | None:
            if getattr(value, "source", None) is not ResourceValueSource.MEASURED:
                return None
            number = getattr(value, "value", None)
            return number if isinstance(number, int) and not isinstance(number, bool) and number >= 0 else None

        pressure = (
            classify_memory_pressure(snapshot)
            if measured(snapshot.available_memory_bytes) is not None
            and measured(snapshot.total_memory_bytes) is not None
            else ResourcePressure.UNKNOWN
        )
        with self._lock:
            native = self._native_pressure
        effective = self._effective_pressure(pressure)
        status = {
            "pressure": effective.value,
            "source": (
                "native_dispatch_event" if native is not None
                and effective is native
                and (pressure is ResourcePressure.UNKNOWN or effective is not pressure)
                else "derived_available_fraction" if pressure is not ResourcePressure.UNKNOWN
                else "unavailable"
            ),
            "available_bytes": measured(snapshot.available_memory_bytes),
            "total_bytes": measured(snapshot.total_memory_bytes),
            "swap_used_bytes": measured(getattr(snapshot, "swap_used_bytes", None)),
            "alert": _host_pressure_alert(effective),
            "automatic_pressure_eviction_enabled": False,
        }
        if native is not None:
            status["native_last_event"] = native.value
            status["derived_pressure"] = pressure.value
        return status

    def reject_unestimated_load(self) -> AdmissionResult:
        """A missing size cannot silently bypass host admission on macOS."""
        with self._lock:
            return self._result(
                AdmissionDecision.REJECT,
                requested_bytes=0,
                reason="resident memory estimate unavailable under host memory guard",
            )

    @property
    def budget(self) -> ResourceBudget:
        return self._budget

    def snapshot(
        self,
        *,
        kind: ReservationKind | None = None,
    ) -> tuple[ResourceReservation, ...]:
        with self._lock:
            reservations = self._reservations.values()
            if kind is not None:
                reservations = (item for item in reservations if item.kind is kind)
            return tuple(sorted(reservations, key=lambda item: item.reservation_id))

    def reserve(
        self,
        reservation_id: str,
        requested_bytes: int,
        *,
        kind: ReservationKind = ReservationKind.RESIDENT,
    ) -> AdmissionResult:
        if not reservation_id.strip():
            raise ValueError("reservation_id must be non-empty")
        if requested_bytes < 0:
            raise ValueError("requested_bytes must be >= 0")

        with self._lock:
            if reservation_id in self._reservations:
                raise ValueError(f"reservation already exists: {reservation_id}")
            decision = self._decision_for(requested_bytes)
            if decision.decision is AdmissionDecision.ADMIT:
                self._reservations[reservation_id] = ResourceReservation(
                    reservation_id=reservation_id,
                    requested_bytes=requested_bytes,
                    accounted_bytes=requested_bytes,
                    state=ReservationState.RESERVED,
                    kind=kind,
                )
            return decision

    def commit(self, reservation_id: str, *, observed_bytes: int | None = None) -> AdmissionResult:
        if observed_bytes is not None and observed_bytes < 0:
            raise ValueError("observed_bytes must be >= 0")
        with self._lock:
            current = self._require(reservation_id)
            accounted = current.accounted_bytes if observed_bytes is None else observed_bytes
            other_bytes = sum(
                item.accounted_bytes
                for key, item in self._reservations.items()
                if key != reservation_id
            )
            usable = self._budget.usable_bytes
            if usable is not None and other_bytes + accounted > usable:
                return self._result(
                    AdmissionDecision.REJECT,
                    requested_bytes=accounted,
                    reason="observed footprint exceeds configured usable budget",
                )
            # The new backend now occupies real host memory. Check that the
            # system still has headroom before publishing the resident runtime.
            host = self._check_host_available(0, exclude_reservation_id=reservation_id)
            if host is not None and host.decision is AdmissionDecision.REJECT:
                return host
            self._reservations[reservation_id] = ResourceReservation(
                reservation_id=reservation_id,
                requested_bytes=current.requested_bytes,
                accounted_bytes=accounted,
                state=ReservationState.COMMITTED,
                kind=current.kind,
            )
            return self._result(
                AdmissionDecision.ADMIT
                if usable is not None or self.host_protection_enabled
                else AdmissionDecision.UNKNOWN,
                requested_bytes=accounted,
                reason=(
                    "committed within configured budget"
                    if usable is not None
                    else "no configured resource limit; accounting recorded without enforceable admission"
                ),
            )

    def release(self, reservation_id: str) -> bool:
        with self._lock:
            return self._reservations.pop(reservation_id, None) is not None

    def rollback(self, reservation_id: str) -> bool:
        return self.release(reservation_id)

    def _decision_for(self, requested_bytes: int) -> AdmissionResult:
        usable = self._budget.usable_bytes
        accounted = sum(item.accounted_bytes for item in self._reservations.values())
        if usable is not None and accounted + requested_bytes > usable:
            return self._result(
                AdmissionDecision.REJECT,
                requested_bytes=requested_bytes,
                reason="configured usable budget would be exceeded",
            )
        host = self._check_host_available(requested_bytes)
        if host is not None and host.decision is AdmissionDecision.REJECT:
            return host
        if usable is None and host is None:
            return self._result(
                AdmissionDecision.UNKNOWN,
                requested_bytes=requested_bytes,
                reason="no configured resource limit",
            )
        return self._result(
            AdmissionDecision.ADMIT,
            requested_bytes=requested_bytes,
            reason=(
                "host headroom and configured budget admit reservation"
                if usable is not None and host is not None
                else "host headroom admits reservation"
                if host is not None
                else "reservation fits configured usable budget"
            ),
        )

    def _check_host_available(
        self, requested_bytes: int, *, exclude_reservation_id: str | None = None
    ) -> AdmissionResult | None:
        """Live admission, separate from the configured per-Korgis ledger.

        Already COMMITTED processes are reflected in the host's measured
        available memory. Count only still-pending reservations to avoid
        double charging resident models. The 1.5x load allowance is a policy
        safety estimate, not a proven native/GPU peak memory bound.
        """
        observer = self._host_observer
        if observer is None:
            return None
        snapshot = None
        try:
            snapshot = observer.snapshot()
            total = snapshot.total_memory_bytes.value
            available = snapshot.available_memory_bytes.value
        except Exception:
            total = available = None
        if (
            snapshot is None
            or not isinstance(total, int) or not isinstance(available, int)
            or snapshot.total_memory_bytes.source is not ResourceValueSource.MEASURED
            or snapshot.available_memory_bytes.source is not ResourceValueSource.MEASURED
            or total <= 0 or available < 0
        ):
            return self._result(
                AdmissionDecision.REJECT,
                requested_bytes=requested_bytes,
                reason="host memory observation unavailable",
            )
        # The conservative OS reserve alone is not enough when the measured
        # host is already under pressure (especially with a small new load).
        # No resident runtime is unloaded here; admission only rejects.
        pressure = self._effective_pressure(classify_memory_pressure(snapshot))
        if pressure in {ResourcePressure.ELEVATED, ResourcePressure.CRITICAL}:
            return self._result(
                AdmissionDecision.REJECT,
                requested_bytes=requested_bytes,
                reason=f"host available memory unsafe: host memory pressure is {pressure.value}; rejecting new allocation",
            )
        reserve = max(2 * 1024**3, min(6 * 1024**3, ceil(total * 0.1875)))
        pending = sum(
            entry.accounted_bytes
            for key, entry in self._reservations.items()
            if key != exclude_reservation_id and entry.state is ReservationState.RESERVED
        )
        projected = ceil((requested_bytes + pending) * 1.5)
        if available - projected < reserve:
            return self._result(
                AdmissionDecision.REJECT,
                requested_bytes=requested_bytes,
                reason="host available memory cannot cover estimated growth and OS reserve",
            )
        return self._result(
            AdmissionDecision.ADMIT,
            requested_bytes=requested_bytes,
            reason="host available memory above conservative admission floor",
        )

    def _result(
        self,
        decision: AdmissionDecision,
        *,
        requested_bytes: int,
        reason: str,
    ) -> AdmissionResult:
        committed = sum(
            item.accounted_bytes
            for item in self._reservations.values()
            if item.state is ReservationState.COMMITTED
        )
        reserved = sum(
            item.accounted_bytes
            for item in self._reservations.values()
            if item.state is ReservationState.RESERVED
        )
        return AdmissionResult(
            decision=decision,
            requested_bytes=requested_bytes,
            committed_bytes=committed,
            reserved_bytes=reserved,
            usable_budget_bytes=self._budget.usable_bytes,
            reason=reason,
        )

    def _require(self, reservation_id: str) -> ResourceReservation:
        try:
            return self._reservations[reservation_id]
        except KeyError as exc:
            raise KeyError(f"unknown reservation: {reservation_id}") from exc

def _host_pressure_alert(pressure: ResourcePressure) -> str:
    if pressure is ResourcePressure.CRITICAL:
        return "critical"
    if pressure is ResourcePressure.ELEVATED:
        return "warning"
    if pressure is ResourcePressure.NORMAL:
        return "none"
    return "unknown"
