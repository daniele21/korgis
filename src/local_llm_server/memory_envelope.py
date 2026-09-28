"""Deterministic configured memory envelopes for residency and active requests.

These estimates are policy inputs, not measurements. Missing evidence remains
explicitly unavailable rather than being converted to zero. Representative
hardware evidence is responsible for calibrating configured values against real
backend and unified-memory behavior.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .core.contracts import InferenceRequest

_GIB = 1024**3
_MIB = 1024**2


@dataclass(frozen=True, slots=True)
class MemoryComponent:
    name: str
    bytes: int | None
    source: str
    required: bool = True

    @property
    def available(self) -> bool:
        return self.bytes is not None

    def as_dict(self) -> dict[str, Any]:
        return {
            "bytes": self.bytes,
            "source": self.source,
            "required": self.required,
        }


@dataclass(frozen=True, slots=True)
class MemoryEnvelope:
    scope: str
    components: tuple[MemoryComponent, ...]
    override_total_bytes: int | None = None

    @property
    def accounted_bytes(self) -> int | None:
        if self.override_total_bytes is not None:
            return self.override_total_bytes
        available = [component.bytes for component in self.components if component.bytes is not None]
        if not available:
            return None
        return sum(available)

    @property
    def unavailable_components(self) -> tuple[str, ...]:
        if self.override_total_bytes is not None:
            return ()
        return tuple(
            component.name
            for component in self.components
            if component.required and component.bytes is None
        )

    @property
    def complete(self) -> bool:
        if self.override_total_bytes is not None:
            return True
        return self.accounted_bytes is not None and not self.unavailable_components

    def as_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "accounted_bytes": self.accounted_bytes,
            "complete": self.complete,
            "override_total_bytes": self.override_total_bytes,
            "unavailable_components": list(self.unavailable_components),
            "components": {
                component.name: component.as_dict()
                for component in self.components
            },
        }


def resident_memory_envelope(config: Mapping[str, Any]) -> MemoryEnvelope:
    """Build the configured resident-memory estimate for one runtime."""
    override = _non_negative_int(config.get("resource_estimate_bytes"))
    weights = _model_weights_component(config)
    backend_overhead = _configured_component(
        "backend_overhead",
        config.get("resource_backend_overhead_bytes"),
    )
    context_cache = _configured_component(
        "context_cache",
        config.get("resource_context_cache_bytes"),
    )
    prompt_cache = _prompt_cache_component(config)
    projector = _projector_component(config)
    safety_margin = _configured_component(
        "safety_margin",
        config.get("resource_safety_margin_bytes"),
    )
    return MemoryEnvelope(
        scope="resident",
        components=(
            weights,
            backend_overhead,
            context_cache,
            prompt_cache,
            projector,
            safety_margin,
        ),
        override_total_bytes=override,
    )


def request_memory_envelope(
    request: InferenceRequest,
    runtime_config: Mapping[str, Any],
) -> MemoryEnvelope:
    """Build the transient peak estimate held for one canonical inference request."""
    override = _non_negative_int(runtime_config.get("resource_request_estimate_bytes"))
    base = _request_base_component(runtime_config)
    input_component = _request_input_component(
        _canonical_payload_bytes(request),
        runtime_config,
    )

    per_output_token = _non_negative_int(
        runtime_config.get("resource_request_output_token_bytes")
    )
    if per_output_token is None:
        output = MemoryComponent(
            name="request_output_tokens",
            bytes=None,
            source="not_configured",
            required=False,
        )
    elif request.generation.max_tokens is None:
        output = MemoryComponent(
            name="request_output_tokens",
            bytes=None,
            source="max_tokens_unavailable",
            required=True,
        )
    else:
        output = MemoryComponent(
            name="request_output_tokens",
            bytes=per_output_token * max(0, int(request.generation.max_tokens)),
            source="configured_per_output_token",
            required=True,
        )

    return MemoryEnvelope(
        scope="transient",
        components=(base, input_component, output, _request_safety_component(runtime_config)),
        override_total_bytes=override,
    )


def transcription_memory_envelope(
    audio_size_bytes: int,
    runtime_config: Mapping[str, Any],
) -> MemoryEnvelope:
    """Build the transient execution estimate for one resident ASR request."""
    if audio_size_bytes < 0:
        raise ValueError("audio_size_bytes must be >= 0")
    return MemoryEnvelope(
        scope="transient",
        components=(
            _request_base_component(runtime_config),
            _request_input_component(audio_size_bytes, runtime_config),
            _request_safety_component(runtime_config),
        ),
        override_total_bytes=_non_negative_int(
            runtime_config.get("resource_request_estimate_bytes")
        ),
    )


def _model_weights_component(config: Mapping[str, Any]) -> MemoryComponent:
    explicit = _non_negative_int(config.get("resource_model_weights_bytes"))
    if explicit is not None:
        return MemoryComponent("model_weights", explicit, "configured")

    model_artifacts = config.get("model_artifacts")
    if isinstance(model_artifacts, Mapping) and model_artifacts:
        try:
            paths = [
                Path(str(value)).expanduser()
                for value in model_artifacts.values()
            ]
            if paths and all(path.is_file() for path in paths):
                return MemoryComponent(
                    "model_weights",
                    sum(path.stat().st_size for path in paths),
                    "artifact_bundle_file_size",
                )
        except OSError:
            pass

    size_gb = config.get("size_gb")
    if size_gb is not None:
        try:
            size_bytes = max(0, int(float(size_gb) * _GIB))
        except (TypeError, ValueError):
            size_bytes = None
        if size_bytes is not None:
            return MemoryComponent(
                "model_weights",
                size_bytes,
                "registry_artifact_size",
            )

    model_path = config.get("model_path")
    if model_path:
        try:
            path = Path(str(model_path)).expanduser()
            if path.is_file():
                return MemoryComponent("model_weights", path.stat().st_size, "artifact_file_size")
        except OSError:
            pass
    return MemoryComponent("model_weights", None, "unavailable")


def _prompt_cache_component(config: Mapping[str, Any]) -> MemoryComponent:
    explicit = _non_negative_int(config.get("resource_prompt_cache_bytes"))
    if explicit is not None:
        return MemoryComponent("prompt_cache", explicit, "configured")
    cache_mib = _non_negative_int(config.get("llama_server_cache_ram_mib"))
    if cache_mib is not None:
        return MemoryComponent("prompt_cache", cache_mib * _MIB, "llama_server_cache_ram")
    return MemoryComponent("prompt_cache", None, "not_configured", required=False)


def _projector_component(config: Mapping[str, Any]) -> MemoryComponent:
    explicit = _non_negative_int(config.get("resource_projector_bytes"))
    if explicit is not None:
        return MemoryComponent("projector", explicit, "configured")
    projector_path = config.get("mmproj_path")
    if not projector_path:
        return MemoryComponent("projector", None, "not_applicable", required=False)
    try:
        path = Path(str(projector_path)).expanduser()
        if path.is_file():
            return MemoryComponent("projector", path.stat().st_size, "artifact_file_size")
    except OSError:
        pass
    return MemoryComponent("projector", None, "unavailable", required=True)


def _request_base_component(config: Mapping[str, Any]) -> MemoryComponent:
    return _optional_configured_component(
        "request_base",
        config.get("resource_request_base_bytes"),
    )


def _request_input_component(
    input_bytes: int,
    config: Mapping[str, Any],
) -> MemoryComponent:
    multiplier = _non_negative_int(config.get("resource_request_input_byte_multiplier"))
    if multiplier is None:
        return MemoryComponent(
            name="request_input",
            bytes=None,
            source="not_configured",
            required=False,
        )
    return MemoryComponent(
        name="request_input",
        bytes=input_bytes * multiplier,
        source="configured_input_byte_multiplier",
        required=True,
    )


def _request_safety_component(config: Mapping[str, Any]) -> MemoryComponent:
    return _optional_configured_component(
        "request_safety_margin",
        config.get("resource_request_safety_margin_bytes"),
    )


def _configured_component(name: str, value: Any) -> MemoryComponent:
    parsed = _non_negative_int(value)
    if parsed is None:
        return MemoryComponent(name, None, "unavailable", required=True)
    return MemoryComponent(name, parsed, "configured", required=True)


def _optional_configured_component(name: str, value: Any) -> MemoryComponent:
    parsed = _non_negative_int(value)
    if parsed is None:
        return MemoryComponent(name, None, "not_configured", required=False)
    return MemoryComponent(name, parsed, "configured", required=True)


def _canonical_payload_bytes(request: InferenceRequest) -> int:
    payload = {
        "task": request.task.value,
        "model": request.model,
        "messages": list(request.messages),
        "input_text": request.input_text,
        "stream": request.stream,
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return len(encoded)


def _non_negative_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError("memory estimate values must be non-negative integers")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("memory estimate values must be non-negative integers") from exc
    if parsed < 0:
        raise ValueError("memory estimate values must be non-negative integers")
    return parsed
