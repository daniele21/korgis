"""One input reference must use the existing pinned async native job path."""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse

import pytest

from local_llm_server.stable_diffusion_cpp_image_engine import StableDiffusionCppImageEngine


class Response:
    def __init__(self, obj):
        self.obj = obj

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return json.dumps(self.obj).encode("utf-8")


class FakeProcess:
    instances = []

    def __init__(self, command, *, name, logger):
        self.command = command
        self.started = False
        self.closed = False
        self.__class__.instances.append(self)

    def start(self):
        self.started = True

    def wait_ready(self, readiness, *, timeout):
        assert readiness()

    def close(self):
        self.closed = True


def fixture_config(tmp_path: Path, *, projector_present: bool = True):
    artifacts = {}
    for name, basename in (
        ("diffusion_model", "diff.gguf"),
        ("text_encoder", "text.gguf"),
        ("vae", "vae.safetensors"),
    ):
        p = tmp_path / name / basename
        p.parent.mkdir(parents=True)
        p.write_bytes(b"weights")
        artifacts[name] = str(p)
    projector = tmp_path / "text_encoder" / "mmproj-BF16.gguf"
    content = b"vision-test-weights"
    if projector_present:
        projector.write_bytes(content)
    binary = tmp_path / "sd-server"
    binary.write_bytes(b"#!/bin/sh\n")
    binary.chmod(0o755)
    return {
        "model_artifacts": artifacts,
        "artifact_specs": {
            role: {"url": "https://example.invalid/" + role}
            for role in artifacts
        },
        "image_editing_enabled": True,
        "image_editing_projector_path": str(projector),
        "image_editing_projector_spec": {
            "repo": "unsloth/Qwen3-VL-8B-Instruct-GGUF",
            "revision": "174e4a339d8aceef41c87fda9f732c042d49e50b",
            "filename": "mmproj-BF16.gguf",
            "sha256": hashlib.sha256(content).hexdigest(),
        },
        "sd_server_bin": str(binary),
        "startup_timeout": 600,
        "no_download": True,
        "image_num_inference_steps": 8,
        "image_guidance_scale": 1.0,
        "image_sampling_method": "euler",
        "image_sigma_schedule": "qwen-image-2.1-turbo-v1",
    }


def test_pinned_async_native_edit_receives_only_one_reference(tmp_path):
    cfg = fixture_config(tmp_path)
    requests = []
    image_data = b"image-placeholder"
    image_b64 = base64.b64encode(image_data).decode("ascii")

    def urlopen(request, *, timeout):
        path = urlparse(request.full_url).path
        body = json.loads(request.data) if request.data else None
        requests.append((path, body))
        if path == "/sdcpp/v1/capabilities":
            return Response({"supported_modes": ["img_gen"]})
        if path == "/sdcpp/v1/img_gen":
            return Response({"id": "edit-1", "poll_url": "/sdcpp/v1/jobs/edit-1"})
        if path == "/sdcpp/v1/jobs/edit-1":
            return Response({
                "status": "completed", "started": 1, "completed": 3,
                "result": {"images": [{"b64_json": image_b64}], "output_format": "png"},
            })
        raise AssertionError(path)

    engine = StableDiffusionCppImageEngine(
        cfg, process_factory=FakeProcess, urlopen=urlopen,
    )
    assert "--llm_vision" in engine.process.command
    assert "vision_projector" in engine.artifact_provenance
    source = "data:image/png;base64," + base64.b64encode(b"source").decode()
    result = engine.generate_image({
        "prompt": "Change mug color", "width": 512, "height": 512,
        "seed": 42, "num_inference_steps": 8, "ref_images": [source],
    })
    assert result.data == image_data
    assert result.metadata["image_editing"] is True
    assert "source" not in json.dumps(result.metadata)
    payload = next(body for path, body in requests if path == "/sdcpp/v1/img_gen")
    assert payload["ref_images"] == [source]
    assert len(payload["sample_params"]["custom_sigmas"]) == 9
    assert payload["sample_params"]["sample_steps"] == 8
    assert payload["sample_params"]["guidance"]["txt_cfg"] == 1.0
    engine.close()


def test_missing_projector_is_detected_before_native_process_starts(tmp_path):
    cfg = fixture_config(tmp_path, projector_present=False)
    FakeProcess.instances.clear()
    with pytest.raises((FileNotFoundError, RuntimeError)):
        StableDiffusionCppImageEngine(cfg, process_factory=FakeProcess)
    assert FakeProcess.instances == []


def test_reference_is_not_accepted_by_text_only_runtime(tmp_path):
    cfg = fixture_config(tmp_path)
    cfg["image_editing_enabled"] = False
    cfg["image_editing_projector_spec"] = None
    cfg["image_editing_projector_path"] = None
    def urlopen(request, *, timeout):
        if urlparse(request.full_url).path == "/sdcpp/v1/capabilities":
            return Response({"supported_modes": ["img_gen"]})
        raise AssertionError("no native edit should be submitted")
    engine = StableDiffusionCppImageEngine(
        cfg, process_factory=FakeProcess, urlopen=urlopen,
    )
    with pytest.raises(ValueError, match="disabled"):
        engine.generate_image({
            "prompt": "Edit", "ref_images": ["data:image/png;base64,c291cmNl"]
        })
    engine.close()
