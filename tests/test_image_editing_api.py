"""Security, route compatibility and opt-in integration for one-image edits."""
from __future__ import annotations

import base64
import hashlib
import struct
import zlib

from fastapi import FastAPI
from fastapi.testclient import TestClient

from local_llm_server.config import build_config
from local_llm_server.image_editing_api import validate_png_reference
from local_llm_server.image_generation_api import install_image_generation_api
from local_llm_server.image_generation_common import GeneratedImage
from local_llm_server.runtime import ModelRuntimeManager
from local_llm_server.stable_diffusion_cpp_compat import build_sd_server_command

MODEL = "qwen-image-2.1-turbo-gguf-q4km"


def make_png(width: int = 512, height: int = 512) -> bytes:
    def chunk(name: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload)) + name + payload
            + struct.pack(">I", zlib.crc32(name + payload) & 0xFFFFFFFF)
        )
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IEND", b"")


class FakeImageEngine:
    backend = "stable_diffusion_cpp_image"

    def __init__(self) -> None:
        self.calls = []

    def generate_image(self, payload):
        self.calls.append(payload)
        return GeneratedImage(
            data=make_png(512, 512),
            mime_type="image/png",
            width=512,
            height=512,
            seed=payload.get("seed"),
            metadata={"native_job": "fake"},
        )

    def close(self):
        pass


def app_with_engine(*, enabled: bool = True):
    cfg = build_config(MODEL, image_editing_enabled=enabled)
    manager = ModelRuntimeManager(default_model=MODEL)
    engine = FakeImageEngine()
    manager.add(cfg, engine)
    app = FastAPI()
    app.state.runtime_manager = manager
    install_image_generation_api(app)
    return TestClient(app), engine


def test_editing_off_does_not_claim_image_input_or_load_projector():
    cfg = build_config(MODEL)
    assert cfg["image_editing_enabled"] is False
    assert cfg["input_modalities"] == ["text"]
    assert cfg["resource_estimate_bytes"] == 8 * 1024**3
    assert cfg["image_editing_projector_path"].endswith("text_encoder/mmproj-BF16.gguf")


def test_opt_in_uses_pinned_projector_and_extra_2g_budget():
    cfg = build_config(MODEL, image_editing_enabled=True)
    assert cfg["input_modalities"] == ["image", "text"]
    assert cfg["image_editing_extra_resident_bytes"] == 2 * 1024**3
    assert cfg["resource_estimate_bytes"] == 10 * 1024**3
    spec = cfg["image_editing_projector_spec"]
    assert spec["revision"] == "174e4a339d8aceef41c87fda9f732c042d49e50b"
    assert spec["sha256"] == "6516bb64bae1503a0fcd7ec9fa39655f8c481580be0a0a066397941d9761c9f4"


def test_projector_required_for_edit_opt_in_but_not_for_text_to_image(tmp_path):
    binary = tmp_path / "sd-server"
    artifacts = {
        "diffusion_model": tmp_path / "diffusion.gguf",
        "text_encoder": tmp_path / "encoder.gguf",
        "vae": tmp_path / "vae.safetensors",
    }
    base = {"image_editing_enabled": False}
    command = build_sd_server_command(
        binary=binary, artifacts=artifacts, host="127.0.0.1", port=8093, cfg=base,
    )
    assert "--llm_vision" not in command
    projector = tmp_path / "mmproj.gguf"
    config = {**base, "image_editing_enabled": True,
              "image_editing_projector_path": str(projector)}
    import pytest
    with pytest.raises(FileNotFoundError, match="vision projector"):
        build_sd_server_command(
            binary=binary, artifacts=artifacts, host="127.0.0.1", port=8093,
            cfg=config,
        )
    projector.write_bytes(b"test vision projector fixture")
    edited = build_sd_server_command(
        binary=binary, artifacts=artifacts, host="127.0.0.1", port=8093,
        cfg=config,
    )
    assert edited[edited.index("--llm_vision") + 1] == str(projector)


def test_one_png_edit_reuses_existing_engine_and_returns_only_generated_image():
    client, engine = app_with_engine()
    source = make_png()
    response = client.post(
        "/v1/images/edits",
        data={"model": MODEL, "prompt": "Change the wall color", "seed": "42",
              "num_inference_steps": "8"},
        files={"image": ("room.png", source, "image/png")},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert base64.b64decode(result["data"][0]["b64_json"]) == source
    assert result["korgis"]["source_sha256"] == hashlib.sha256(source).hexdigest()
    assert result["korgis"]["source_width"] == 512
    assert len(engine.calls) == 1
    payload = engine.calls[0]
    assert payload["seed"] == 42
    assert payload["num_inference_steps"] == 8
    assert payload["width"] == 512 and payload["height"] == 512
    assert payload["ref_images"] == [
        "data:image/png;base64," + base64.b64encode(source).decode("ascii")
    ]
    assert "ref_images" not in str(result)


def test_disabled_edit_returns_typed_failure_before_backend():
    client, engine = app_with_engine(enabled=False)
    response = client.post(
        "/v1/images/edits", data={"prompt": "Edit"},
        files={"image": ("a.png", make_png(), "image/png")},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "unsupported_task"
    assert engine.calls == []


def test_untrusted_input_checks_before_inference():
    client, engine = app_with_engine()
    cases = [
        (make_png(4096, 512), "image/png", 400),
        (make_png(512, 512) + b"x" * (8 * 1024 * 1024), "image/png", 400),
        (b"not really png", "image/png", 400),
        (make_png(), "image/jpeg", 415),
    ]
    for data, mime, code in cases:
        response = client.post(
            "/v1/images/edits", data={"prompt": "Modify"},
            files={"image": ("source.png", data, mime)},
        )
        assert response.status_code == code, response.text
    assert engine.calls == []


def test_size_step_and_n_limits_match_generation_policy():
    client, engine = app_with_engine()
    for params in (
        {"size": "4000x4000"},
        {"num_inference_steps": "9"},
        {"n": "2"},
    ):
        response = client.post(
            "/v1/images/edits", data={"prompt": "Edit", **params},
            files={"image": ("a.png", make_png(), "image/png")},
        )
        assert response.status_code == 400, response.text
    assert engine.calls == []


def test_png_header_validation_does_not_decode_untrusted_pixels():
    import pytest
    data = make_png(1024, 512)
    assert validate_png_reference(data) == (1024, 512)
    with pytest.raises(ValueError, match="Corrupt PNG"):
        validate_png_reference(data[:29] + b"AAAA" + data[33:])
    with pytest.raises(ValueError, match="pixel budget"):
        validate_png_reference(make_png(2048, 2048), max_pixels=512 * 512)

def test_editing_output_over_1mp_is_rejected_before_native_submission():
    client, engine = app_with_engine()
    response = client.post(
        "/v1/images/edits",
        data={"prompt": "Edit", "size": "2048x1024"},
        files={"image": ("a.png", make_png(), "image/png")},
    )
    assert response.status_code == 400
    assert engine.calls == []


def test_unaligned_output_is_rejected_before_native_submission():
    client, engine = app_with_engine()
    response = client.post(
        "/v1/images/edits",
        data={"prompt": "Edit", "size": "1000x1000"},
        files={"image": ("a.png", make_png(), "image/png")},
    )
    assert response.status_code == 400
    assert engine.calls == []
