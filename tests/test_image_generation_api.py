from __future__ import annotations

import base64

from fastapi import FastAPI
from fastapi.testclient import TestClient

from local_llm_server.image_generation_api import install_image_generation_api
from local_llm_server.image_generation_engine import GeneratedImage
from local_llm_server.request_middleware import install_request_policy
from local_llm_server.runtime import ModelRuntimeManager


class _ImageEngine:
    backend = "diffusers_image"

    def __init__(self) -> None:
        self.calls = []

    def generate_image(self, payload):
        self.calls.append(payload)
        return GeneratedImage(
            data=b"generated-image",
            mime_type="image/png",
            width=512,
            height=512,
            seed=payload.get("seed"),
            metadata={
                "device": "cpu",
                "dtype": "float32",
                "num_inference_steps": payload.get("num_inference_steps"),
                "guidance_scale": payload.get("guidance_scale"),
            },
        )

    def close(self):
        pass


class _TextEngine:
    backend = "fake"

    def __init__(self) -> None:
        self.calls = 0

    def generate_image(self, payload):
        self.calls += 1
        raise AssertionError("text runtime must not reach image backend")

    def close(self):
        pass


def _image_cfg():
    return {
        "model": "qwen-image-2.1",
        "model_id": "Qwen/Qwen-Image-2.1",
        "model_path": "/models/qwen-image",
        "backend": "diffusers_image",
        "tasks": ["image_generation"],
        "input_modalities": ["text"],
        "output_modalities": ["image"],
        "thinking_mode": "none",
        "max_concurrent_requests": 1,
        "image_width": 512,
        "image_height": 512,
        "image_num_inference_steps": 20,
        "image_max_inference_steps": 50,
        "image_max_pixels": 1048576,
        "image_output_format": "png",
    }


def _text_cfg():
    return {
        "model": "text",
        "model_id": "org/text",
        "model_path": "/models/text",
        "backend": "fake",
        "modalities": ["text"],
        "thinking_mode": "none",
    }


def _client_with_runtimes():
    image_engine = _ImageEngine()
    text_engine = _TextEngine()
    manager = ModelRuntimeManager(default_model="qwen-image-2.1")
    manager.add(_image_cfg(), image_engine)
    manager.add(_text_cfg(), text_engine)
    app = FastAPI()
    app.state.runtime_manager = manager
    install_image_generation_api(app)
    return TestClient(app), image_engine, text_engine


def test_image_generation_route_returns_openai_compatible_base64():
    client, engine, _ = _client_with_runtimes()

    response = client.post(
        "/v1/images/generations",
        json={
            "model": "qwen-image-2.1",
            "prompt": "A red cube",
            "size": "512x512",
            "response_format": "b64_json",
            "seed": 42,
            "num_inference_steps": 12,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert base64.b64decode(payload["data"][0]["b64_json"]) == b"generated-image"
    assert payload["model"] == "Qwen/Qwen-Image-2.1"
    assert payload["korgis"]["runtime_key"] == "qwen-image-2.1"
    assert payload["korgis"]["backend"] == "diffusers_image"
    assert payload["korgis"]["width"] == 512
    assert payload["korgis"]["seed"] == 42
    assert engine.calls == [
        {
            "prompt": "A red cube",
            "width": 512,
            "height": 512,
            "seed": 42,
            "num_inference_steps": 12,
        }
    ]


def test_image_generation_route_rejects_text_runtime_before_backend():
    client, _, text_engine = _client_with_runtimes()

    response = client.post(
        "/v1/images/generations",
        json={
            "model": "text",
            "prompt": "A red cube",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "unsupported_task"
    assert text_engine.calls == 0


def test_image_generation_route_rejects_invalid_size():
    client, engine, _ = _client_with_runtimes()

    response = client.post(
        "/v1/images/generations",
        json={
            "model": "qwen-image-2.1",
            "prompt": "A red cube",
            "size": "bad-size",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_request"
    assert engine.calls == []


def test_image_generation_route_rejects_request_over_pixel_budget():
    client, engine, _ = _client_with_runtimes()

    response = client.post(
        "/v1/images/generations",
        json={
            "model": "qwen-image-2.1",
            "prompt": "A red cube",
            "size": "2048x2048",
        },
    )

    assert response.status_code == 400
    assert "configured maximum" in response.json()["detail"]["message"]
    assert engine.calls == []


def test_image_generation_route_rejects_excessive_steps():
    client, engine, _ = _client_with_runtimes()

    response = client.post(
        "/v1/images/generations",
        json={
            "model": "qwen-image-2.1",
            "prompt": "A red cube",
            "num_inference_steps": 51,
        },
    )

    assert response.status_code == 400
    assert "configured maximum 50" in response.json()["detail"]["message"]
    assert engine.calls == []


def test_image_policy_preserves_pydantic_422_for_malformed_body():
    image_engine = _ImageEngine()
    manager = ModelRuntimeManager(default_model="qwen-image-2.1")
    manager.add(_image_cfg(), image_engine)
    app = FastAPI()
    app.state.runtime_manager = manager
    install_image_generation_api(app)
    install_request_policy(app)

    response = TestClient(app).post(
        "/v1/images/generations",
        json={
            "model": "qwen-image-2.1",
            "prompt": "A red cube",
            "n": 2,
        },
    )

    assert response.status_code == 422
    assert image_engine.calls == []
