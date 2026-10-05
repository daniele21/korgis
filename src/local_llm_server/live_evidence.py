"""Privacy-safe runtime evidence projection for product/API consumers."""
from __future__ import annotations

from typing import Any

from .metrics import InferenceMetrics
from .metrics_adapters import metrics_from_runtime_status
from .runtime_evidence import attached_runtime_identity
from .resource_telemetry import RequestResourceEvidence
from .transcription_metrics import latest_transcription_metrics


def record_runtime_metrics(runtime: Any, metrics: InferenceMetrics) -> InferenceMetrics:
    """Attach the latest canonical generation metrics when a producer measured them."""
    runtime.latest_inference_metrics = metrics
    return metrics


def latest_runtime_metrics(runtime: Any) -> InferenceMetrics:
    explicit = getattr(runtime, "latest_inference_metrics", None)
    if isinstance(explicit, InferenceMetrics):
        return explicit
    return metrics_from_runtime_status(runtime.snapshot_status())


def runtime_evidence_payload(runtime: Any) -> dict[str, object]:
    """Project source-backed runtime state without prompt/output/private paths."""
    status = runtime.snapshot_status()
    identity = attached_runtime_identity(runtime)
    metrics = latest_runtime_metrics(runtime)
    transcription = latest_transcription_metrics(runtime)
    resource_admission = status.get("resource_admission")
    request_resources = getattr(runtime, "latest_request_resource_evidence", None)

    task_metrics: dict[str, object] = {}
    if transcription is not None:
        task_metrics["transcription"] = transcription.to_public_dict()

    return {
        "runtime": {
            "key": runtime.key,
            "model_id": runtime.model_id,
            "backend": status.get("backend"),
            "state": status.get("state"),
            "active_requests": status.get("active_requests"),
            "max_concurrent_requests": status.get("max_concurrent_requests"),
            "loaded_at": status.get("loaded_at"),
        },
        "resource_admission": (
            dict(resource_admission) if isinstance(resource_admission, dict) else None
        ),
        "metrics": metrics.to_public_dict(),
        "request_resources": (
            request_resources.to_public_dict()
            if isinstance(request_resources, RequestResourceEvidence)
            else None
        ),
        "task_metrics": task_metrics,
        "identity": identity.to_public_dict() if identity is not None else None,
    }


def manager_evidence_payload(manager: Any) -> dict[str, object]:
    runtimes = manager.list()
    return {
        "configured_default_model": getattr(
            manager, "configured_default_model", manager.default_model
        ),
        "default_model": manager.default_model,
        "runtime_count": len(runtimes),
        "cold": len(runtimes) == 0,
        "runtimes": [runtime_evidence_payload(runtime) for runtime in runtimes],
    }
