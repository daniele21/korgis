from __future__ import annotations

import pytest

from local_llm_server.core import TaskType
from local_llm_server.image_generation_request import (
    ImageGenerationRequest,
    prepare_image_generation_request,
)


def _runtime_config() -> dict:
    return {
        "image_width": 1024,
        "image_height": 1024,
        "image_max_pixels": 1048576,
        "image_max_inference_steps": 50,
    }


def test_image_request_preparation_builds_canonical_and_backend_payload() -> None:
    prepared = prepare_image_generation_request(
        ImageGenerationRequest(
            model="image",
            prompt="  A red cube  ",
            size="512x768",
            seed=42,
            num_inference_steps=12,
            guidance_scale=1.0,
            output_format="png",
        ),
        runtime_key="image",
        runtime_config=_runtime_config(),
    )

    assert prepared.canonical.task is TaskType.IMAGE_GENERATION
    assert prepared.canonical.model == "image"
    assert prepared.canonical.input_text == "A red cube"
    assert prepared.canonical.image_generation.width == 512
    assert prepared.canonical.image_generation.height == 768
    assert prepared.canonical.image_generation.seed == 42
    assert prepared.backend_payload == {
        "prompt": "A red cube",
        "width": 512,
        "height": 768,
        "seed": 42,
        "num_inference_steps": 12,
        "guidance_scale": 1.0,
        "output_format": "png",
    }


def test_image_request_preparation_uses_runtime_defaults() -> None:
    prepared = prepare_image_generation_request(
        ImageGenerationRequest(prompt="A blue sphere"),
        runtime_key="image",
        runtime_config=_runtime_config(),
    )

    assert prepared.width == 1024
    assert prepared.height == 1024
    assert prepared.backend_payload == {
        "prompt": "A blue sphere",
        "width": 1024,
        "height": 1024,
    }


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            ImageGenerationRequest(prompt=" ", size="512x512"),
            "non-empty prompt",
        ),
        (
            ImageGenerationRequest(prompt="ok", size="bad"),
            "WIDTHxHEIGHT",
        ),
        (
            ImageGenerationRequest(prompt="ok", size="2048x2048"),
            "configured maximum",
        ),
        (
            ImageGenerationRequest(prompt="ok", num_inference_steps=51),
            "configured maximum 50",
        ),
    ],
)
def test_image_request_preparation_rejects_runtime_policy_violations(
    payload: ImageGenerationRequest,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        prepare_image_generation_request(
            payload,
            runtime_key="image",
            runtime_config=_runtime_config(),
        )
