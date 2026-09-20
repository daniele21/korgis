from __future__ import annotations

import pytest

from local_llm_server.registry import load_registry, validate_registry


def _registry(models, *, default_model="one", startup_models=None):
    return {
        "models_dir": "/tmp/models",
        "defaults": {},
        "models": models,
        "default_model": default_model,
        "startup_models": startup_models or [],
    }


def test_registry_validation_rejects_alias_collisions():
    registry = _registry({
        "one": {"filename": "one.gguf", "model_id": "shared"},
        "shared": {"filename": "two.gguf", "model_id": "two"},
    })

    with pytest.raises(ValueError, match="alias 'shared'"):
        validate_registry(registry)


def test_registry_validation_rejects_invalid_multimodal_llama_server():
    registry = _registry({
        "one": {
            "filename": "one.gguf",
            "backend": "llama_server",
            "multimodal": True,
            "modalities": ["text", "image"],
        },
    })

    with pytest.raises(ValueError, match="needs mmproj"):
        validate_registry(registry)


def test_registry_validation_rejects_invalid_runtime_parameters():
    registry = _registry({
        "one": {
            "filename": "one.gguf",
            "params": {"max_concurrent_requests": 0},
        },
    })

    with pytest.raises(ValueError, match="max_concurrent_requests"):
        validate_registry(registry)


def test_registry_validation_accepts_huggingface_vlm_model():
    validate_registry(_registry({
        "vision": {
            "model_id": "mlx-community/example",
            "backend": "mlx_vlm_server",
            "multimodal": True,
            "modalities": ["text", "image"],
            "params": {"max_concurrent_requests": 2},
        },
    }, default_model="vision"))


def test_registry_validation_rejects_unknown_thinking_mode():
    registry = _registry({
        "one": {
            "filename": "one.gguf",
            "thinking_mode": "sometimes",
        },
    })

    with pytest.raises(ValueError, match="thinking_mode"):
        validate_registry(registry)


def test_registry_validation_accepts_explicit_bounded_generation_domain():
    validate_registry(
        _registry(
            {
                "one": {
                    "filename": "one.gguf",
                    "generation_parameter_domains": {
                        "temperature": {
                            "kind": "float",
                            "minimum": 0.0,
                            "maximum": 0.8,
                            "step": 0.1,
                        }
                    },
                }
            }
        )
    )


def test_registry_validation_rejects_runtime_load_field_as_request_domain():
    registry = _registry(
        {
            "one": {
                "filename": "one.gguf",
                "generation_parameter_domains": {
                    "n_ubatch": {
                        "kind": "integer",
                        "minimum": 64,
                        "maximum": 512,
                    }
                },
            }
        }
    )

    with pytest.raises(ValueError, match="n_ubatch"):
        validate_registry(registry)



def test_builtin_registry_exposes_q4km_local_benchmark_models(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("HOME", str(tmp_path))
    registry = load_registry()

    expected = {
        "qwen3.5-4b-q4km": ("Qwen/Qwen3.5-4B", 2.71, "switchable"),
        "qwen3.5-9b-q4km": ("Qwen/Qwen3.5-9B", 5.63, "switchable"),
        "nemotron-nano-4b": ("nvidia/nemotron-3-nano-4b", 2.5, "switchable"),
        "minicpm3-4b-q4km": ("openbmb/MiniCPM3-4B", 2.47, "none"),
    }
    for key, (model_id, size_gb, thinking_mode) in expected.items():
        entry = registry["models"][key]
        assert entry["model_id"] == model_id
        assert entry["quantization"] == "Q4_K_M"
        assert entry["size_gb"] == size_gb
        assert entry["thinking_mode"] == thinking_mode

    for key in ("qwen3.5-4b-q4km", "qwen3.5-9b-q4km", "minicpm3-4b-q4km"):
        entry = registry["models"][key]
        assert entry["backend"] == "llama_server"
        assert entry["params"]["ctx_size"] == 8192
        assert entry["params"]["enable_thinking"] is False
        assert len(entry["sha256"]) == 64

    assert (
        registry["models"]["minicpm3-4b-q4km"]["sha256"]
        == "64913247e927414ecf47fd3e9ea8e3f0c9acae293f583dfa7e24b8872e20fa4c"
    )


def test_builtin_registry_exposes_tiny_qwen35_runtime_smoke_model(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("HOME", str(tmp_path))
    entry = load_registry()["models"]["qwen3.5-0.8b-smoke-q4"]

    assert entry["model_id"] == "Qwen/Qwen3.5-0.8B"
    assert entry["quantization"] == "Q4_0"
    assert entry["backend"] == "llama_server"
    assert entry["size_gb"] == 0.563
    assert entry["thinking_mode"] == "switchable"
    assert entry["params"]["ctx_size"] == 2048
    assert entry["params"]["enable_thinking"] is False
    assert entry["sha256"] == "57d1997790d1744fba5b40a7317df71ea5e2acee28c47e78f0cce39c0703f8cf"
