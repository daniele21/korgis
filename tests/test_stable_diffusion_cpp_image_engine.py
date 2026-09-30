from __future__ import annotations

import base64
import json
from pathlib import Path
from urllib.parse import urlparse

import pytest

from local_llm_server.stable_diffusion_cpp_image_engine import (
    StableDiffusionCppImageEngine,
)


class _Response:
    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


class _FakeProcess:
    instances = []

    def __init__(self, command, *, name, logger) -> None:
        self.command = list(command)
        self.name = name
        self.logger = logger
        self.started = False
        self.closed = False
        self.__class__.instances.append(self)

    def start(self) -> None:
        self.started = True

    def wait_ready(self, check_ready, *, timeout) -> None:
        assert timeout == 600
        assert check_ready() is True

    def close(self) -> None:
        self.closed = True


def _bundle(tmp_path: Path) -> tuple[dict[str, str], dict[str, dict]]:
    paths = {
        "diffusion_model": tmp_path / "diffusion" / "model.gguf",
        "text_encoder": tmp_path / "text_encoder" / "encoder.gguf",
        "vae": tmp_path / "vae" / "model.safetensors",
    }
    for path in paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"weights")
    specs = {
        name: {
            "url": f"https://example.invalid/{name}",
            "filename": path.name,
        }
        for name, path in paths.items()
    }
    return ({name: str(path) for name, path in paths.items()}, specs)


def _binary(tmp_path: Path) -> Path:
    path = tmp_path / "sd-server"
    path.write_text(
        "#!/bin/sh\necho 'stable-diffusion.cpp version: v1.2.3 test'\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | 0o111)
    return path


def test_sdcpp_engine_runs_native_job_and_preserves_q4_provenance(
    tmp_path: Path,
) -> None:
    _FakeProcess.instances.clear()
    artifacts, specs = _bundle(tmp_path)
    calls = []
    encoded = base64.b64encode(b"png-image").decode("ascii")

    def urlopen(request, *, timeout):
        path = urlparse(request.full_url).path
        body = (
            json.loads(request.data.decode("utf-8"))
            if request.data is not None
            else None
        )
        calls.append((request.method, path, body, timeout))
        if path == "/sdcpp/v1/capabilities":
            return _Response({"supported_modes": ["img_gen"]})
        if path == "/sdcpp/v1/img_gen":
            return _Response(
                {
                    "id": "job-1",
                    "status": "queued",
                    "poll_url": "/sdcpp/v1/jobs/job-1",
                }
            )
        if path == "/sdcpp/v1/jobs/job-1":
            return _Response(
                {
                    "id": "job-1",
                    "status": "completed",
                    "started": 10,
                    "completed": 12.5,
                    "result": {
                        "output_format": "png",
                        "images": [{"b64_json": encoded}],
                    },
                }
            )
        raise AssertionError(path)

    engine = StableDiffusionCppImageEngine(
        {
            "model_artifacts": artifacts,
            "artifact_specs": specs,
            "sd_server_bin": str(_binary(tmp_path)),
            "sd_server_port": 8093,
            "startup_timeout": 600,
            "timeout": 30,
            "image_width": 1024,
            "image_height": 1024,
            "image_num_inference_steps": 20,
            "image_guidance_scale": 6.0,
            "image_sampling_method": "euler",
            "image_output_format": "png",
            "image_diffusion_flash_attention": True,
            "image_offload_to_cpu": True,
            "quantization": "Q4_K_M",
            "no_download": True,
        },
        process_factory=_FakeProcess,
        urlopen=urlopen,
        seed_factory=lambda: 999,
        sleeper=lambda _seconds: None,
    )

    result = engine.generate_image(
        {
            "prompt": "A red cube",
            "width": 1024,
            "height": 1024,
            "seed": 42,
            "num_inference_steps": 16,
            "guidance_scale": 5.5,
            "sampling_method": "euler_a",
            "scheduler": "discrete",
            "output_format": "png",
        }
    )

    assert result.data == b"png-image"
    assert result.mime_type == "image/png"
    assert result.seed == 42
    assert result.metadata["runtime"] == "stable-diffusion.cpp"
    assert result.metadata["quantization"] == "Q4_K_M"
    assert result.metadata["artifacts"] == {
        "diffusion_model": {"filename": "model.gguf"},
        "text_encoder": {"filename": "encoder.gguf"},
        "vae": {"filename": "model.safetensors"},
    }
    assert all(
        "local_path" not in artifact
        for artifact in result.metadata["artifacts"].values()
    )
    assert result.metadata["sampling_method"] == "euler_a"
    assert result.metadata["scheduler"] == "discrete"
    assert result.metadata["num_inference_steps"] == 16
    assert result.metadata["guidance_scale"] == 5.5
    assert result.metadata["generation_time_seconds"] == 2.5

    submission = next(call for call in calls if call[1] == "/sdcpp/v1/img_gen")
    assert submission[2] == {
        "prompt": "A red cube",
        "width": 1024,
        "height": 1024,
        "seed": 42,
        "batch_count": 1,
        "sample_params": {
            "sample_method": "euler_a",
            "sample_steps": 16,
            "guidance": {"txt_cfg": 5.5},
            "scheduler": "discrete",
        },
        "output_format": "png",
    }

    process = _FakeProcess.instances[0]
    assert process.started is True
    assert "--diffusion-model" in process.command
    assert "--llm" in process.command
    assert "--vae" in process.command

    engine.close()
    assert process.closed is True


def test_sdcpp_engine_rejects_invalid_qwen_dimensions(tmp_path: Path) -> None:
    artifacts, specs = _bundle(tmp_path)

    def urlopen(request, *, timeout):
        return _Response({"supported_modes": ["img_gen"]})

    engine = StableDiffusionCppImageEngine(
        {
            "model_artifacts": artifacts,
            "artifact_specs": specs,
            "sd_server_bin": str(_binary(tmp_path)),
            "startup_timeout": 600,
            "no_download": True,
        },
        process_factory=_FakeProcess,
        urlopen=urlopen,
    )

    with pytest.raises(ValueError, match="multiples of 32"):
        engine.generate_image(
            {
                "prompt": "A cube",
                "width": 1000,
                "height": 1024,
            }
        )
