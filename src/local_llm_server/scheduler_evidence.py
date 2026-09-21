"""Public-safe aggregate scheduler and global-governor evidence."""
from __future__ import annotations

from collections import Counter
from typing import Any

from .global_execution_governor import global_execution_governor_for
from .scheduler_policy import RequestSchedulerSettings


def _disabled_global_payload() -> dict[str, object]:
    return {
        "enabled": False,
        "max_running": None,
        "queue_capacity": None,
        "inflight": None,
        "queued": None,
        "fairness": None,
        "runtimes": [],
        "workloads": [],
    }


async def scheduler_evidence_payload(application: Any) -> dict[str, object]:
    settings = getattr(
        application.state,
        "request_scheduler_settings",
        RequestSchedulerSettings(),
    )
    payload: dict[str, object] = {
        "policy": settings.to_public_dict(),
        "global": _disabled_global_payload(),
        "runtimes": [],
    }
    manager = getattr(application.state, "runtime_manager", None)
    governor = getattr(application.state, "global_execution_governor", None)
    if governor is None and manager is not None:
        governor = global_execution_governor_for(manager)
    if governor is not None:
        payload["global"] = governor.snapshot().to_public_dict()

    if not settings.runtime_queue_enabled:
        return payload

    registry = getattr(application.state, "runtime_gate_registry", None)
    if manager is None or registry is None:
        return payload

    runtimes: list[dict[str, object]] = []
    for runtime in manager.list():
        gate = registry.gate_for(runtime)
        snapshot = await gate.snapshot()
        states = Counter(
            str(item.get("state"))
            for item in snapshot.requests
            if item.get("state") is not None
        )
        queued_by_workload = Counter(
            str(item.get("workload_class"))
            for item in snapshot.requests
            if item.get("state") == "queued" and item.get("workload_class") is not None
        )
        running_by_workload = Counter(
            str(item.get("workload_class"))
            for item in snapshot.requests
            if item.get("state") == "running" and item.get("workload_class") is not None
        )
        runtimes.append(
            {
                "runtime_key": runtime.key,
                "model_id": runtime.model_id,
                "state": runtime.state.value,
                "max_running": snapshot.max_running,
                "inflight": snapshot.inflight,
                "queue_capacity": snapshot.capacity,
                "queued": states.get("queued", 0),
                "admitted": states.get("admitted", 0),
                "running_bookkeeping": states.get("running", 0),
                "queued_by_workload": dict(sorted(queued_by_workload.items())),
                "running_by_workload": dict(sorted(running_by_workload.items())),
                "terminal_bookkeeping": sum(
                    states.get(name, 0)
                    for name in ("completed", "cancelled", "expired", "rejected")
                ),
            }
        )
    payload["runtimes"] = sorted(runtimes, key=lambda item: str(item["runtime_key"]))
    return payload
