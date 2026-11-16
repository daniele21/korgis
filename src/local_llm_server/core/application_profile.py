"""Experimental M8 ApplicationProfile schema and capability validation.

No loading, worker construction, resource reservations or network effects occur
on this path. Scheduling and residency are intents, not active policies.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping

from .capabilities import CapabilityFeature, descriptor_from_registry_entry
from .contracts import TaskType

_SCHEMA = "korgis-application-profile-v1"
_SAFE_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
_ALLOWED_PRIORITIES = frozenset({"realtime", "interactive", "background"})


class ApplicationProfileError(ValueError):
    """Invalid application intent or mismatch with the exact model registry."""


class ResidencyIntent(str, Enum):
    PINNED = "pinned"
    WARM = "warm"
    ON_DEMAND = "on_demand"


@dataclass(frozen=True, slots=True)
class ApplicationRole:
    model: str
    task: TaskType
    residency: ResidencyIntent
    priority: str = "interactive"
    idle_ttl_seconds: int | None = None

    def to_dict(self) -> dict[str, Any]:
        result = {
            "model": self.model,
            "task": self.task.value,
            "residency": self.residency.value,
            "priority": self.priority,
        }
        if self.idle_ttl_seconds is not None:
            result["idle_ttl_seconds"] = self.idle_ttl_seconds
        return result


@dataclass(frozen=True, slots=True)
class ApplicationProfile:
    application_id: str
    profile_id: str
    roles: Mapping[str, ApplicationRole]
    schema_version: str = _SCHEMA

    @classmethod
    def parse(cls, raw: Mapping[str, Any]) -> "ApplicationProfile":
        if not isinstance(raw, Mapping) or set(raw) != {
            "schema_version", "application_id", "profile_id", "roles"
        }:
            raise ApplicationProfileError("expected exact ApplicationProfile fields")
        if raw["schema_version"] != _SCHEMA:
            raise ApplicationProfileError("unsupported ApplicationProfile version")
        app_id, profile_id = raw["application_id"], raw["profile_id"]
        if not isinstance(app_id, str) or not _SAFE_ID.fullmatch(app_id):
            raise ApplicationProfileError("invalid application_id")
        if not isinstance(profile_id, str) or not _SAFE_ID.fullmatch(profile_id):
            raise ApplicationProfileError("invalid profile_id")
        raw_roles = raw["roles"]
        if not isinstance(raw_roles, Mapping) or not raw_roles or len(raw_roles) > 32:
            raise ApplicationProfileError("roles must contain 1 to 32 explicit entries")
        roles: dict[str, ApplicationRole] = {}
        for role_name, value in raw_roles.items():
            if not isinstance(role_name, str) or not _SAFE_ID.fullmatch(role_name):
                raise ApplicationProfileError("invalid role identifier")
            if not isinstance(value, Mapping):
                raise ApplicationProfileError("role must be an object")
            required = {"model", "task", "residency"}
            optional = {"priority", "idle_ttl_seconds"}
            if not required.issubset(value) or set(value) - required - optional:
                raise ApplicationProfileError("invalid role fields")
            model = value["model"]
            if not isinstance(model, str) or not model or model != model.strip() or len(model) > 256:
                raise ApplicationProfileError("role.model must be an exact model key")
            try:
                task = TaskType(value["task"])
            except (TypeError, ValueError):
                raise ApplicationProfileError("unknown role.task") from None
            try:
                residency = ResidencyIntent(value["residency"])
            except (TypeError, ValueError):
                raise ApplicationProfileError("unknown role.residency") from None
            priority = value.get("priority", "interactive")
            if not isinstance(priority, str) or priority not in _ALLOWED_PRIORITIES:
                raise ApplicationProfileError("unknown role.priority")
            ttl = value.get("idle_ttl_seconds")
            if ttl is not None:
                if type(ttl) is not int or not 1 <= ttl <= 86400 or residency is not ResidencyIntent.WARM:
                    raise ApplicationProfileError("idle TTL requires warm residency and 1..86400 seconds")
            roles[role_name] = ApplicationRole(model, task, residency, priority, ttl)
        return cls(application_id=app_id, profile_id=profile_id, roles=MappingProxyType(roles))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "application_id": self.application_id,
            "profile_id": self.profile_id,
            "roles": {name: role.to_dict() for name, role in self.roles.items()},
        }


def validate_application_profile(
    profile: ApplicationProfile,
    registry_models: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Resolve declared roles against the *same* task facts used by HTTP admission.

    The registry is injected for zero-resident/non-mutating validation.
    Never infer substitution, memory-fit or actual residency from this result.
    """
    if not isinstance(profile, ApplicationProfile):
        raise TypeError("profile must be parsed before validation")
    if not isinstance(registry_models, Mapping):
        raise ApplicationProfileError("model registry is unavailable")
    bindings = []
    for role_name, role in profile.roles.items():
        entry = registry_models.get(role.model)
        if not isinstance(entry, Mapping):
            raise ApplicationProfileError(f"role {role_name}: configured model not found")
        try:
            descriptor = descriptor_from_registry_entry(entry)
        except ValueError:
            raise ApplicationProfileError(f"role {role_name}: invalid model capability metadata") from None
        if role.task not in descriptor.tasks:
            raise ApplicationProfileError(f"role {role_name}: task unsupported by exact model")
        if (role.task is TaskType.STRUCTURED_GENERATION
                and CapabilityFeature.STRUCTURED_OUTPUT not in descriptor.features):
            raise ApplicationProfileError(f"role {role_name}: structured output not supported")
        bindings.append({
            "role": role_name,
            "model": role.model,
            "task": role.task.value,
            "residency_intent": role.residency.value,
            "priority_intent": role.priority,
            "capabilities": descriptor.to_dict(),
        })
    return {
        "schema_version": profile.schema_version,
        "application_id": profile.application_id,
        "profile_id": profile.profile_id,
        "valid": True,
        "roles": bindings,
        "mutated_runtime": False,
        "resource_fit": "unavailable",
    }
