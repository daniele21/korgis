"""Administrative residency-policy API for pinning and explicit eviction."""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from .residency_eviction import (
    EvictionMode,
    EvictionPolicySettings,
    select_eviction_candidates,
)


class RuntimePinBody(BaseModel):
    model: str = Field(..., min_length=1)
    pinned: bool = True


class EvictionPolicyBody(BaseModel):
    mode: EvictionMode = EvictionMode.LRU
    limit: int = Field(default=1, ge=1, le=32)
    ttl_seconds: float | None = Field(default=None, ge=0)
    protect_resident_default: bool = True


def _eviction_settings(body: EvictionPolicyBody) -> EvictionPolicySettings:
    try:
        return EvictionPolicySettings(
            mode=body.mode,
            limit=body.limit,
            ttl_seconds=body.ttl_seconds,
            protect_resident_default=body.protect_resident_default,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _host_memory_sample(manager: Any) -> dict[str, int | None] | None:
    """Sample canonical observed host counters, never infer freed model bytes."""
    resource_manager = getattr(manager, "resource_manager", None)
    if resource_manager is None or not resource_manager.host_protection_enabled:
        return None
    status = resource_manager.host_memory_status()
    if status is None:
        return None

    def measured(field: str) -> int | None:
        value = status.get(field)
        return value if type(value) is int and value >= 0 else None

    return {
        "available_bytes": measured("available_bytes"),
        "swap_used_bytes": measured("swap_used_bytes"),
    }


def _host_memory_change(
    before: dict[str, int | None] | None,
    after: dict[str, int | None] | None,
) -> dict[str, object] | None:
    """Bounded *system-wide* correlation, not model-attributed reclamation."""
    if before is None and after is None:
        return None

    def delta(name: str) -> int | None:
        earlier = before.get(name) if before is not None else None
        later = after.get(name) if after is not None else None
        if earlier is None or later is None:
            return None
        return later - earlier

    return {
        "observation_kind": "immediate_system_wide_before_after",
        "attribution": "unattributed_external_activity_possible",
        "reclamation_claim": False,
        "before": before,
        "after": after,
        "available_memory_delta_bytes": delta("available_bytes"),
        "swap_used_delta_bytes": delta("swap_used_bytes"),
    }


def install_residency_api(application: FastAPI) -> FastAPI:
    """Install privacy-safe residency policy routes when admin API is enabled."""
    if getattr(application.state, "residency_api_installed", False):
        return application
    application.state.residency_api_installed = True

    settings = getattr(application.state, "settings", None)
    if not bool(getattr(settings, "enable_admin_api", False)):
        return application

    def snapshot(request: Request) -> dict[str, Any]:
        manager = request.app.state.runtime_manager
        producer = getattr(manager, "residency_policy_snapshot", None)
        if producer is None:
            raise HTTPException(
                status_code=501,
                detail="Runtime manager does not expose residency policy state.",
            )
        payload = producer()
        return {"supported": True, **payload}

    def set_pin(body: RuntimePinBody, request: Request) -> dict[str, Any]:
        manager = request.app.state.runtime_manager
        setter = getattr(manager, "set_pinned", None)
        producer = getattr(manager, "residency_policy_snapshot", None)
        if setter is None or producer is None:
            raise HTTPException(
                status_code=501,
                detail="Runtime manager does not support residency pinning.",
            )
        try:
            runtime = setter(body.model, body.pinned)
        except LookupError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {
            "ok": True,
            "model": runtime.model_id,
            "key": runtime.key,
            "pinned": body.pinned,
            "residency": producer(),
        }

    def preview_eviction(body: EvictionPolicyBody, request: Request) -> dict[str, Any]:
        manager = request.app.state.runtime_manager
        producer = getattr(manager, "residency_policy_snapshot", None)
        if producer is None:
            raise HTTPException(
                status_code=501,
                detail="Runtime manager does not expose residency policy state.",
            )
        policy = _eviction_settings(body)
        candidates = select_eviction_candidates(producer(), policy)
        return {
            "mode": policy.mode.value,
            "protect_resident_default": policy.protect_resident_default,
            "candidate_count": len(candidates),
            "candidates": [candidate.to_public_dict() for candidate in candidates],
            "automatic": False,
            "reclamation_claim": False,
        }

    def evict(body: EvictionPolicyBody, request: Request) -> dict[str, Any]:
        manager = request.app.state.runtime_manager
        producer = getattr(manager, "residency_policy_snapshot", None)
        unload = getattr(manager, "unload", None)
        if producer is None or unload is None:
            raise HTTPException(
                status_code=501,
                detail="Runtime manager does not support explicit residency eviction.",
            )

        policy = _eviction_settings(body)
        candidates = select_eviction_candidates(producer(), policy)
        # Sample once before and once after the complete explicit unload batch.
        # These host-wide counters cannot establish which model freed bytes,
        # or prove actual allocator/memory pressure recovery.
        before = _host_memory_sample(manager)
        evicted: list[dict[str, str]] = []
        skipped: list[dict[str, str]] = []
        for candidate in candidates:
            try:
                runtime = unload(candidate.key)
            except (LookupError, RuntimeError) as exc:
                # A lease/state change between selection and unload is expected
                # concurrency, not a reason to force eviction.
                skipped.append({
                    "key": candidate.key,
                    "model": candidate.model,
                    "reason": type(exc).__name__,
                })
                continue
            evicted.append({
                "key": runtime.key,
                "model": runtime.model_id,
                "reason": candidate.reason.value,
            })

        after = _host_memory_sample(manager)
        return {
            "ok": len(skipped) == 0,
            "mode": policy.mode.value,
            "host_memory_observation": _host_memory_change(before, after),
            "automatic": False,
            "reclamation_claim": False,
            "evicted": evicted,
            "skipped": skipped,
            "residency": producer(),
        }

    application.add_api_route(
        "/api/v1/residency",
        snapshot,
        methods=["GET"],
        tags=["Resources"],
        name="get_residency_policy",
    )
    application.add_api_route(
        "/api/v1/residency/pin",
        set_pin,
        methods=["POST"],
        tags=["Resources"],
        name="set_runtime_pin",
    )
    application.add_api_route(
        "/api/v1/residency/eviction/preview",
        preview_eviction,
        methods=["POST"],
        tags=["Resources"],
        name="preview_runtime_eviction",
    )
    application.add_api_route(
        "/api/v1/residency/evict",
        evict,
        methods=["POST"],
        tags=["Resources"],
        name="evict_runtimes_explicitly",
    )
    return application
