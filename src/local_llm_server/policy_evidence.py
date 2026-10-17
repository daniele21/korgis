"""Privacy-safe product policy evidence for Settings/control-plane consumers."""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request

from .memory_envelope import resident_memory_envelope
from .resource_manager import ReservationKind


def policy_evidence_payload(application: Any) -> dict[str, object]:
    """Expose effective policy flags and bounded resource evidence without paths."""
    manager = getattr(application.state, "runtime_manager", None)
    runtimes = manager.list() if manager is not None else []
    resource_manager = getattr(manager, "resource_manager", None) if manager is not None else None
    reservations = resource_manager.snapshot() if resource_manager is not None else ()
    payload = {
        "canonical_request_policy_installed": bool(
            getattr(application.state, "canonical_request_policy_installed", False)
        ),
        "request_resource_admission_installed": bool(
            getattr(application.state, "request_resource_admission_installed", False)
        ),
        "remote_media_default": "blocked",
        "trust_remote_code_default": False,
        "resource_budget": {
            "limit_bytes": (
                resource_manager.budget.limit_bytes
                if resource_manager is not None
                else None
            ),
            "headroom_bytes": (
                resource_manager.budget.headroom_bytes
                if resource_manager is not None
                else None
            ),
            "usable_bytes": (
                resource_manager.budget.usable_bytes
                if resource_manager is not None
                else None
            ),
            "resident_accounted_bytes": sum(
                item.accounted_bytes
                for item in reservations
                if item.kind is ReservationKind.RESIDENT
            ),
            "transient_accounted_bytes": sum(
                item.accounted_bytes
                for item in reservations
                if item.kind is ReservationKind.TRANSIENT
            ),
        },
        "runtimes": [
            {
                "key": runtime.key,
                "model": runtime.model_id,
                "allow_remote_media": bool(runtime.cfg.get("allow_remote_media", False)),
                "trust_remote_code": bool(runtime.cfg.get("trust_remote_code", False)),
                "resident_memory_envelope": resident_memory_envelope(runtime.cfg).as_dict(),
            }
            for runtime in runtimes
        ],
    }
    if resource_manager is not None and resource_manager.host_protection_enabled:
        payload["host_memory"] = resource_manager.host_memory_status()
    return payload


def install_policy_evidence_api(application: FastAPI) -> FastAPI:
    """Install the read-only policy evidence route when admin API is enabled."""
    if getattr(application.state, "policy_evidence_api_installed", False):
        return application
    application.state.policy_evidence_api_installed = True

    settings = getattr(application.state, "settings", None)
    if not bool(getattr(settings, "enable_admin_api", False)):
        return application

    def get_policy_evidence(request: Request) -> dict[str, object]:
        return policy_evidence_payload(request.app)

    application.add_api_route(
        "/api/v1/policies",
        get_policy_evidence,
        methods=["GET"],
        tags=["Resources"],
        name="get_product_policy_evidence",
    )
    return application
