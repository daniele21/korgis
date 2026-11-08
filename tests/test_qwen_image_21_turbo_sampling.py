from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse

import pytest

from local_llm_server.image_sampling_presets import (
    QWEN_IMAGE_21_TURBO_V1,
    image_sigma_digest,
    resolve_image_sigma_schedule,
)
from local_llm_server.stable_diffusion_cpp_image_engine import (
    StableDiffusionCppImageEngine,
)


def _resolve(**overrides):
    params = {
        "steps": 8,
        "guidance_scale": 1.0,
        "sampling_method": "euler",
        "scheduler": None,
    }
    params.update(overrides)
    return resolve_image_sigma_schedule("qwen-image-2.1-turbo-v1", **params)


def test_turbo_preset_has_exact_eight_steps_and_stable_digest():
    assert _resolve() == QWEN_IMAGE_21_TURBO_V1
    assert len(QWEN_IMAGE_21_TURBO_V1) == 9
    assert QWEN_IMAGE_21_TURBO_V1[-1] == 0.0
    assert len(image_sigma_digest(QWEN_IMAGE_21_TURBO_V1)) == 64
    assert resolve_image_sigma_schedule(
        None, steps=20, guidance_scale=6.0,
        sampling_method="euler", scheduler="discrete"
    ) is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"steps": 7},
        {"steps": 9},
        {"guidance_scale": 0.0},
        {"guidance_scale": 6.0},
        {"sampling_method": "euler_a"},
        {"scheduler": "discrete"},
    ],
)
def test_turbo_rejects_noncanonical_generation(overrides):
    with pytest.raises(ValueError, match="requires|override"):
        _resolve(**overrides)


def test_turbo_rejects_unknown_preset():
    with pytest.raises(ValueError, match="Unknown image sigma schedule"):
        resolve_image_sigma_schedule(
            "unexpected", steps=8, guidance_scale=1,
            sampling_method="euler", scheduler=None,
        )


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.body).encode("utf-8")


class FakeProcess:
    def __init__(self, command, *, name, logger):
        self.command = command
        self.closed = False

    def start(self):
        pass

    def wait_ready(self, check_ready, *, timeout):
        assert check_ready()

    def close(self):
        self.closed = True


def _engine(tmp_path, calls, *, verify=False, tamper_hash=False):
    paths = {
        "diffusion_model": tmp_path / "diffusion" / "diffusion.gguf",
        "text_encoder": tmp_path / "encoder" / "encoder.safetensors",
        "vae": tmp_path / "vae" / "vae.safetensors",
    }
    for path in paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"tiny-test-weight")
    sha = hashlib.sha256(b"tiny-test-weight").hexdigest()
    specs = {
        name: {
            "url": "https://example.invalid/no-download",
            "sha256": "0" * 64 if tamper_hash else sha,
        }
        for name in paths
    }
    binary = tmp_path / "sd-server"
    binary.write_text("#!/bin/sh\nprintf 'stable-diffusion.cpp version: 1.2.3'\n")
    binary.chmod(0o755)

    def urlopen(request, *, timeout):
        path = urlparse(request.full_url).path
        body = json.loads(request.data) if request.data is not None else {}
        calls.append((request.method, path, body))
        if path == "/sdcpp/v1/capabilities":
            return FakeResponse({"supported_modes": ["img_gen"]})
        if path == "/sdcpp/v1/img_gen":
            return FakeResponse({
                "id": "t1", "status": "queued",
                "poll_url": "/sdcpp/v1/jobs/t1",
            })
        if path == "/sdcpp/v1/jobs/t1":
            return FakeResponse({
                "id": "t1", "status": "completed",
                "result": {
                    "output_format": "png",
                    "images": [{"b64_json": base64.b64encode(b"png").decode()}],
                },
            })
        raise AssertionError(path)

    cfg = {
        "model_artifacts": {key: str(p) for key, p in paths.items()},
        "artifact_specs": specs,
        "sd_server_bin": str(binary),
        "no_download": True,
        "startup_timeout": 60,
        "image_sigma_schedule": "qwen-image-2.1-turbo-v1",
        "image_verify_existing_artifacts": verify,
        "image_num_inference_steps": 8,
        "image_guidance_scale": 1.0,
        "image_sampling_method": "euler",
    }
    return StableDiffusionCppImageEngine(
        cfg, process_factory=FakeProcess,
        urlopen=urlopen, sleeper=lambda _: None,
    )


def test_sd_server_receives_exact_turbo_sigmas_and_reports_effective_params(tmp_path):
    calls = []
    engine = _engine(tmp_path, calls, verify=True)
    try:
        result = engine.generate_image({
            "prompt": "A red cube on white",
            "width": 1024,
            "height": 1024,
            "seed": 42,
        })
        native = next(body for _, path, body in calls if path == "/sdcpp/v1/img_gen")
        assert native["sample_params"] == {
            "sample_method": "euler",
            "sample_steps": 8,
            "guidance": {"txt_cfg": 1.0},
            "custom_sigmas": list(QWEN_IMAGE_21_TURBO_V1),
        }
        assert result.data == b"png"
        assert result.seed == 42
        assert result.metadata["custom_sigmas"] == list(QWEN_IMAGE_21_TURBO_V1)
        assert result.metadata["sigma_schedule"] == "qwen-image-2.1-turbo-v1"
        assert result.metadata["sigma_schedule_sha256"] == image_sigma_digest(
            QWEN_IMAGE_21_TURBO_V1
        )
    finally:
        engine.close()
    assert engine.process.closed


def test_invalid_turbo_override_fails_before_native_submission(tmp_path):
    calls = []
    engine = _engine(tmp_path, calls)
    try:
        with pytest.raises(ValueError, match="exactly 8"):
            engine.generate_image({"prompt": "cat", "num_inference_steps": 9})
        assert not any(path == "/sdcpp/v1/img_gen" for _, path, _ in calls)
    finally:
        engine.close()


def test_existing_turbo_file_hash_mismatch_fails_before_start(tmp_path):
    with pytest.raises((RuntimeError, ValueError), match="SHA-256|checksum|hash"):
        _engine(tmp_path, [], verify=True, tamper_hash=True)
