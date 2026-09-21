"""Async FIFO admission gate over the deterministic BoundedScheduler contract.

The gate owns queue waiting before runtime leases. Backend-native batching and
the runtime's own semaphore remain final execution safeguards.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from .core.contracts import ErrorCode, InferenceError, InferenceRequest
from .scheduler import BoundedScheduler, QueueState, ScheduledRequest
from .scheduler_policy import WorkloadClass


@dataclass(frozen=True, slots=True)
class GateSnapshot:
    max_running: int
    inflight: int
    capacity: int
    requests: tuple[dict[str, object], ...]

    def to_public_dict(self) -> dict[str, object]:
        return {
            "max_running": self.max_running,
            "inflight": self.inflight,
            "capacity": self.capacity,
            "requests": [dict(item) for item in self.requests],
        }


class AsyncRuntimeGate:
    """Bounded FIFO queue that admits at most ``max_running`` requests."""

    def __init__(
        self,
        *,
        capacity: int,
        max_running: int,
        scheduler: BoundedScheduler | None = None,
    ) -> None:
        if max_running < 1:
            raise ValueError("max_running must be >= 1")
        self.scheduler = scheduler or BoundedScheduler(capacity)
        if self.scheduler.capacity != capacity:
            raise ValueError("scheduler capacity must match gate capacity")
        self.max_running = max_running
        self._inflight = 0
        self._condition = asyncio.Condition()

    async def acquire(
        self,
        request_id: str,
        request: InferenceRequest,
        *,
        timeout_seconds: float | None = None,
        workload_class: str | WorkloadClass = WorkloadClass.STANDARD,
    ) -> ScheduledRequest:
        scheduled = self.scheduler.submit(
            request_id,
            request,
            timeout_seconds=timeout_seconds,
            workload_class=workload_class,
        )
        try:
            while True:
                async with self._condition:
                    state = scheduled.state
                    if state is QueueState.ADMITTED:
                        started = self.scheduler.start(request_id)
                        if started.state is QueueState.RUNNING:
                            return started
                        self._release_slot_locked()
                        self._condition.notify_all()
                        self._raise_terminal(started)

                    if scheduled.terminal:
                        self._raise_terminal(scheduled)

                    if self._inflight < self.max_running:
                        admitted = self.scheduler.next()
                        if admitted is not None:
                            self._inflight += 1
                            self._condition.notify_all()
                            continue

                    remaining = self._remaining_seconds(scheduled)
                    if remaining is not None and remaining <= 0:
                        self.scheduler.snapshot()
                        continue
                    if remaining is None:
                        await self._condition.wait()
                    else:
                        try:
                            await asyncio.wait_for(self._condition.wait(), timeout=remaining)
                        except asyncio.TimeoutError:
                            self.scheduler.snapshot()
        except asyncio.CancelledError:
            async with self._condition:
                previous = scheduled.state
                self.scheduler.cancel(request_id)
                if previous is QueueState.ADMITTED:
                    self._release_slot_locked()
                self._condition.notify_all()
            raise

    async def release(
        self,
        request_id: str,
        *,
        cancel_requested: bool = False,
    ) -> ScheduledRequest:
        async with self._condition:
            scheduled = self.scheduler.get(request_id)
            if scheduled is None:
                raise KeyError(request_id)
            previous = scheduled.state
            if cancel_requested:
                self.scheduler.cancel(request_id)
            if scheduled.state is QueueState.RUNNING:
                self.scheduler.complete(request_id)
            elif scheduled.state is QueueState.ADMITTED:
                self.scheduler.cancel(request_id)

            if previous in {QueueState.RUNNING, QueueState.ADMITTED}:
                self._release_slot_locked()
            self._condition.notify_all()
            return scheduled

    async def cancel(self, request_id: str) -> ScheduledRequest:
        async with self._condition:
            scheduled = self.scheduler.get(request_id)
            if scheduled is None:
                raise KeyError(request_id)
            previous = scheduled.state
            result = self.scheduler.cancel(request_id)
            if previous is QueueState.ADMITTED:
                self._release_slot_locked()
            self._condition.notify_all()
            return result

    async def forget(self, request_id: str) -> None:
        """Remove terminal request bookkeeping after product evidence is captured."""
        async with self._condition:
            self.scheduler.forget(request_id)

    async def snapshot(self) -> GateSnapshot:
        async with self._condition:
            return GateSnapshot(
                max_running=self.max_running,
                inflight=self._inflight,
                capacity=self.scheduler.capacity,
                requests=self.scheduler.snapshot(),
            )

    def _remaining_seconds(self, scheduled: ScheduledRequest) -> float | None:
        if scheduled.deadline_at is None:
            return None
        return scheduled.deadline_at - self.scheduler.clock()

    def _release_slot_locked(self) -> None:
        self._inflight = max(0, self._inflight - 1)

    def _raise_terminal(self, scheduled: ScheduledRequest) -> None:
        if scheduled.state is QueueState.EXPIRED:
            raise InferenceError(
                ErrorCode.TIMEOUT,
                "request deadline expired while waiting for runtime admission",
                retryable=True,
                details={"request_id": scheduled.request_id},
            )
        if scheduled.state is QueueState.CANCELLED:
            raise InferenceError(
                ErrorCode.CANCELLED,
                "request was cancelled before runtime execution",
                retryable=False,
                details={"request_id": scheduled.request_id},
            )
        if scheduled.state is QueueState.REJECTED:
            raise InferenceError(
                ErrorCode.RESOURCE_EXHAUSTED,
                "request queue is full",
                retryable=True,
                details={"capacity": self.scheduler.capacity},
            )
        raise RuntimeError(
            f"request {scheduled.request_id} cannot be admitted from {scheduled.state.value}"
        )
