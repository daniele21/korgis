"""Canonical metrics for non-streaming JSON completion responses.

Response JSON is observed transiently and only up to a bounded byte limit. When
usage/timing evidence cannot be parsed, token/backend timing fields remain
unavailable; wall-clock total duration may still be measured on completed bodies.
"""
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI, Request

from .live_evidence import record_runtime_metrics
from .metrics import DurationMetrics, InferenceMetrics
from .metrics_adapters import merge_metrics, metrics_from_completion_response

_INFERENCE_PATHS = frozenset({"/v1/chat/completions", "/api/v1/chat"})
_DEFAULT_CAPTURE_BYTES = 2 * 1024 * 1024


def install_completion_metrics(
    application: FastAPI,
    *,
    max_capture_bytes: int = _DEFAULT_CAPTURE_BYTES,
) -> FastAPI:
    """Install bounded response metrics capture exactly once."""
    if getattr(application.state, "completion_metrics_installed", False):
        return application
    if max_capture_bytes < 1:
        raise ValueError("max_capture_bytes must be >= 1")
    application.state.completion_metrics_installed = True
    application.state.completion_metrics_max_capture_bytes = max_capture_bytes

    @application.middleware("http")
    async def completion_metrics(request: Request, call_next):
        if request.method.upper() != "POST" or request.url.path not in _INFERENCE_PATHS:
            return await call_next(request)

        started_at = time.perf_counter()
        response = await call_next(request)
        prepared = getattr(request.state, "prepared_inference_request", None)
        canonical = getattr(prepared, "canonical", None)
        if (
            canonical is None
            or canonical.stream is True
            or response.status_code >= 400
            or not hasattr(response, "body_iterator")
        ):
            return response

        runtime = _request_runtime(request, canonical.model)
        if runtime is None:
            return response

        original_iterator = response.body_iterator
        content_type = str(response.headers.get("content-type") or "").lower()
        capture_json = "application/json" in content_type

        async def observed_body():
            chunks: list[bytes] = []
            captured_bytes = 0
            capture_overflow = False
            completed = False
            try:
                async for chunk in original_iterator:
                    raw = chunk if isinstance(chunk, bytes) else str(chunk).encode("utf-8")
                    if capture_json and not capture_overflow:
                        next_size = captured_bytes + len(raw)
                        if next_size <= max_capture_bytes:
                            chunks.append(raw)
                            captured_bytes = next_size
                        else:
                            chunks.clear()
                            capture_overflow = True
                    yield chunk
                completed = True
            except asyncio.CancelledError:
                raise
            finally:
                total_ms = (
                    max(0.0, (time.perf_counter() - started_at) * 1000.0)
                    if completed
                    else None
                )
                queue_wait_ms = _nonnegative_number(
                    getattr(request.state, "queue_wait_ms", None)
                )
                queue_metrics = InferenceMetrics(
                    durations=DurationMetrics(
                        queue_wait_ms=queue_wait_ms,
                        total_ms=total_ms,
                    ),
                    resource_snapshot_id=getattr(
                        request.state,
                        "resource_snapshot_id",
                        None,
                    ),
                    sources={
                        **(
                            {"queue_wait_ms": "request_scheduler.admission_wall_clock"}
                            if queue_wait_ms is not None
                            else {}
                        ),
                        **(
                            {"total_ms": "http_nonstream.completed_body_wall_clock"}
                            if total_ms is not None
                            else {}
                        ),
                    },
                )

                backend_metrics = None
                if completed and capture_json and not capture_overflow and chunks:
                    backend_metrics = _metrics_from_captured_json(
                        b"".join(chunks),
                        total_ms=total_ms,
                    )
                chunks.clear()

                metrics = (
                    merge_metrics(queue_metrics, backend_metrics)
                    if backend_metrics is not None
                    else queue_metrics
                )
                if any(metrics.sources):
                    record_runtime_metrics(runtime, metrics)

        response.body_iterator = observed_body()
        return response

    return application


def _metrics_from_captured_json(
    raw: bytes,
    *,
    total_ms: float | None,
) -> InferenceMetrics | None:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, Mapping):
        return None
    return metrics_from_completion_response(payload, total_ms=total_ms)


def _request_runtime(request: Request, model: str | None):
    scheduled_runtime = getattr(request.state, "scheduled_runtime", None)
    manager = getattr(request.app.state, "runtime_manager", None)
    if manager is None:
        return None
    if scheduled_runtime is not None:
        try:
            if manager.resolve(model) is not scheduled_runtime:
                return None
        except (LookupError, RuntimeError):
            return None
        return scheduled_runtime
    try:
        return manager.resolve(model)
    except (LookupError, RuntimeError):
        return None


def _nonnegative_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    return numeric if numeric >= 0 else None
