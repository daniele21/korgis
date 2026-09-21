from __future__ import annotations

import pytest

from local_llm_server.core.contracts import ErrorCode, InferenceError, InferenceRequest, TaskType
from local_llm_server.scheduler import BoundedScheduler, QueueState


class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds: float):
        self.now += seconds


def _request(model: str = "demo") -> InferenceRequest:
    return InferenceRequest(task=TaskType.CHAT, model=model, input_text="hello")


def test_fifo_admission_and_completion_lifecycle():
    clock = _Clock()
    scheduler = BoundedScheduler(2, clock=clock)
    scheduler.submit("a", _request())
    clock.advance(0.1)
    scheduler.submit("b", _request())

    first = scheduler.next()
    assert first is not None and first.request_id == "a"
    assert first.state is QueueState.ADMITTED
    assert scheduler.start("a").state is QueueState.RUNNING
    clock.advance(1)
    assert scheduler.complete("a").state is QueueState.COMPLETED

    second = scheduler.next()
    assert second is not None and second.request_id == "b"


def test_full_queue_rejects_with_typed_resource_error():
    scheduler = BoundedScheduler(1, clock=lambda: 0.0)
    scheduler.submit("a", _request())

    with pytest.raises(InferenceError) as exc_info:
        scheduler.submit("b", _request())

    assert exc_info.value.code is ErrorCode.RESOURCE_EXHAUSTED
    assert exc_info.value.retryable is True
    assert scheduler.get("b").state is QueueState.REJECTED


def test_expired_queued_request_is_never_admitted():
    clock = _Clock()
    scheduler = BoundedScheduler(2, clock=clock)
    scheduled = scheduler.submit("a", _request(), timeout_seconds=1.0)
    clock.advance(1.0)

    assert scheduler.next() is None
    assert scheduled.state is QueueState.EXPIRED
    assert scheduled.finished_at == 1.0


def test_expiry_frees_queue_capacity_before_new_submission():
    clock = _Clock()
    scheduler = BoundedScheduler(1, clock=clock)
    scheduler.submit("old", _request(), timeout_seconds=1.0)
    clock.advance(2.0)

    fresh = scheduler.submit("fresh", _request())
    assert fresh.state is QueueState.QUEUED
    assert scheduler.get("old").state is QueueState.EXPIRED


def test_cancelled_queued_request_is_not_admitted():
    scheduler = BoundedScheduler(2, clock=lambda: 0.0)
    scheduled = scheduler.submit("a", _request())

    scheduler.cancel("a")

    assert scheduled.state is QueueState.CANCELLED
    assert scheduled.cancellation.cancelled is True
    assert scheduler.next() is None


def test_cancel_after_admission_prevents_start():
    clock = _Clock()
    scheduler = BoundedScheduler(1, clock=clock)
    scheduler.submit("a", _request())
    scheduler.next()
    cancelled = scheduler.cancel("a")

    assert cancelled.state is QueueState.CANCELLED
    with pytest.raises(RuntimeError, match="cannot start"):
        scheduler.start("a")


def test_running_cancel_sets_token_without_claiming_backend_interruption():
    scheduler = BoundedScheduler(1, clock=lambda: 0.0)
    scheduler.submit("a", _request())
    scheduler.next()
    running = scheduler.start("a")

    scheduler.cancel("a")

    assert running.state is QueueState.RUNNING
    assert running.cancellation.cancelled is True


def test_start_after_deadline_expires_admitted_request():
    clock = _Clock()
    scheduler = BoundedScheduler(1, clock=clock)
    scheduler.submit("a", _request(), timeout_seconds=1.0)
    scheduler.next()
    clock.advance(2.0)

    result = scheduler.start("a")
    assert result.state is QueueState.EXPIRED


def test_snapshot_exposes_states_without_request_content():
    scheduler = BoundedScheduler(1, clock=lambda: 0.0)
    scheduler.submit("a", _request())
    [snapshot] = scheduler.snapshot()

    assert snapshot["request_id"] == "a"
    assert snapshot["state"] == "queued"
    assert "request" not in snapshot
    assert "hello" not in str(snapshot)


def test_higher_priority_workload_overtakes_earlier_lower_priority_request():
    clock = _Clock()
    scheduler = BoundedScheduler(3, clock=clock)
    scheduler.submit("background", _request(), workload_class="background")
    clock.advance(0.1)
    scheduler.submit("interactive", _request(), workload_class="interactive")

    selected = scheduler.next()
    assert selected is not None
    assert selected.request_id == "interactive"
    assert selected.workload_class.value == "interactive"

    next_selected = scheduler.next()
    assert next_selected is not None
    assert next_selected.request_id == "background"


def test_aging_lets_old_background_work_overtake_fresh_interactive_work():
    clock = _Clock()
    scheduler = BoundedScheduler(3, clock=clock)
    scheduler.submit("background", _request(), workload_class="background")
    clock.advance(31)
    scheduler.submit("interactive", _request(), workload_class="interactive")

    selected = scheduler.next()
    assert selected is not None
    assert selected.request_id == "background"
