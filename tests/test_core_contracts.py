from __future__ import annotations

import pytest

from local_llm_server.core import (
    ErrorCode,
    ImageGenerationOptions,
    InferenceError,
    InferenceRequest,
    TaskType,
    chat_payload_to_inference_request,
)


def test_openai_chat_translates_to_chat_task():
    request = chat_payload_to_inference_request(
        {
            "model": "demo",
            "messages": [{"role": "user", "content": "hello"}],
            "temperature": 0.2,
            "max_tokens": 32,
            "stream": True,
        }
    )

    assert request.task is TaskType.CHAT
    assert request.model == "demo"
    assert request.input_text == "hello"
    assert request.generation.temperature == 0.2
    assert request.generation.max_tokens == 32
    assert request.stream is True


def test_legacy_input_translates_without_api_specific_types():
    request = chat_payload_to_inference_request(
        {"system_prompt": "be concise", "input": "hello"}
    )

    assert request.task is TaskType.CHAT
    assert request.messages == (
        {"role": "system", "content": "be concise"},
        {"role": "user", "content": "hello"},
    )


def test_image_message_translates_to_vision_language():
    request = chat_payload_to_inference_request(
        {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
                        {"type": "text", "text": "describe"},
                    ],
                }
            ]
        }
    )

    assert request.task is TaskType.VISION_LANGUAGE
    assert request.input_text == "describe"


def test_audio_message_translates_to_transcription():
    request = chat_payload_to_inference_request(
        {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_audio", "input_audio": {"data": "abc", "format": "wav"}},
                        {"type": "text", "text": "transcribe"},
                    ],
                }
            ]
        }
    )

    assert request.task is TaskType.TRANSCRIPTION


def test_json_response_format_translates_to_structured_generation():
    request = chat_payload_to_inference_request(
        {
            "messages": [{"role": "user", "content": "return json"}],
            "response_format": {"type": "json_object"},
        }
    )

    assert request.task is TaskType.STRUCTURED_GENERATION
    assert request.output.format == "json_object"


def test_missing_input_uses_typed_invalid_request_error():
    with pytest.raises(InferenceError) as exc_info:
        chat_payload_to_inference_request({})

    assert exc_info.value.code is ErrorCode.INVALID_REQUEST


def test_inference_error_allows_interpreter_traceback_state_updates():
    error = InferenceError(ErrorCode.INVALID_REQUEST, "invalid")

    # Python and generator-based context managers update these standard
    # Exception attributes while propagating failures. Freezing the dataclass
    # masks the original typed error during unwinding.
    error.__traceback__ = None
    error.__context__ = None

    assert str(error) == "invalid"


def test_image_generation_options_are_backend_neutral_and_typed():
    request = InferenceRequest(
        task=TaskType.IMAGE_GENERATION,
        model="qwen-image-2.1",
        input_text="A clean product photograph",
        image_generation=ImageGenerationOptions(
            width=1024,
            height=1024,
            num_inference_steps=40,
            guidance_scale=4.0,
            seed=42,
            output_format="png",
        ),
    )

    assert request.image_generation.width == 1024
    assert request.image_generation.height == 1024
    assert request.image_generation.num_inference_steps == 40
    assert request.image_generation.seed == 42
