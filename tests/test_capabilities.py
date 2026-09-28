from __future__ import annotations

from local_llm_server.core.capabilities import (
    CapabilityFeature,
    Modality,
    ThinkingMode,
    descriptor_from_registry_entry,
    effective_thinking_mode,
)
from local_llm_server.core.contracts import GenerationOptions, InferenceRequest, TaskType


def test_text_legacy_entry_maps_to_text_tasks_without_audio_claims():
    descriptor = descriptor_from_registry_entry(
        {"modalities": ["text"], "thinking_mode": "none"}
    )

    assert TaskType.CHAT in descriptor.tasks
    assert TaskType.STRUCTURED_GENERATION in descriptor.tasks
    assert TaskType.TRANSCRIPTION not in descriptor.tasks
    assert descriptor.input_modalities == frozenset({Modality.TEXT})
    assert CapabilityFeature.STRUCTURED_OUTPUT in descriptor.features
    assert descriptor.thinking_mode is ThinkingMode.NONE


def test_empty_legacy_modalities_migrate_conservatively_to_text_only():
    descriptor = descriptor_from_registry_entry(
        {
            "modalities": [],
            "backend": "llama_server",
            "thinking_mode": "switchable",
        }
    )

    assert descriptor.tasks == frozenset({TaskType.CHAT, TaskType.STRUCTURED_GENERATION})
    assert descriptor.input_modalities == frozenset({Modality.TEXT})
    assert descriptor.output_modalities == frozenset({Modality.TEXT})
    assert CapabilityFeature.STREAMING in descriptor.features
    assert CapabilityFeature.STRUCTURED_OUTPUT in descriptor.features
    assert CapabilityFeature.THINKING in descriptor.features
    assert descriptor.thinking_mode is ThinkingMode.SWITCHABLE


def test_explicit_empty_input_modalities_remain_invalid():
    try:
        descriptor_from_registry_entry(
            {
                "tasks": ["chat"],
                "input_modalities": [],
                "output_modalities": ["text"],
            }
        )
    except ValueError as exc:
        assert "input_modalities must be a non-empty collection" in str(exc)
    else:
        raise AssertionError("expected explicit empty input modalities to fail")


def test_image_legacy_entry_maps_to_vision_language():
    descriptor = descriptor_from_registry_entry(
        {"modalities": ["text", "image"], "multimodal": True}
    )

    assert TaskType.VISION_LANGUAGE in descriptor.tasks
    assert Modality.IMAGE in descriptor.input_modalities


def test_audio_modality_does_not_imply_first_class_transcription():
    descriptor = descriptor_from_registry_entry(
        {"modalities": ["text", "audio"], "multimodal": True}
    )

    assert TaskType.TRANSCRIPTION not in descriptor.tasks


def test_explicit_transcription_requires_audio_input():
    try:
        descriptor_from_registry_entry(
            {
                "tasks": ["transcription"],
                "input_modalities": ["text"],
                "output_modalities": ["text"],
            }
        )
    except ValueError as exc:
        assert "requires audio input" in str(exc)
    else:
        raise AssertionError("expected invalid transcription capability to fail")


def test_descriptor_rejects_streaming_when_not_declared():
    descriptor = descriptor_from_registry_entry(
        {
            "tasks": ["chat"],
            "input_modalities": ["text"],
            "output_modalities": ["text"],
            "features": ["structured_output"],
        }
    )
    request = InferenceRequest(
        task=TaskType.CHAT,
        model="demo",
        messages=({"role": "user", "content": "hello"},),
        stream=True,
    )

    assert descriptor.supports(request) is False


def test_thinking_request_requires_thinking_feature():
    descriptor = descriptor_from_registry_entry(
        {"modalities": ["text"], "thinking_mode": "none"}
    )
    request = InferenceRequest(
        task=TaskType.CHAT,
        model="demo",
        messages=({"role": "user", "content": "hello"},),
        generation=GenerationOptions(enable_thinking=True),
    )

    assert descriptor.supports(request) is False


def test_always_thinking_rejects_request_level_disable():
    descriptor = descriptor_from_registry_entry(
        {"modalities": ["text"], "thinking_mode": "always"}
    )
    request = InferenceRequest(
        task=TaskType.CHAT,
        model="demo",
        messages=({"role": "user", "content": "hello"},),
        generation=GenerationOptions(enable_thinking=False),
    )

    assert descriptor.thinking_mode is ThinkingMode.ALWAYS
    assert descriptor.supports(request) is False


def test_switchable_mode_requires_backend_with_proven_request_control():
    assert effective_thinking_mode(
        {"backend": "llama_server", "thinking_mode": "switchable"}
    ) is ThinkingMode.SWITCHABLE
    assert effective_thinking_mode(
        {"backend": "llama_cpp", "thinking_mode": "switchable"}
    ) is ThinkingMode.SWITCHABLE
    assert effective_thinking_mode(
        {"backend": "gguf", "thinking_mode": "switchable"}
    ) is ThinkingMode.SWITCHABLE
    assert effective_thinking_mode(
        {
            "backend": "unknown_backend",
            "thinking_mode": "switchable",
            "enable_thinking": True,
        }
    ) is ThinkingMode.ALWAYS


def test_llama_cpp_switchable_descriptor_is_not_tied_to_runtime_default():
    descriptor = descriptor_from_registry_entry(
        {
            "backend": "llama_cpp",
            "modalities": ["text"],
            "thinking_mode": "switchable",
            "params": {"enable_thinking": True},
        }
    )

    assert descriptor.thinking_mode is ThinkingMode.SWITCHABLE
    assert CapabilityFeature.THINKING in descriptor.features


def test_descriptor_serialization_is_stable_and_public_safe():
    descriptor = descriptor_from_registry_entry(
        {
            "modalities": ["text", "image"],
            "backend": "llama_server",
            "thinking_mode": "switchable",
        }
    )

    assert descriptor.to_dict() == {
        "tasks": ["chat", "structured_generation", "vision_language"],
        "input_modalities": ["image", "text"],
        "output_modalities": ["text"],
        "features": ["streaming", "structured_output", "thinking"],
        "thinking_mode": "switchable",
    }


def test_image_generation_descriptor_allows_image_output_without_text_output():
    descriptor = descriptor_from_registry_entry(
        {
            "backend": "diffusers_image",
            "tasks": ["image_generation"],
            "input_modalities": ["text"],
            "output_modalities": ["image"],
            "thinking_mode": "none",
        }
    )

    request = InferenceRequest(
        task=TaskType.IMAGE_GENERATION,
        model="qwen-image-2.1",
        input_text="A red cube on a white background",
    )
    chat_request = InferenceRequest(
        task=TaskType.CHAT,
        model="qwen-image-2.1",
        messages=({"role": "user", "content": "hello"},),
    )

    assert descriptor.output_modalities == frozenset({Modality.IMAGE})
    assert descriptor.features == frozenset()
    assert descriptor.supports(request) is True
    assert descriptor.supports(chat_request) is False


def test_image_generation_requires_text_input_and_image_output():
    invalid_entries = (
        {
            "tasks": ["image_generation"],
            "input_modalities": ["image"],
            "output_modalities": ["image"],
        },
        {
            "tasks": ["image_generation"],
            "input_modalities": ["text"],
            "output_modalities": ["text"],
        },
    )

    for entry in invalid_entries:
        try:
            descriptor_from_registry_entry(entry)
        except ValueError as exc:
            assert "image_generation requires" in str(exc)
        else:
            raise AssertionError("invalid image generation capability must fail")
