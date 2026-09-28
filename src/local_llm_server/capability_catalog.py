"""Capability-aware projection of model registry entries.

This module keeps registry/API presentation independent from FastAPI. Consumers
can serialize the returned descriptor without loading or invoking a backend.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .core.capabilities import CapabilityDescriptor, descriptor_from_registry_entry
from .core.generation_domains import generation_parameter_domains_from_registry_entry


_CAPABILITY_FIELDS = {
    "tasks",
    "input_modalities",
    "output_modalities",
    "features",
}


def capability_for_registry_entry(entry: Mapping[str, Any]) -> CapabilityDescriptor:
    """Resolve explicit capability metadata or conservative legacy fallback."""
    return descriptor_from_registry_entry(entry)


def validate_registry_capability_entry(entry: Mapping[str, Any]) -> None:
    """Validate capability declarations without backend execution."""
    descriptor_from_registry_entry(entry)
    generation_parameter_domains_from_registry_entry(entry)


def capability_catalog_item(key: str, entry: Mapping[str, Any]) -> dict[str, Any]:
    descriptor = capability_for_registry_entry(entry)
    domains = generation_parameter_domains_from_registry_entry(entry)
    return {
        "key": key,
        "model_id": str(entry.get("model_id") or key),
        "capabilities": descriptor.to_dict(),
        "capability_source": (
            "explicit"
            if any(field in entry for field in _CAPABILITY_FIELDS)
            else "legacy_conservative"
        ),
        "generation_parameter_domains": [domain.to_dict() for domain in domains],
    }


def project_capability_catalog(
    models: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    return [
        capability_catalog_item(str(key), entry)
        for key, entry in sorted(models.items(), key=lambda item: str(item[0]))
    ]
