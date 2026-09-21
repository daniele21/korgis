"""Backend-neutral bounded request scheduler foundation.

B5a owns queue/admission/deadline/cancellation semantics. Backend-native batching
remains backend-owned, and this module does not claim it can interrupt every
in-process backend after execution has started.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from .core.contracts import ErrorCode, InferenceError, InferenceRequest
from .scheduler_policy import (
    WorkloadClass,
    effective_workload_priority,
    normalize_workload_class,
)


class QueueState(str, Enum):
    QUEUED = "queued"
    ADMITTED = "admitted"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    REJECTED = "rejected"


_TERMINAL_STATES = {
    QueueState.COMPLETED,
    QueueState.CANCELLED,
    QueueState.EXPIRED,
    QueueState.REJECTED,
}


class CancellationToken:
    def __init__(self) -> None:
        self._event = threading.Event()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        self._event.set()


@dataclass(slots=True)
class ScheduledRequest:
    request_id: str
    request: InferenceRequest
    submitted_at: float
    deadline_at: float | None
    workload_class: WorkloadClass = WorkloadClass.STANDARD
    state: QueueState = QueueState.QUEUED
    cancellation: CancellationToken = field(default_factory=CancellationToken)
    admitted_at: float | None = None
    started_at: float | None = None
    finished_at: float | None = None

    def expired(self, now: float) -> bool:
        return self.deadline_at is not None and now >= self.deadline_at

    @property
    def terminal(self) -> bool:
        return self.state in _TERMINAL_STATES


class BoundedScheduler:
    def __init__(
        self,
        capacity: int,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self.capacity = capacity
        self.clock = clock
        self._queue: deque[str] = deque()
        self._requests: dict[str, ScheduledRequest] = {}
        self._lock = threading.RLock()

    def submit(
        self,
        request_id: str,
        request: InferenceRequest,
        *,
        timeout_seconds: float | None = None,
        workload_class: str | WorkloadClass = WorkloadClass.STANDARD,
    ) -> ScheduledRequest:
        if not request_id.strip():
            raise ValueError("request_id must be non-empty")
        if timeout_seconds is not None and timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")

        resolved_workload_class = normalize_workload_class(workload_class)
        now = self.clock()
        with self._lock:
            if request_id in self._requests:
                raise ValueError(f"request_id already exists: {request_id}")
            self._expire_queued(now)
            queued_count = sum(
                1 for item in self._requests.values() if item.state is QueueState.QUEUED
            )
            if queued_count >= self.capacity:
                rejected = ScheduledRequest(
                    request_id=request_id,
                    request=request,
                    submitted_at=now,
                    deadline_at=(now + timeout_seconds) if timeout_seconds is not None else None,
                    workload_class=resolved_workload_class,
                    state=QueueState.REJECTED,
                    finished_at=now,
                )
                self._requests[request_id] = rejected
                raise InferenceError(
                    ErrorCode.RESOURCE_EXHAUSTED,
                    "request queue is full",
                    retryable=True,
                    details={"capacity": self.capacity},
                )

            scheduled = ScheduledRequest(
                request_id=request_id,
                request=request,
                submitted_at=now,
                deadline_at=(now + timeout_seconds) if timeout_seconds is not None else None,
                workload_class=resolved_workload_class,
            )
            self._requests[request_id] = scheduled
            self._queue.append(request_id)
            return scheduled

    def next(self) -> ScheduledRequest | None:
        now = self.clock()
        with self._lock:
            self._expire_queued(now)
            scheduled = self._next_queued(now)
            if scheduled is None:
                return None
            scheduled.state = QueueState.ADMITTED
            scheduled.admitted_at = now
            return scheduled

    def start(self, request_id: str) -> ScheduledRequest:
        now = self.clock()
        with self._lock:
            scheduled = self._require(request_id)
            if scheduled.state is not QueueState.ADMITTED:
                raise RuntimeError(
                    f"request {request_id} cannot start from {scheduled.state.value}"
                )
            if scheduled.cancellation.cancelled:
                self._transition_terminal(scheduled, QueueState.CANCELLED, now)
                return scheduled
            if scheduled.expired(now):
                self._transition_terminal(scheduled, QueueState.EXPIRED, now)
                return scheduled
            scheduled.state = QueueState.RUNNING
            scheduled.started_at = now
            return scheduled

    def complete(self, request_id: str) -> ScheduledRequest:
        now = self.clock()
        with self._lock:
            scheduled = self._require(request_id)
            if scheduled.state is not QueueState.RUNNING:
                raise RuntimeError(
                    f"request {request_id} cannot complete from {scheduled.state.value}"
                )
            self._transition_terminal(scheduled, QueueState.COMPLETED, now)
            return scheduled

    def cancel(self, request_id: str) -> ScheduledRequest:
        now = self.clock()
        with self._lock:
            scheduled = self._require(request_id)
            scheduled.cancellation.cancel()
            if scheduled.state in {QueueState.QUEUED, QueueState.ADMITTED}:
                self._transition_terminal(scheduled, QueueState.CANCELLED, now)
            return scheduled

    def forget(self, request_id: str) -> None:
        """Discard a terminal request so long-running servers do not retain it forever."""
        with self._lock:
            scheduled = self._require(request_id)
            if not scheduled.terminal:
                raise RuntimeError(
                    f"request {request_id} cannot be forgotten from {scheduled.state.value}"
                )
            self._requests.pop(request_id, None)
            try:
                self._queue.remove(request_id)
            except ValueError:
                pass

    def get(self, request_id: str) -> ScheduledRequest | None:
        with self._lock:
            return self._requests.get(request_id)

    def snapshot(self) -> tuple[dict[str, object], ...]:
        now = self.clock()
        with self._lock:
            self._expire_queued(now)
            return tuple(
                {
                    "request_id": item.request_id,
                    "state": item.state.value,
                    "workload_class": item.workload_class.value,
                    "submitted_at": item.submitted_at,
                    "deadline_at": item.deadline_at,
                    "admitted_at": item.admitted_at,
                    "started_at": item.started_at,
                    "finished_at": item.finished_at,
                    "cancel_requested": item.cancellation.cancelled,
                }
                for item in sorted(self._requests.values(), key=lambda value: value.submitted_at)
            )

    def _next_queued(self, now: float) -> ScheduledRequest | None:
        self._compact_queue()
        selected: ScheduledRequest | None = None
        selected_priority: int | None = None
        for request_id in list(self._queue):
            scheduled = self._requests[request_id]
            if scheduled.cancellation.cancelled:
                self._transition_terminal(scheduled, QueueState.CANCELLED, now)
                continue
            if scheduled.expired(now):
                self._transition_terminal(scheduled, QueueState.EXPIRED, now)
                continue
            priority = effective_workload_priority(
                scheduled.workload_class,
                submitted_at=scheduled.submitted_at,
                now=now,
            )
            if selected_priority is None or priority > selected_priority:
                selected = scheduled
                selected_priority = priority

        self._compact_queue()
        if selected is not None:
            try:
                self._queue.remove(selected.request_id)
            except ValueError:
                return None
        return selected

    def _compact_queue(self) -> None:
        self._queue = deque(
            request_id
            for request_id in self._queue
            if request_id in self._requests
            and self._requests[request_id].state is QueueState.QUEUED
        )

    def _expire_queued(self, now: float) -> None:
        for request_id in list(self._queue):
            scheduled = self._requests[request_id]
            if scheduled.state is QueueState.QUEUED and scheduled.expired(now):
                self._transition_terminal(scheduled, QueueState.EXPIRED, now)

    def _require(self, request_id: str) -> ScheduledRequest:
        scheduled = self._requests.get(request_id)
        if scheduled is None:
            raise KeyError(request_id)
        return scheduled

    @staticmethod
    def _transition_terminal(
        scheduled: ScheduledRequest,
        state: QueueState,
        now: float,
    ) -> None:
        scheduled.state = state
        scheduled.finished_at = now
