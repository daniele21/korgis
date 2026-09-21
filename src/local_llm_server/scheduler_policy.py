"""Explicit product settings for request scheduling and global execution admission.

Queueing remains opt-in. A configured timeout applies only while waiting for
pre-execution admission (per-runtime queue and/or global governor); it is not
presented as an end-to-end inference deadline. Workload classes are optional:
requests default to standard so clients that do not opt in retain the existing
scheduling behavior.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from typing import Mapping


_QUEUE_CAPACITY_ENV = "LOCAL_LLM_REQUEST_QUEUE_CAPACITY"
_QUEUE_TIMEOUT_ENV = "LOCAL_LLM_QUEUE_TIMEOUT_MS"
_QUEUE_TIMEOUT_HEADER = "x-local-llm-queue-timeout-ms"
_WORKLOAD_CLASS_HEADER = "x-local-llm-workload-class"
_GLOBAL_MAX_RUNNING_ENV = "LOCAL_LLM_GLOBAL_MAX_RUNNING"
_GLOBAL_QUEUE_CAPACITY_ENV = "LOCAL_LLM_GLOBAL_QUEUE_CAPACITY"
_PRIORITY_AGING_SECONDS = 1.0


class WorkloadClass(str, Enum):
    INTERACTIVE = "interactive"
    STANDARD = "standard"
    BATCH = "batch"
    BACKGROUND = "background"


_WORKLOAD_BASE_PRIORITY = {
    WorkloadClass.INTERACTIVE: 30,
    WorkloadClass.STANDARD: 20,
    WorkloadClass.BATCH: 10,
    WorkloadClass.BACKGROUND: 0,
}


def normalize_workload_class(
    value: str | WorkloadClass | None,
) -> WorkloadClass:
    if value is None:
        return WorkloadClass.STANDARD
    if isinstance(value, WorkloadClass):
        return value
    normalized = str(value).strip().lower()
    try:
        return WorkloadClass(normalized)
    except ValueError as exc:
        allowed = ", ".join(item.value for item in WorkloadClass)
        raise ValueError(
            f"{_WORKLOAD_CLASS_HEADER} must be one of: {allowed}"
        ) from exc


def workload_base_priority(workload_class: str | WorkloadClass) -> int:
    resolved = normalize_workload_class(workload_class)
    return _WORKLOAD_BASE_PRIORITY[resolved]


def effective_workload_priority(
    workload_class: str | WorkloadClass,
    *,
    submitted_at: float,
    now: float,
) -> int:
    """Return class priority plus deterministic wait aging."""
    waited = max(0.0, now - submitted_at)
    aging_points = int(waited / _PRIORITY_AGING_SECONDS)
    return workload_base_priority(workload_class) + aging_points


@dataclass(frozen=True, slots=True)
class RequestSchedulerSettings:
    queue_capacity: int | None = None
    default_queue_timeout_ms: int | None = None
    global_max_running: int | None = None
    global_queue_capacity: int | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("queue_capacity", self.queue_capacity),
            ("default_queue_timeout_ms", self.default_queue_timeout_ms),
            ("global_max_running", self.global_max_running),
            ("global_queue_capacity", self.global_queue_capacity),
        ):
            if value is not None and value < 1:
                raise ValueError(f"{name} must be >= 1")
        if (self.global_max_running is None) != (self.global_queue_capacity is None):
            raise ValueError(
                "global execution governor requires both global_max_running and "
                "global_queue_capacity"
            )
        if self.default_queue_timeout_ms is not None and not self.enabled:
            raise ValueError(
                "queue timeout requires request queue capacity or global execution governor"
            )

    @property
    def runtime_queue_enabled(self) -> bool:
        return self.queue_capacity is not None

    @property
    def global_governor_enabled(self) -> bool:
        return self.global_max_running is not None and self.global_queue_capacity is not None

    @property
    def enabled(self) -> bool:
        return self.runtime_queue_enabled or self.global_governor_enabled

    def timeout_seconds_for_headers(self, headers: Mapping[str, str]) -> float | None:
        raw = headers.get(_QUEUE_TIMEOUT_HEADER)
        if raw is not None:
            try:
                milliseconds = int(raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{_QUEUE_TIMEOUT_HEADER} must be a positive integer") from exc
            if milliseconds < 1:
                raise ValueError(f"{_QUEUE_TIMEOUT_HEADER} must be >= 1")
            return milliseconds / 1000.0
        if self.default_queue_timeout_ms is None:
            return None
        return self.default_queue_timeout_ms / 1000.0

    def workload_class_for_headers(self, headers: Mapping[str, str]) -> WorkloadClass:
        return normalize_workload_class(headers.get(_WORKLOAD_CLASS_HEADER))

    def to_public_dict(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "runtime_queue_enabled": self.runtime_queue_enabled,
            "queue_capacity": self.queue_capacity,
            "global_governor_enabled": self.global_governor_enabled,
            "global_max_running": self.global_max_running,
            "global_queue_capacity": self.global_queue_capacity,
            "global_fairness": (
                "priority_aging_runtime_round_robin"
                if self.global_governor_enabled
                else None
            ),
            "default_queue_timeout_ms": self.default_queue_timeout_ms,
            "request_timeout_header": _QUEUE_TIMEOUT_HEADER,
            "timeout_scope": "pre_execution_admission_wait_only",
            "workload_class_header": _WORKLOAD_CLASS_HEADER,
            "workload_default": WorkloadClass.STANDARD.value,
            "workload_priorities": {
                item.value: _WORKLOAD_BASE_PRIORITY[item]
                for item in WorkloadClass
            },
            "priority_aging_seconds": _PRIORITY_AGING_SECONDS,
        }


def scheduler_settings_from_env(
    env: Mapping[str, str] | None = None,
) -> RequestSchedulerSettings:
    source = os.environ if env is None else env
    return RequestSchedulerSettings(
        queue_capacity=_optional_positive_int(source.get(_QUEUE_CAPACITY_ENV), _QUEUE_CAPACITY_ENV),
        default_queue_timeout_ms=_optional_positive_int(
            source.get(_QUEUE_TIMEOUT_ENV), _QUEUE_TIMEOUT_ENV
        ),
        global_max_running=_optional_positive_int(
            source.get(_GLOBAL_MAX_RUNNING_ENV), _GLOBAL_MAX_RUNNING_ENV
        ),
        global_queue_capacity=_optional_positive_int(
            source.get(_GLOBAL_QUEUE_CAPACITY_ENV), _GLOBAL_QUEUE_CAPACITY_ENV
        ),
    )


def _optional_positive_int(value: str | None, name: str) -> int | None:
    if value is None or not value.strip():
        return None
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if parsed < 1:
        raise ValueError(f"{name} must be >= 1")
    return parsed
