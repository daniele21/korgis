from __future__ import annotations

import pytest

from local_llm_server.config import build_config
from local_llm_server.image_generation_request import (
    ImageGenerationRequest,
    prepare_image_generation_request,
)

TURBO = "qwen-image-2.1-turbo-gguf-q4km"


def test_turbo_resolves_pinned_backend_and_sampling_profile_without_download():
    cfg = build_config(TURBO)
    assert cfg["model"] == TURBO
    assert cfg["backend"] == "stable_diffusion_cpp_image"
    assert cfg["model_id"] == "unsloth/Qwen-Image-2.1-Turbo-GGUF"
    assert cfg["image_sigma_schedule"] == "qwen-image-2.1-turbo-v1"
    assert cfg["image_num_inference_steps"] == 8
    assert cfg["image_guidance_scale"] == 1.0
    assert cfg["image_verify_existing_artifacts"] is True
    assert cfg["image_offload_to_cpu"] is True
    assert cfg["sd_server_params_backend"] == "diffusion=disk,te=cpu,vae=cpu"
    assert set(cfg["model_artifacts"]) == {
        "diffusion_model", "text_encoder", "vae",
    }


def test_turbo_http_preflight_rejects_steps_above_runtime_cap():
    cfg = build_config(TURBO)
    prepared = prepare_image_generation_request(
        ImageGenerationRequest(
            model=TURBO,
            prompt="Small red cube",
            size="1024x1024",
            seed=42,
            num_inference_steps=8,
        ),
        runtime_key=TURBO,
        runtime_config=cfg,
    )
    assert prepared.backend_payload["seed"] == 42
    assert prepared.backend_payload["width"] == 1024
    assert prepared.backend_payload["height"] == 1024
    with pytest.raises(ValueError, match="maximum 8"):
        prepare_image_generation_request(
            ImageGenerationRequest(
                model=TURBO,
                prompt="Small red cube",
                num_inference_steps=9,
            ),
            runtime_key=TURBO,
            runtime_config=cfg,
        )


def test_standard_qwen_image_profile_has_no_turbo_sigma_schedule():
    cfg = build_config("qwen-image-2.1-gguf-q4km")
    assert cfg["image_sigma_schedule"] is None
    assert cfg["image_num_inference_steps"] == 20
