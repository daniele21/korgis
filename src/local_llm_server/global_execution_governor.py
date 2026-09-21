"""Bounded fair cross-runtime execution admission.

The governor owns aggregate control-plane execution slots and fair selection
across runtimes. It mirrors each runtime's configured concurrency only as an
eligibility bound so global slots are never wasted on work that would immediately
block on the runtime semaphore. The runtime semaphore remains the final canonical
per-runtime safeguard; backend-native batching remains backend-owned.
"""
from __future__ import annotations

import threading
import time
from collections import Counter, deque
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

from .core.contracts import ErrorCode, InferenceError
from .scheduler_policy import (
    WorkloadClass,
    effective_workload_priority,
    normalize_workload_class,
)


_OWNER_ATTRIBUTE = "_local_llm_global_execution_governor"


class GlobalExecutionState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


@dataclass(slots=True)
class _Waiter:
    request_id: str
    runtime_key: str
    runtime_max_running: int
    submitted_at: float
    deadline_at: float | None
    workload_class: WorkloadClass = WorkloadClass.STANDARD
    state: GlobalExecutionState = GlobalExecutionState.QUEUED
    started_at: float | None = None
    finished_at: float | None = None

    def expired(self, now: float) -> bool:
        return self.deadline_at is not None and now >= self.deadline_at


@dataclass(frozen=True, slots=True)
class GlobalExecutionPermit:
    request_id: str
    runtime_key: str
    submitted_at: float
    started_at: float
    deadline_at: float | None
    workload_class: WorkloadClass

    @property
    def wait_ms(self) -> float:
        return max(0.0, (self.started_at - self.submitted_at) * 1000.0)


@dataclass(frozen=True, slots=True)
class GlobalExecutionSnapshot:
    max_running: int
    queue_capacity: int
    inflight: int
    queued: int
    runtimes: tuple[dict[str, object], ...]
    workloads: tuple[dict[str, object], ...]

    def to_public_dict(self) -> dict[str, object]:
        return {
            "enabled": True,
            "max_running": self.max_running,
            "queue_capacity": self.queue_capacity,
            "inflight": self.inflight,
            "queued": self.queued,
            "fairness": "priority_aging_runtime_round_robin",
            "runtimes": [dict(item) for item in self.runtimes],
            "workloads": [dict(item) for item in self.workloads],
        }


class GlobalExecutionGovernor:
    """Bound aggregate running work with round-robin fairness by runtime."""

    def __init__(
        self,
        *,
        max_running: int,
        queue_capacity: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_running < 1:
            raise ValueError("max_running must be >= 1")
        if queue_capacity < 1:
            raise ValueError("queue_capacity must be >= 1")
        self.max_running = max_running
        self.queue_capacity = queue_capacity
        self.clock = clock
        self._condition = threading.Condition(threading.RLock())
        self._waiters: dict[str, _Waiter] = {}
        self._runtime_queues: dict[str, deque[str]] = {}
        self._runtime_order: deque[str] = deque()
        self._runtime_in_order: set[str] = set()
        self._runtime_inflight: Counter[str] = Counter()
        self._inflight = 0

    def submit(
        self,
        runtime_key: str,
        request_id: str,
        *,
        runtime_max_running: int,
        timeout_seconds: float | None = None,
        workload_class: str | WorkloadClass = WorkloadClass.STANDARD,
    ) -> None:
        """Register a waiter synchronously so cancellation can always find it."""
        if not runtime_key.strip():
            raise ValueError("runtime_key must be non-empty")
        if not request_id.strip():
            raise ValueError("request_id must be non-empty")
        if runtime_max_running < 1:
            raise ValueError("runtime_max_running must be >= 1")
        if timeout_seconds is not None and timeout_seconds <= 0:
            raise InferenceError(
                ErrorCode.TIMEOUT,
                "request deadline expired while waiting for global execution admission",
                retryable=True,
                details={},
            )

        resolved_workload_class = normalize_workload_class(workload_class)
        now = self.clock()
        with self._condition:
            if request_id in self._waiters:
                raise ValueError(f"request_id already exists: {request_id}")
            self._expire_queued_locked(now)
            self._admit_available_locked(now)
            queued = self._queued_count_locked()
            can_run_now = (
                self._inflight < self.max_running
                and self._runtime_inflight[runtime_key] < runtime_max_running
            )
            if queued >= self.queue_capacity and not can_run_now:
                raise InferenceError(
                    ErrorCode.RESOURCE_EXHAUSTED,
                    "global execution queue is full",
                    retryable=True,
                    details={"capacity": self.queue_capacity},
                )

            self._waiters[request_id] = _Waiter(
                request_id=request_id,
                runtime_key=runtime_key,
                runtime_max_running=runtime_max_running,
                submitted_at=now,
                deadline_at=(now + timeout_seconds) if timeout_seconds is not None else None,
                workload_class=resolved_workload_class,
            )
            self._runtime_queues.setdefault(runtime_key, deque()).append(request_id)
            self._schedule_runtime_locked(runtime_key)
            self._admit_available_locked(now)
            self._condition.notify_all()

    def wait(self, request_id: str) -> GlobalExecutionPermit:
        """Block until a previously submitted waiter is running or terminal."""
        with self._condition:
            waiter = self._waiters.get(request_id)
            if waiter is None:
                raise KeyError(request_id)
            while True:
                if waiter.state is GlobalExecutionState.RUNNING:
                    assert waiter.started_at is not None
                    return GlobalExecutionPermit(
                        request_id=waiter.request_id,
                        runtime_key=waiter.runtime_key,
                        submitted_at=waiter.submitted_at,
                        started_at=waiter.started_at,
                        deadline_at=waiter.deadline_at,
                        workload_class=waiter.workload_class,
                    )
                if waiter.state is GlobalExecutionState.EXPIRED:
                    self._discard_waiter_locked(request_id, waiter)
                    raise InferenceError(
                        ErrorCode.TIMEOUT,
                        "request deadline expired while waiting for global execution admission",
                        retryable=True,
                        details={},
                    )
                if waiter.state is GlobalExecutionState.CANCELLED:
                    self._discard_waiter_locked(request_id, waiter)
                    raise InferenceError(
                        ErrorCode.CANCELLED,
                        "request was cancelled before global execution admission",
                        retryable=False,
                        details={},
                    )

                now = self.clock()
                self._expire_queued_locked(now)
                self._admit_available_locked(now)
                if waiter.state is not GlobalExecutionState.QUEUED:
                    continue
                remaining = (
                    None
                    if waiter.deadline_at is None
                    else max(0.0, waiter.deadline_at - now)
                )
                if remaining is not None and remaining <= 0:
                    continue
                self._condition.wait(timeout=remaining)

    def acquire(
        self,
        runtime_key: str,
        request_id: str,
        *,
        runtime_max_running: int,
        timeout_seconds: float | None = None,
        workload_class: str | WorkloadClass = WorkloadClass.STANDARD,
    ) -> GlobalExecutionPermit:
        """Synchronous convenience wrapper used by non-async execution paths."""
        self.submit(
            runtime_key,
            request_id,
            runtime_max_running=runtime_max_running,
            timeout_seconds=timeout_seconds,
            workload_class=workload_class,
        )
        return self.wait(request_id)

    def abandon(self, request_id: str) -> bool:
        """Cancel an acquisition that will not be handed to execution."""
        now = self.clock()
        with self._condition:
            waiter = self._waiters.get(request_id)
            if waiter is None:
                return False
            if waiter.state is GlobalExecutionState.QUEUED:
                waiter.state = GlobalExecutionState.CANCELLED
                waiter.finished_at = now
            elif waiter.state is GlobalExecutionState.RUNNING:
                waiter.state = GlobalExecutionState.CANCELLED
                waiter.finished_at = now
                self._release_running_locked(waiter.runtime_key)
            else:
                return False
            self._discard_waiter_locked(request_id, waiter)
            self._admit_available_locked(now)
            self._condition.notify_all()
            return True

    def release(self, request_id: str) -> None:
        now = self.clock()
        with self._condition:
            waiter = self._waiters.get(request_id)
            if waiter is None:
                raise KeyError(request_id)
            if waiter.state is not GlobalExecutionState.RUNNING:
                raise RuntimeError(
                    f"request {request_id} cannot release from {waiter.state.value}"
                )
            waiter.state = GlobalExecutionState.COMPLETED
            waiter.finished_at = now
            self._release_running_locked(waiter.runtime_key)
            self._waiters.pop(request_id, None)
            self._admit_available_locked(now)
            self._condition.notify_all()

    def snapshot(self) -> GlobalExecutionSnapshot:
        now = self.clock()
        with self._condition:
            self._expire_queued_locked(now)
            self._admit_available_locked(now)
            queued_by_runtime: Counter[str] = Counter()
            queued_by_workload: Counter[str] = Counter()
            running_by_workload: Counter[str] = Counter()
            for waiter in self._waiters.values():
                if waiter.state is GlobalExecutionState.QUEUED:
                    queued_by_runtime[waiter.runtime_key] += 1
                    queued_by_workload[waiter.workload_class.value] += 1
                elif waiter.state is GlobalExecutionState.RUNNING:
                    running_by_workload[waiter.workload_class.value] += 1
            runtime_keys = sorted(set(queued_by_runtime) | set(self._runtime_inflight))
            runtimes = tuple(
                {
                    "runtime_key": runtime_key,
                    "queued": queued_by_runtime[runtime_key],
                    "running": self._runtime_inflight[runtime_key],
                }
                for runtime_key in runtime_keys
                if queued_by_runtime[runtime_key] or self._runtime_inflight[runtime_key]
            )
            workloads = tuple(
                {
                    "workload_class": workload.value,
                    "queued": queued_by_workload[workload.value],
                    "running": running_by_workload[workload.value],
                }
                for workload in WorkloadClass
                if queued_by_workload[workload.value] or running_by_workload[workload.value]
            )
            return GlobalExecutionSnapshot(
                max_running=self.max_running,
                queue_capacity=self.queue_capacity,
                inflight=self._inflight,
                queued=sum(queued_by_runtime.values()),
                runtimes=runtimes,
                workloads=workloads,
            )

    def _queued_count_locked(self) -> int:
        return sum(
            1
            for waiter in self._waiters.values()
            if waiter.state is GlobalExecutionState.QUEUED
        )

    def _schedule_runtime_locked(self, runtime_key: str) -> None:
        if runtime_key in self._runtime_in_order:
            return
        queue = self._runtime_queues.get(runtime_key)
        if not queue:
            return
        self._runtime_order.append(runtime_key)
        self._runtime_in_order.add(runtime_key)

    def _admit_available_locked(self, now: float) -> None:
        while self._inflight < self.max_running and self._runtime_order:
            selected_runtime: str | None = None
            selected_waiter: _Waiter | None = None
            selected_priority: int | None = None
            empty_runtimes: list[str] = []

            for runtime_key in tuple(self._runtime_order):
                queue = self._runtime_queues.get(runtime_key)
                if queue is None:
                    empty_runtimes.append(runtime_key)
                    continue
                candidate = self._best_queued_locked(queue, now)
                if candidate is None:
                    empty_runtimes.append(runtime_key)
                    continue
                if self._runtime_inflight[runtime_key] >= candidate.runtime_max_running:
                    continue
                priority = effective_workload_priority(
                    candidate.workload_class,
                    submitted_at=candidate.submitted_at,
                    now=now,
                )
                if selected_priority is None or priority > selected_priority:
                    selected_runtime = runtime_key
                    selected_waiter = candidate
                    selected_priority = priority

            for runtime_key in empty_runtimes:
                self._drop_runtime_from_order_locked(runtime_key)
                self._runtime_queues.pop(runtime_key, None)

            if selected_runtime is None or selected_waiter is None:
                break

            queue = self._runtime_queues[selected_runtime]
            self._drop_runtime_from_order_locked(selected_runtime)
            try:
                queue.remove(selected_waiter.request_id)
            except ValueError:
                continue

            if self._best_queued_locked(queue, now) is not None:
                self._schedule_runtime_locked(selected_runtime)
            else:
                self._runtime_queues.pop(selected_runtime, None)

            selected_waiter.state = GlobalExecutionState.RUNNING
            selected_waiter.started_at = now
            self._inflight += 1
            self._runtime_inflight[selected_runtime] += 1

    def _best_queued_locked(self, queue: deque[str], now: float) -> _Waiter | None:
        selected: _Waiter | None = None
        selected_priority: int | None = None
        for request_id in list(queue):
            waiter = self._waiters.get(request_id)
            if waiter is None or waiter.state is not GlobalExecutionState.QUEUED:
                try:
                    queue.remove(request_id)
                except ValueError:
                    pass
                continue
            if waiter.expired(now):
                waiter.state = GlobalExecutionState.EXPIRED
                waiter.finished_at = now
                try:
                    queue.remove(request_id)
                except ValueError:
                    pass
                continue
            priority = effective_workload_priority(
                waiter.workload_class,
                submitted_at=waiter.submitted_at,
                now=now,
            )
            if selected_priority is None or priority > selected_priority:
                selected = waiter
                selected_priority = priority
        return selected

    def _drop_runtime_from_order_locked(self, runtime_key: str) -> None:
        if runtime_key not in self._runtime_in_order:
            return
        try:
            self._runtime_order.remove(runtime_key)
        except ValueError:
            pass
        self._runtime_in_order.discard(runtime_key)

    def _release_running_locked(self, runtime_key: str) -> None:
        self._inflight = max(0, self._inflight - 1)
        current = self._runtime_inflight[runtime_key]
        if current <= 1:
            self._runtime_inflight.pop(runtime_key, None)
        else:
            self._runtime_inflight[runtime_key] = current - 1

    def _expire_queued_locked(self, now: float) -> None:
        changed = False
        for waiter in self._waiters.values():
            if waiter.state is GlobalExecutionState.QUEUED and waiter.expired(now):
                waiter.state = GlobalExecutionState.EXPIRED
                waiter.finished_at = now
                changed = True
        if changed:
            self._condition.notify_all()

    def _discard_waiter_locked(self, request_id: str, waiter: _Waiter) -> None:
        """Discard exactly this waiter without deleting a later same-ID submission."""
        if self._waiters.get(request_id) is waiter:
            self._waiters.pop(request_id, None)


def attach_global_execution_governor(
    owner: Any,
    governor: GlobalExecutionGovernor | None,
) -> None:
    """Attach the governor to the runtime owner without moving lifecycle state."""
    setattr(owner, _OWNER_ATTRIBUTE, governor)


def global_execution_governor_for(owner: Any) -> GlobalExecutionGovernor | None:
    """Return the governor attached to a runtime owner, if configured."""
    governor = getattr(owner, _OWNER_ATTRIBUTE, None)
    return governor if isinstance(governor, GlobalExecutionGovernor) else None
