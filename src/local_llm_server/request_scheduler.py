"""Product HTTP admission before transient memory and runtime leases.

Canonical request policy prepares the request first. Optional per-runtime
priority admission preserves FIFO within equal workload priority, then the
optional global execution governor bounds aggregate work with priority aging
and runtime fairness. Neither layer
replaces backend batching, transient-memory accounting or the final runtime
semaphore. Streaming requests retain every acquired execution slot until their
body iterator finishes.
"""
from __future__ import annotations

import asyncio
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .async_scheduler import AsyncRuntimeGate
from .core.contracts import ErrorCode, InferenceError
from .global_execution_governor import (
    GlobalExecutionGovernor,
    GlobalExecutionPermit,
    attach_global_execution_governor,
)
from .live_evidence import record_runtime_metrics
from .metrics import DurationMetrics, InferenceMetrics
from .request_pipeline import public_error_detail
from .scheduler_policy import RequestSchedulerSettings, scheduler_settings_from_env

_INFERENCE_PATHS = frozenset({"/v1/chat/completions", "/api/v1/chat"})
_QUEUE_WAIT_HEADER = "x-local-llm-queue-wait-ms"
_GLOBAL_WAIT_HEADER = "x-local-llm-global-wait-ms"
_WORKLOAD_CLASS_RESPONSE_HEADER = "x-local-llm-workload-class"


@dataclass(slots=True)
class _GateEntry:
    runtime: Any
    gate: AsyncRuntimeGate


class RuntimeGateRegistry:
    """Own one optional priority-aware admission gate per current runtime residency."""

    def __init__(self, settings: RequestSchedulerSettings) -> None:
        if not settings.runtime_queue_enabled or settings.queue_capacity is None:
            raise ValueError("runtime gate registry requires runtime queue settings")
        self.settings = settings
        self._entries: dict[str, _GateEntry] = {}
        self._lock = threading.RLock()

    def gate_for(self, runtime: Any) -> AsyncRuntimeGate:
        with self._lock:
            existing = self._entries.get(runtime.key)
            if existing is not None and existing.runtime is runtime:
                return existing.gate
            gate = AsyncRuntimeGate(
                capacity=int(self.settings.queue_capacity),
                max_running=max(1, int(runtime.cfg.get("max_concurrent_requests") or 1)),
            )
            self._entries[runtime.key] = _GateEntry(runtime=runtime, gate=gate)
            return gate

    def entry_count(self) -> int:
        with self._lock:
            return len(self._entries)


async def _safe_forget(gate: AsyncRuntimeGate, request_id: str) -> None:
    try:
        await gate.forget(request_id)
    except (KeyError, RuntimeError):
        pass


def install_request_scheduler(
    application: FastAPI,
    *,
    settings: RequestSchedulerSettings | None = None,
) -> FastAPI:
    """Install optional per-runtime queueing and global execution admission once."""
    if getattr(application.state, "request_scheduler_installed", False):
        return application
    application.state.request_scheduler_installed = True

    resolved = settings or scheduler_settings_from_env()
    application.state.request_scheduler_settings = resolved
    registry = RuntimeGateRegistry(resolved) if resolved.runtime_queue_enabled else None
    governor = (
        GlobalExecutionGovernor(
            max_running=int(resolved.global_max_running),
            queue_capacity=int(resolved.global_queue_capacity),
        )
        if resolved.global_governor_enabled
        else None
    )
    application.state.runtime_gate_registry = registry
    application.state.global_execution_governor = governor
    manager = getattr(application.state, "runtime_manager", None)
    if manager is not None:
        attach_global_execution_governor(manager, governor)

    if not resolved.enabled:
        return application

    @application.middleware("http")
    async def request_scheduler(request: Request, call_next):
        if request.method.upper() != "POST" or request.url.path not in _INFERENCE_PATHS:
            return await call_next(request)

        prepared = getattr(request.state, "prepared_inference_request", None)
        canonical = getattr(prepared, "canonical", None)
        manager = getattr(request.app.state, "runtime_manager", None)
        if canonical is None or manager is None:
            return await call_next(request)

        try:
            timeout_seconds = resolved.timeout_seconds_for_headers(request.headers)
            workload_class = resolved.workload_class_for_headers(request.headers)
        except ValueError as exc:
            return JSONResponse(
                status_code=400,
                content={
                    "detail": {
                        "code": ErrorCode.INVALID_REQUEST.value,
                        "message": str(exc),
                        "retryable": False,
                        "details": {},
                    }
                },
            )

        request.state.scheduler_workload_class = workload_class.value
        admission_started = time.monotonic()
        deadline = (
            admission_started + timeout_seconds
            if timeout_seconds is not None
            else None
        )
        base_request_id = uuid.uuid4().hex
        for attempt in range(2):
            try:
                runtime = manager.resolve(canonical.model)
            except LookupError:
                return await call_next(request)

            gate = registry.gate_for(runtime) if registry is not None else None
            gate_request_id = f"{base_request_id}-runtime-{attempt}"
            global_request_id = f"{base_request_id}-global-{attempt}"
            permit: GlobalExecutionPermit | None = None
            local_acquired = False

            if gate is not None:
                remaining = _remaining_seconds(deadline)
                if remaining is not None and remaining <= 0:
                    return _scheduler_error_response(_admission_timeout_error())
                try:
                    await gate.acquire(
                        gate_request_id,
                        canonical,
                        timeout_seconds=remaining,
                        workload_class=workload_class,
                    )
                    local_acquired = True
                except InferenceError as exc:
                    await _safe_forget(gate, gate_request_id)
                    return _scheduler_error_response(exc)
                except asyncio.CancelledError:
                    await _safe_forget(gate, gate_request_id)
                    raise

            if not _runtime_is_current(manager, canonical.model, runtime):
                await _release_admission(
                    gate=gate,
                    gate_request_id=gate_request_id if local_acquired else None,
                    governor=None,
                    global_request_id=None,
                    cancel_requested=True,
                )
                if attempt == 0:
                    continue
                return _residency_changed_response(canonical.model)

            if governor is not None:
                remaining = _remaining_seconds(deadline)
                if remaining is not None and remaining <= 0:
                    await _release_admission(
                        gate=gate,
                        gate_request_id=gate_request_id if local_acquired else None,
                        governor=None,
                        global_request_id=None,
                        cancel_requested=True,
                    )
                    return _scheduler_error_response(_admission_timeout_error())
                try:
                    governor.submit(
                        runtime.key,
                        global_request_id,
                        runtime_max_running=max(
                            1,
                            int(runtime.cfg.get("max_concurrent_requests") or 1),
                        ),
                        timeout_seconds=remaining,
                        workload_class=workload_class,
                    )
                    permit = await asyncio.to_thread(governor.wait, global_request_id)
                except InferenceError as exc:
                    await _release_admission(
                        gate=gate,
                        gate_request_id=gate_request_id if local_acquired else None,
                        governor=None,
                        global_request_id=None,
                        cancel_requested=True,
                    )
                    return _scheduler_error_response(exc)
                except asyncio.CancelledError:
                    governor.abandon(global_request_id)
                    await _release_admission(
                        gate=gate,
                        gate_request_id=gate_request_id if local_acquired else None,
                        governor=None,
                        global_request_id=None,
                        cancel_requested=True,
                    )
                    raise

            if not _runtime_is_current(manager, canonical.model, runtime):
                await _release_admission(
                    gate=gate,
                    gate_request_id=gate_request_id if local_acquired else None,
                    governor=governor if permit is not None else None,
                    global_request_id=global_request_id if permit is not None else None,
                    cancel_requested=True,
                )
                if attempt == 0:
                    continue
                return _residency_changed_response(canonical.model)

            queue_wait_ms = max(0.0, (time.monotonic() - admission_started) * 1000.0)
            request.state.queue_wait_ms = queue_wait_ms
            request.state.scheduler_request_id = (
                gate_request_id if local_acquired else global_request_id
            )
            request.state.global_execution_wait_ms = (
                permit.wait_ms if permit is not None else None
            )
            try:
                response = await call_next(request)
            except asyncio.CancelledError:
                await _release_admission(
                    gate=gate,
                    gate_request_id=gate_request_id if local_acquired else None,
                    governor=governor if permit is not None else None,
                    global_request_id=global_request_id if permit is not None else None,
                    cancel_requested=True,
                )
                raise
            except Exception:
                await _release_admission(
                    gate=gate,
                    gate_request_id=gate_request_id if local_acquired else None,
                    governor=governor if permit is not None else None,
                    global_request_id=global_request_id if permit is not None else None,
                    cancel_requested=True,
                )
                raise

            response.headers[_QUEUE_WAIT_HEADER] = f"{queue_wait_ms:.3f}"
            response.headers[_WORKLOAD_CLASS_RESPONSE_HEADER] = workload_class.value
            if permit is not None:
                response.headers[_GLOBAL_WAIT_HEADER] = f"{permit.wait_ms:.3f}"
            if canonical.stream is True and hasattr(response, "body_iterator"):
                response.body_iterator = _hold_gate_for_stream(
                    response.body_iterator,
                    gate=gate,
                    request_id=gate_request_id if local_acquired else None,
                    governor=governor if permit is not None else None,
                    global_request_id=global_request_id if permit is not None else None,
                )
                return response

            await _release_admission(
                gate=gate,
                gate_request_id=gate_request_id if local_acquired else None,
                governor=governor if permit is not None else None,
                global_request_id=global_request_id if permit is not None else None,
            )
            record_runtime_metrics(
                runtime,
                InferenceMetrics(
                    durations=DurationMetrics(queue_wait_ms=queue_wait_ms),
                    sources={
                        "queue_wait_ms": "request_scheduler.pre_execution_admission_wall_clock"
                    },
                ),
            )
            return response

        raise RuntimeError("request scheduler exhausted residency retry loop")

    return application


def _remaining_seconds(deadline: float | None) -> float | None:
    if deadline is None:
        return None
    return deadline - time.monotonic()


def _runtime_is_current(manager: Any, model: str | None, runtime: Any) -> bool:
    try:
        return manager.resolve(model) is runtime
    except LookupError:
        return False


def _admission_timeout_error() -> InferenceError:
    return InferenceError(
        ErrorCode.TIMEOUT,
        "request deadline expired while waiting for execution admission",
        retryable=True,
        details={},
    )


def _residency_changed_response(model: str | None) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={
            "detail": {
                "code": ErrorCode.MODEL_NOT_RESIDENT.value,
                "message": "runtime residency changed while request was queued; retry request",
                "retryable": True,
                "details": {"model": model},
            }
        },
    )


async def _release_admission(
    *,
    gate: AsyncRuntimeGate | None,
    gate_request_id: str | None,
    governor: GlobalExecutionGovernor | None,
    global_request_id: str | None,
    cancel_requested: bool = False,
) -> None:
    if governor is not None and global_request_id is not None:
        try:
            governor.release(global_request_id)
        except (KeyError, RuntimeError):
            governor.abandon(global_request_id)
    if gate is not None and gate_request_id is not None:
        try:
            await gate.release(gate_request_id, cancel_requested=cancel_requested)
        finally:
            await _safe_forget(gate, gate_request_id)


async def _hold_gate_for_stream(
    iterator,
    *,
    gate: AsyncRuntimeGate | None,
    request_id: str | None,
    governor: GlobalExecutionGovernor | None = None,
    global_request_id: str | None = None,
):
    cancelled = False
    try:
        async for chunk in iterator:
            yield chunk
    except asyncio.CancelledError:
        cancelled = True
        raise
    finally:
        await _release_admission(
            gate=gate,
            gate_request_id=request_id,
            governor=governor,
            global_request_id=global_request_id,
            cancel_requested=cancelled,
        )


def _scheduler_error_response(error: InferenceError) -> JSONResponse:
    status_code = 400
    if error.code is ErrorCode.RESOURCE_EXHAUSTED:
        status_code = 429
    elif error.code is ErrorCode.TIMEOUT:
        status_code = 408
    elif error.code is ErrorCode.CANCELLED:
        status_code = 409
    return JSONResponse(
        status_code=status_code,
        content={"detail": public_error_detail(error)},
    )
