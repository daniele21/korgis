from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from local_llm_server.mflux_image_engine import MFluxImageEngine


class _Image:
    size = (768, 512)

    def save(self, buffer, *, format: str):
        buffer.write(f"{format}:mflux-image".encode())


class _Model:
    bits = 8

    def __init__(self) -> None:
        self.calls = []

    def generate_image(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            image=_Image(),
            quantization=8,
            generation_time=12.5,
        )


def _write_component(path: Path, *, quantization: str = "8") -> None:
    path.mkdir(parents=True)
    shard = "0.safetensors"
    (path / shard).write_bytes(b"weights")
    (path / "model.safetensors.index.json").write_text(
        json.dumps(
            {
                "metadata": {
                    "quantization_level": quantization,
                    "mflux_version": "0.20.0",
                },
                "weight_map": {"layer.weight": shard},
            }
        ),
        encoding="utf-8",
    )


def _write_snapshot(path: Path) -> None:
    for component in ("transformer", "text_encoder", "vae"):
        _write_component(path / component)
    processor = path / "processor"
    processor.mkdir()
    (processor / "tokenizer_config.json").write_text("{}", encoding="utf-8")


def test_mflux_engine_uses_prequantized_model_and_maps_generation_options(tmp_path: Path):
    model_path = tmp_path / "qwen-q8"
    _write_snapshot(model_path)
    model = _Model()
    loader_calls = []

    def loader(reference: str):
        loader_calls.append(reference)
        return model

    engine = MFluxImageEngine(
        {
            "model_path": str(model_path),
            "no_download": True,
            "image_width": 1024,
            "image_height": 1024,
            "image_num_inference_steps": 40,
            "image_guidance_scale": 1.0,
            "image_output_format": "png",
            "image_quantization_bits": 8,
        },
        model_loader=loader,
        seed_factory=lambda: 999,
    )

    result = engine.generate_image(
        {
            "prompt": "A precise product photograph",
            "width": 768,
            "height": 512,
            "seed": 42,
            "num_inference_steps": 12,
            "guidance_scale": 1.5,
        }
    )

    assert loader_calls == [str(model_path)]
    assert model.calls == [
        {
            "seed": 42,
            "prompt": "A precise product photograph",
            "num_inference_steps": 12,
            "width": 768,
            "height": 512,
            "guidance": 1.5,
        }
    ]
    assert result.data == b"PNG:mflux-image"
    assert result.mime_type == "image/png"
    assert (result.width, result.height) == (768, 512)
    assert result.seed == 42
    assert result.metadata["runtime"] == "mflux"
    assert result.metadata["quantization_bits"] == 8
    assert result.metadata["generation_time_seconds"] == 12.5


def test_mflux_engine_records_generated_seed_when_request_omits_it(tmp_path: Path):
    model_path = tmp_path / "qwen-q8"
    _write_snapshot(model_path)
    model = _Model()
    engine = MFluxImageEngine(
        {"model_path": str(model_path), "no_download": True},
        model_loader=lambda _reference: model,
        seed_factory=lambda: 123456,
    )

    result = engine.generate_image({"prompt": "A blue sphere"})

    assert result.seed == 123456
    assert model.calls[0]["seed"] == 123456


def test_mflux_engine_rejects_invalid_dimensions_before_model_call(tmp_path: Path):
    model_path = tmp_path / "qwen-q8"
    _write_snapshot(model_path)
    model = _Model()
    engine = MFluxImageEngine(
        {"model_path": str(model_path), "no_download": True},
        model_loader=lambda _reference: model,
    )

    with pytest.raises(ValueError, match="multiples of 32"):
        engine.generate_image(
            {
                "prompt": "A blue sphere",
                "width": 1000,
                "height": 1024,
            }
        )

    assert model.calls == []


def test_mflux_engine_rejects_chat(tmp_path: Path):
    model_path = tmp_path / "qwen-q8"
    _write_snapshot(model_path)
    engine = MFluxImageEngine(
        {"model_path": str(model_path), "no_download": True},
        model_loader=lambda _reference: _Model(),
    )

    with pytest.raises(RuntimeError, match="does not support chat"):
        engine.complete({})


def test_mflux_engine_fails_when_loaded_bits_disagree_with_config(tmp_path: Path):
    model_path = tmp_path / "qwen-q8"
    _write_snapshot(model_path)
    model = _Model()
    model.bits = 4

    with pytest.raises(RuntimeError, match="loaded Q4"):
        MFluxImageEngine(
            {
                "model_path": str(model_path),
                "no_download": True,
                "image_quantization_bits": 8,
            },
            model_loader=lambda _reference: model,
        )
