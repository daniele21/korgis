from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from local_llm_server.image_generation_engine import DiffusersImageEngine


class _DeviceAvailability:
    def __init__(self, available: bool) -> None:
        self._available = available

    def is_available(self) -> bool:
        return self._available


class _Generator:
    def __init__(self, device: str) -> None:
        self.device = device
        self.seed = None

    def manual_seed(self, seed: int):
        self.seed = seed
        return self


class _Torch:
    bfloat16 = object()
    float16 = object()
    float32 = object()
    backends = SimpleNamespace(mps=_DeviceAvailability(True))
    cuda = _DeviceAvailability(False)
    mps = SimpleNamespace(empty_cache=lambda: None)
    Generator = _Generator


class _Image:
    size = (768, 512)

    def save(self, buffer, *, format: str):
        buffer.write(f"{format}:image".encode())


class _Pipeline:
    def __init__(self) -> None:
        self.device = None
        self.calls = []

    def to(self, device: str):
        self.device = device
        return self

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(images=[_Image()])


def _write_diffusers_snapshot(path: Path) -> None:
    path.mkdir()
    (path / "model_index.json").write_text(
        json.dumps(
            {
                "_class_name": "QwenImage21Pipeline",
                "transformer": ["diffusers", "QwenImage21Transformer2DModel"],
                "vae": ["diffusers", "AutoencoderKLQwenImage21"],
            }
        ),
        encoding="utf-8",
    )
    for component in ("transformer", "vae"):
        component_path = path / component
        component_path.mkdir()
        (component_path / "model.safetensors").write_bytes(b"weights")


def test_diffusers_engine_generates_config_driven_image(tmp_path: Path):
    model = tmp_path / "qwen-image"
    _write_diffusers_snapshot(model)
    pipeline = _Pipeline()
    loader_calls = []

    def loader(model_ref, *, torch_dtype):
        loader_calls.append((model_ref, torch_dtype))
        return pipeline

    engine = DiffusersImageEngine(
        {
            "model_path": str(model),
            "no_download": True,
            "image_device": "auto",
            "image_dtype": "bfloat16",
            "image_width": 1024,
            "image_height": 1024,
            "image_num_inference_steps": 40,
            "image_output_format": "png",
        },
        pipeline_loader=loader,
        torch_module=_Torch,
    )

    result = engine.generate_image(
        {
            "prompt": "A red cube",
            "width": 768,
            "height": 512,
            "seed": 42,
            "num_inference_steps": 12,
        }
    )

    assert engine.device == "mps"
    assert pipeline.device == "mps"
    assert loader_calls == [(str(model), _Torch.bfloat16)]
    assert pipeline.calls[0]["prompt"] == "A red cube"
    assert pipeline.calls[0]["width"] == 768
    assert pipeline.calls[0]["height"] == 512
    assert pipeline.calls[0]["num_inference_steps"] == 12
    assert pipeline.calls[0]["generator"].device == "cpu"
    assert pipeline.calls[0]["generator"].seed == 42
    assert result.data == b"PNG:image"
    assert result.mime_type == "image/png"
    assert (result.width, result.height) == (768, 512)
    assert result.seed == 42


def test_diffusers_engine_rejects_chat_and_invalid_prompt(tmp_path: Path):
    model = tmp_path / "qwen-image"
    _write_diffusers_snapshot(model)
    engine = DiffusersImageEngine(
        {
            "model_path": str(model),
            "no_download": True,
            "image_device": "cpu",
            "image_dtype": "float32",
        },
        pipeline_loader=lambda *_args, **_kwargs: _Pipeline(),
        torch_module=_Torch,
    )

    with pytest.raises(RuntimeError, match="does not support chat"):
        engine.complete({})
    with pytest.raises(ValueError, match="non-empty prompt"):
        engine.generate_image({"prompt": ""})


def test_diffusers_engine_fails_closed_for_unknown_device(tmp_path: Path):
    model = tmp_path / "qwen-image"
    _write_diffusers_snapshot(model)

    with pytest.raises(ValueError, match="image_device"):
        DiffusersImageEngine(
            {
                "model_path": str(model),
                "no_download": True,
                "image_device": "tpu",
                "image_dtype": "float32",
            },
            pipeline_loader=lambda *_args, **_kwargs: _Pipeline(),
            torch_module=_Torch,
        )
