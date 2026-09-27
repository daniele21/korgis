"""Backend-neutral model capability descriptors.

Capabilities describe proven product behavior, not theoretical backend potential.
Legacy registry fields are translated conservatively during migration.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from .contracts import InferenceRequest, TaskType


class Modality(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"


class CapabilityFeature(str, Enum):
    STREAMING = "streaming"
    STRUCTURED_OUTPUT = "structured_output"
    THINKING = "thinking"


class ThinkingMode(str, Enum):
    """Effective request-level thinking behavior for a concrete runtime path."""

    NONE = "none"
    SWITCHABLE = "switchable"
    ALWAYS = "always"


# These adapters have a request-level path for a thinking flag. llama_cpp/gguf
# uses only metadata-backed Jinja chat handlers; unsupported/custom handler
# combinations fail explicitly at the engine boundary instead of dropping the
# flag or mutating global model state.
_REQUEST_SWITCHABLE_BACKENDS = frozenset(
    {"llama_cpp", "gguf", "mlx", "llama_server", "mlx_vlm_server"}
)


@dataclass(frozen=True, slots=True)
class CapabilityDescriptor:
    tasks: frozenset[TaskType]
    input_modalities: frozenset[Modality]
    output_modalities: frozenset[Modality]
    features: frozenset[CapabilityFeature] = field(default_factory=frozenset)
    thinking_mode: ThinkingMode = ThinkingMode.NONE

    def supports(self, request: InferenceRequest) -> bool:
        if request.task not in self.tasks:
            return False
        required = _request_modalities(request)
        if not required.issubset(self.input_modalities):
            return False
        if request.stream and CapabilityFeature.STREAMING not in self.features:
            return False
        if request.task is TaskType.STRUCTURED_GENERATION and CapabilityFeature.STRUCTURED_OUTPUT not in self.features:
            return False

        requested_thinking = request.generation.enable_thinking
        if requested_thinking is True and self.thinking_mode is ThinkingMode.NONE:
            return False
        if requested_thinking is False and self.thinking_mode is ThinkingMode.ALWAYS:
            return False
        return True

    def to_dict(self) -> dict[str, object]:
        return {
            "tasks": sorted(task.value for task in self.tasks),
            "input_modalities": sorted(modality.value for modality in self.input_modalities),
            "output_modalities": sorted(modality.value for modality in self.output_modalities),
            "features": sorted(feature.value for feature in self.features),
            "thinking_mode": self.thinking_mode.value,
        }


def effective_thinking_mode(config: Mapping[str, Any]) -> ThinkingMode:
    """Resolve the thinking behavior the effective backend path can prove.

    ``thinking_mode`` in a registry entry describes the model/template intent,
    while this function describes the concrete request path. A model declared
    ``switchable`` is only exposed as such when its selected backend currently
    forwards a request-level control. Otherwise the path is fixed to its
    explicitly configured runtime default, so callers cannot be told an OFF/ON
    toggle exists when the adapter would silently discard it.
    """
    declared = _parse_thinking_mode(str(config.get("thinking_mode", "none")))
    if declared is not ThinkingMode.SWITCHABLE:
        return declared

    backend = str(config.get("backend") or "llama_cpp")
    if backend in _REQUEST_SWITCHABLE_BACKENDS:
        return ThinkingMode.SWITCHABLE

    configured = config.get("enable_thinking")
    if configured is None:
        params = config.get("params")
        if isinstance(params, Mapping):
            configured = params.get("enable_thinking")

    # For a backend without proven request-level control we deliberately expose
    # only the fixed configured state. Missing state is treated conservatively
    # as not proven rather than guessing that switching works.
    return ThinkingMode.ALWAYS if configured is True else ThinkingMode.NONE


def descriptor_from_registry_entry(entry: Mapping[str, Any]) -> CapabilityDescriptor:
    """Build a conservative descriptor from current and future registry fields."""
    legacy_modalities = _legacy_modalities(entry)
    explicit_tasks = entry.get("tasks")
    if explicit_tasks is not None:
        tasks = frozenset(_parse_task(value) for value in _as_string_list(explicit_tasks, "tasks"))
    else:
        # Legacy migration deliberately does not infer TRANSCRIPTION merely from
        # an audio modality: current audio-capable chat paths do not prove a
        # first-class ASR contract.
        legacy_modality_set = set(legacy_modalities)
        tasks_set = {TaskType.CHAT}
        if "text" in legacy_modality_set:
            tasks_set.add(TaskType.STRUCTURED_GENERATION)
        if "image" in legacy_modality_set:
            tasks_set.add(TaskType.VISION_LANGUAGE)
        tasks = frozenset(tasks_set)

    explicit_input_modalities = entry.get("input_modalities")
    input_values = (
        legacy_modalities
        if explicit_input_modalities is None
        else _as_string_list(explicit_input_modalities, "input_modalities")
    )
    input_modalities = frozenset(_parse_modality(value) for value in input_values)
    output_modalities = frozenset(
        _parse_modality(value)
        for value in _as_string_list(entry.get("output_modalities", ["text"]), "output_modalities")
    )

    thinking_mode = effective_thinking_mode(entry)
    features_set: set[CapabilityFeature] = set()
    explicit_features = entry.get("features")
    if explicit_features is not None:
        features_set = {
            _parse_feature(value)
            for value in _as_string_list(explicit_features, "features", allow_empty=True)
        }
    else:
        if tasks.intersection(
            {
                TaskType.CHAT,
                TaskType.STRUCTURED_GENERATION,
                TaskType.VISION_LANGUAGE,
            }
        ):
            features_set.add(CapabilityFeature.STREAMING)
        if TaskType.STRUCTURED_GENERATION in tasks:
            features_set.add(CapabilityFeature.STRUCTURED_OUTPUT)
        if thinking_mode is not ThinkingMode.NONE:
            features_set.add(CapabilityFeature.THINKING)

    descriptor = CapabilityDescriptor(
        tasks=tasks,
        input_modalities=input_modalities,
        output_modalities=output_modalities,
        features=frozenset(features_set),
        thinking_mode=thinking_mode,
    )
    validate_capability_descriptor(descriptor)
    return descriptor


def validate_capability_descriptor(descriptor: CapabilityDescriptor) -> None:
    if not descriptor.tasks:
        raise ValueError("capability descriptor must declare at least one task")
    if not descriptor.input_modalities:
        raise ValueError("capability descriptor must declare at least one input modality")
    if not descriptor.output_modalities:
        raise ValueError("capability descriptor must declare at least one output modality")
    text_output_tasks = {
        TaskType.CHAT,
        TaskType.STRUCTURED_GENERATION,
        TaskType.VISION_LANGUAGE,
        TaskType.TRANSCRIPTION,
    }
    if descriptor.tasks.intersection(text_output_tasks) and Modality.TEXT not in descriptor.output_modalities:
        raise ValueError("text-producing tasks require text output capability")
    if TaskType.IMAGE_GENERATION in descriptor.tasks:
        if Modality.TEXT not in descriptor.input_modalities:
            raise ValueError("image_generation requires text input capability")
        if Modality.IMAGE not in descriptor.output_modalities:
            raise ValueError("image_generation requires image output capability")
    if TaskType.VISION_LANGUAGE in descriptor.tasks and Modality.IMAGE not in descriptor.input_modalities:
        raise ValueError("vision_language requires image input capability")
    if TaskType.TRANSCRIPTION in descriptor.tasks and Modality.AUDIO not in descriptor.input_modalities:
        raise ValueError("transcription requires audio input capability")

    has_thinking_feature = CapabilityFeature.THINKING in descriptor.features
    if has_thinking_feature != (descriptor.thinking_mode is not ThinkingMode.NONE):
        raise ValueError("thinking feature and effective thinking_mode must agree")


def _request_modalities(request: InferenceRequest) -> frozenset[Modality]:
    modalities = {Modality.TEXT}
    for message in request.messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, Mapping):
                continue
            part_type = str(part.get("type") or "")
            if part_type in {"image", "image_url", "input_image"}:
                modalities.add(Modality.IMAGE)
            elif part_type in {"audio", "input_audio"}:
                modalities.add(Modality.AUDIO)
    return frozenset(modalities)


def _legacy_modalities(entry: Mapping[str, Any]) -> list[str]:
    """Normalize the historical empty/missing text-only sentinel conservatively.

    Older effective runtime configs could expose ``modalities=[]`` even though
    their proven behavior was ordinary text chat. Treat only that legacy shape
    as text-only. Explicit ``input_modalities`` remains strict and is validated
    separately, so an explicitly empty capability declaration still fails.
    """
    value = entry.get("modalities")
    if value is None or value == []:
        return ["text"]
    return _as_string_list(value, "modalities")


def _as_string_list(
    value: Any,
    field_name: str,
    *,
    allow_empty: bool = False,
) -> list[str]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        raise ValueError(f"{field_name} must be a collection")
    if not value and not allow_empty:
        raise ValueError(f"{field_name} must be a non-empty collection")
    return [str(item) for item in value]


def _parse_task(value: str) -> TaskType:
    try:
        return TaskType(value)
    except ValueError as exc:
        raise ValueError(f"unsupported task capability: {value}") from exc


def _parse_modality(value: str) -> Modality:
    try:
        return Modality(value)
    except ValueError as exc:
        raise ValueError(f"unsupported modality capability: {value}") from exc


def _parse_feature(value: str) -> CapabilityFeature:
    try:
        return CapabilityFeature(value)
    except ValueError as exc:
        raise ValueError(f"unsupported capability feature: {value}") from exc


def _parse_thinking_mode(value: str) -> ThinkingMode:
    try:
        return ThinkingMode(value)
    except ValueError as exc:
        raise ValueError(f"unsupported thinking_mode: {value}") from exc
