"""Non-mutating ApplicationProfile resource planning on canonical resource evidence.

This is a snapshot-based planning aid, NOT a reservation or a device-memory proof.
It cannot authorize model activation. Missing evidence cannot become a "fits" claim.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from .core.application_profile import (
    ApplicationProfile,
    ResidencyIntent,
    validate_application_profile,
)
from .memory_envelope import resident_memory_envelope
from .resource_manager import ResourceManager, ReservationState
from .resource_policy import ResourcePolicySettings

_GIB = 1024 ** 3


def plan_application_profile(
    profile: ApplicationProfile,
    *,
    registry_models: Mapping[str, Mapping[str, Any]],
    settings: ResourcePolicySettings,
    resource_manager: ResourceManager | None,
    resident_runtime_reservations: Mapping[str, str | None],
    config_for_model: Callable[[str], Mapping[str, Any]],
) -> dict[str, Any]:
    """Estimate additional intended residency without changing any runtime.

    resident_runtime_reservations maps exact resident model keys to the
    reservation IDs owned by their runtimes. Values are never inferred from
    the model name; missing/partial ledger attribution makes results uncertain.
    config_for_model must resolve effective configurations locally without
    downloads or backend construction (the product uses build_config).
    """
    validated = validate_application_profile(profile, registry_models)

    snapshot = resource_manager.snapshot() if resource_manager is not None else ()
    reservation_ids = {reservation.reservation_id for reservation in snapshot}
    accounted_bytes = sum(item.accounted_bytes for item in snapshot)
    usable = settings.budget.usable_bytes
    current_keys = set(resident_runtime_reservations)
    unattributed = [
        key for key, reservation_id in sorted(resident_runtime_reservations.items())
        if resource_manager is not None
        and (reservation_id is None or reservation_id not in reservation_ids)
    ]
    unknown_evidence = bool(unattributed)
    additional_lower_bound = 0
    prospective_additional: int | None = 0
    models: list[dict[str, Any]] = []
    requested_models: dict[str, dict[str, Any]] = {}
    for name, role in profile.roles.items():
        selected = requested_models.setdefault(role.model, {
            "role_names": [],
            "intents": set(),
        })
        selected["role_names"].append(name)
        selected["intents"].add(role.residency.value)

    for key, item in requested_models.items():
        is_resident = key in current_keys
        intended = bool(
            item["intents"].intersection({ResidencyIntent.WARM.value, ResidencyIntent.PINNED.value})
        )
        planned_load = intended and not is_resident
        cfg = config_for_model(key)
        if not isinstance(cfg, Mapping) or cfg.get("model") != key:
            raise ValueError("resource planner could not resolve the exact configured model")
        envelope = resident_memory_envelope(cfg)
        estimate = envelope.accounted_bytes
        if planned_load:
            if estimate is not None:
                additional_lower_bound += estimate
            if not envelope.complete:
                unknown_evidence = True
                prospective_additional = None
            elif prospective_additional is not None:
                prospective_additional += estimate or 0
        declared_size = registry_models[key].get("size_gb")
        artifact_size = None
        if declared_size is not None:
            try:
                size = float(declared_size)
                if size >= 0 and size < float("inf"):
                    artifact_size = int(size * _GIB)
            except (ValueError, TypeError):
                pass
        models.append({
            "model": key,
            "roles": item["role_names"],
            "already_resident": is_resident,
            "intended_active_residency": intended,
            "would_load": planned_load,
            "on_demand_deferred": not intended,
            "artifact_size_bytes": {
                "value": artifact_size, "source": "registry_declared" if artifact_size is not None else "unavailable",
            },
            "resident_envelope": envelope.as_dict(),
            "observed_resident_bytes": {"value": None, "source": "unavailable"},
            "observed_peak_bytes": {"value": None, "source": "unavailable"},
            "transient_request_bytes": {
                "value": cfg.get("resource_request_estimate_bytes"),
                "source": (
                    "configured" if cfg.get("resource_request_estimate_bytes") is not None
                    else "unavailable"
                ),
                "request_specific": False,
            },
        })

    lower_bound_total = accounted_bytes + additional_lower_bound
    if usable is not None and lower_bound_total > usable:
        decision, reason = "rejected", "known_lower_bound_exceeds_configured_budget"
    elif usable is None:
        decision, reason = "uncertain", "configured_budget_unavailable"
    elif resource_manager is None:
        decision, reason = "uncertain", "resource_ledger_unavailable"
    elif unknown_evidence:
        decision, reason = "uncertain", "incomplete_resident_or_ledger_evidence"
    else:
        decision, reason = "plausible", "complete_configured_residency_estimates"

    return {
        "schema_version": profile.schema_version,
        "application_id": profile.application_id,
        "profile_id": profile.profile_id,
        "validated_roles": validated["roles"],
        "decision": decision,
        "reason": reason,
        "mutated_runtime": False,
        "resource_claim": "configured_resident_snapshot_only_not_device_safety",
        "budget": {
            "limit_bytes": settings.memory_limit_bytes,
            "headroom_bytes": settings.headroom_bytes,
            "usable_bytes": usable,
            "source": "configured" if usable is not None else "unavailable",
        },
        "current": {
            "resident_models": sorted(current_keys),
            "accounted_bytes": accounted_bytes,
            "source": "configured_ledger" if resource_manager is not None else "unavailable",
            "unattributed_resident_models": unattributed,
            "committed_bytes": sum(
                x.accounted_bytes for x in snapshot if x.state is ReservationState.COMMITTED
            ),
            "reserved_bytes": sum(
                x.accounted_bytes for x in snapshot if x.state is ReservationState.RESERVED
            ),
        },
        "planned": {
            "additional_known_lower_bound_bytes": additional_lower_bound,
            "additional_estimated_bytes": prospective_additional,
            "combined_known_lower_bound_bytes": lower_bound_total,
            "combined_estimated_bytes": (
                accounted_bytes + prospective_additional
                if prospective_additional is not None else None
            ),
        },
        "models": models,
    }
