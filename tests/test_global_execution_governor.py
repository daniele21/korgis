from __future__ import annotations

import threading
import time

import pytest

from local_llm_server.core.contracts import ErrorCode, InferenceError
from local_llm_server.global_execution_governor import GlobalExecutionGovernor


class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds: float):
        self.now += seconds


def _wait_until(predicate, *, timeout: float = 1.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.002)
    raise AssertionError("condition was not reached before timeout")


def test_governor_bounds_global_running_and_preserves_runtime_eligibility():
    governor = GlobalExecutionGovernor(max_running=2, queue_capacity=2)
    governor.acquire("a", "a1", runtime_max_running=1)

    acquired: list[str] = []

    def acquire_a2() -> None:
        governor.acquire("a", "a2", runtime_max_running=1)
        acquired.append("a2")

    waiter = threading.Thread(target=acquire_a2)
    waiter.start()
    _wait_until(lambda: governor.snapshot().queued == 1)

    governor.acquire("b", "b1", runtime_max_running=1)
    snapshot = governor.snapshot()
    assert snapshot.inflight == 2
    assert snapshot.queued == 1
    by_runtime = {item["runtime_key"]: item for item in snapshot.runtimes}
    assert by_runtime["a"] == {"runtime_key": "a", "queued": 1, "running": 1}
    assert by_runtime["b"] == {"runtime_key": "b", "queued": 0, "running": 1}

    governor.release("a1")
    _wait_until(lambda: acquired == ["a2"])
    governor.release("a2")
    governor.release("b1")
    waiter.join(timeout=1)
    assert not waiter.is_alive()
    assert governor.snapshot().inflight == 0


def test_round_robin_fairness_prevents_one_runtime_from_starving_another():
    governor = GlobalExecutionGovernor(max_running=1, queue_capacity=4)
    governor.acquire("a", "a1", runtime_max_running=1)
    acquired: list[str] = []

    def wait_for(runtime_key: str, request_id: str) -> None:
        governor.acquire(runtime_key, request_id, runtime_max_running=1)
        acquired.append(request_id)

    a2 = threading.Thread(target=wait_for, args=("a", "a2"))
    a3 = threading.Thread(target=wait_for, args=("a", "a3"))
    b1 = threading.Thread(target=wait_for, args=("b", "b1"))
    a2.start()
    _wait_until(lambda: governor.snapshot().queued == 1)
    a3.start()
    _wait_until(lambda: governor.snapshot().queued == 2)
    b1.start()
    _wait_until(lambda: governor.snapshot().queued == 3)

    governor.release("a1")
    _wait_until(lambda: acquired == ["a2"])
    governor.release("a2")
    _wait_until(lambda: acquired == ["a2", "b1"])
    governor.release("b1")
    _wait_until(lambda: acquired == ["a2", "b1", "a3"])
    governor.release("a3")

    for thread in (a2, a3, b1):
        thread.join(timeout=1)
        assert not thread.is_alive()
    assert governor.snapshot().queued == 0


def test_global_queue_overflow_is_retryable_resource_exhaustion():
    governor = GlobalExecutionGovernor(max_running=1, queue_capacity=1)
    governor.acquire("a", "running", runtime_max_running=1)

    error: list[InferenceError] = []

    def queued() -> None:
        try:
            governor.acquire("b", "queued", runtime_max_running=1)
        except InferenceError as exc:
            error.append(exc)

    waiter = threading.Thread(target=queued)
    waiter.start()
    _wait_until(lambda: governor.snapshot().queued == 1)

    with pytest.raises(InferenceError) as exc_info:
        governor.acquire("c", "overflow", runtime_max_running=1)
    assert exc_info.value.code is ErrorCode.RESOURCE_EXHAUSTED
    assert exc_info.value.retryable is True

    assert governor.abandon("queued") is True
    waiter.join(timeout=1)
    assert len(error) == 1
    assert error[0].code is ErrorCode.CANCELLED
    governor.release("running")


def test_global_wait_deadline_expires_before_execution():
    governor = GlobalExecutionGovernor(max_running=1, queue_capacity=1)
    governor.acquire("a", "running", runtime_max_running=1)

    with pytest.raises(InferenceError) as exc_info:
        governor.acquire(
            "b",
            "deadline",
            runtime_max_running=1,
            timeout_seconds=0.01,
        )
    assert exc_info.value.code is ErrorCode.TIMEOUT
    assert exc_info.value.retryable is True
    governor.release("running")
    assert governor.snapshot().queued == 0


def test_abandoning_running_permit_releases_slot_without_leak():
    governor = GlobalExecutionGovernor(max_running=1, queue_capacity=1)
    governor.acquire("a", "running", runtime_max_running=1)
    assert governor.abandon("running") is True
    assert governor.snapshot().inflight == 0

    next_permit = governor.acquire("b", "running", runtime_max_running=1)
    assert next_permit.runtime_key == "b"
    governor.release("running")


def test_abandoned_waiter_cannot_delete_later_same_id_submission():
    governor = GlobalExecutionGovernor(max_running=1, queue_capacity=2)
    governor.acquire("a", "running", runtime_max_running=1)
    cancelled: list[ErrorCode] = []

    governor.submit("b", "reuse", runtime_max_running=1)

    def wait_for_cancelled_submission() -> None:
        try:
            governor.wait("reuse")
        except InferenceError as exc:
            cancelled.append(exc.code)

    old_waiter = threading.Thread(target=wait_for_cancelled_submission)
    old_waiter.start()
    _wait_until(lambda: governor.snapshot().queued == 1)
    assert governor.abandon("reuse") is True

    governor.submit("c", "reuse", runtime_max_running=1)
    governor.release("running")
    permit = governor.wait("reuse")
    assert permit.runtime_key == "c"
    governor.release("reuse")

    old_waiter.join(timeout=1)
    assert not old_waiter.is_alive()
    assert cancelled == [ErrorCode.CANCELLED]
    assert governor.snapshot().inflight == 0
    assert governor.snapshot().queued == 0


def test_public_snapshot_contains_no_request_identity_or_content():
    governor = GlobalExecutionGovernor(max_running=1, queue_capacity=2)
    governor.acquire("model-a", "private-request-id", runtime_max_running=1)
    rendered = str(governor.snapshot().to_public_dict())
    assert "private-request-id" not in rendered
    assert "prompt" not in rendered
    assert "input_text" not in rendered
    assert "model-a" in rendered
    governor.release("private-request-id")


def test_global_priority_selects_interactive_work_before_background_work():
    governor = GlobalExecutionGovernor(max_running=1, queue_capacity=3)
    governor.acquire("a", "running", runtime_max_running=1)
    governor.submit(
        "b",
        "background",
        runtime_max_running=1,
        workload_class="background",
    )
    governor.submit(
        "c",
        "interactive",
        runtime_max_running=1,
        workload_class="interactive",
    )

    governor.release("running")
    by_workload = {
        item["workload_class"]: item
        for item in governor.snapshot().workloads
    }
    assert by_workload["interactive"]["running"] == 1
    assert by_workload["background"]["queued"] == 1

    governor.release("interactive")
    governor.release("background")
    assert governor.snapshot().inflight == 0


def test_global_priority_aging_prevents_background_starvation():
    clock = _Clock()
    governor = GlobalExecutionGovernor(
        max_running=1,
        queue_capacity=3,
        clock=clock,
    )
    governor.acquire("a", "running", runtime_max_running=1)
    governor.submit(
        "b",
        "background",
        runtime_max_running=1,
        workload_class="background",
    )
    clock.advance(31)
    governor.submit(
        "c",
        "interactive",
        runtime_max_running=1,
        workload_class="interactive",
    )

    governor.release("running")
    by_workload = {
        item["workload_class"]: item
        for item in governor.snapshot().workloads
    }
    assert by_workload["background"]["running"] == 1
    assert by_workload["interactive"]["queued"] == 1

    governor.release("background")
    governor.release("interactive")
