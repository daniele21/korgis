from __future__ import annotations

from pathlib import Path

import pytest

from local_llm_server.config import build_config
from local_llm_server import list_models


def _write_complete_vlm(path: Path) -> None:
    path.mkdir(parents=True)
    (path / "config.json").write_text("{}")
    (path / "tokenizer_config.json").write_text("{}")
    (path / "preprocessor_config.json").write_text("{}")
    (path / "model.safetensors").write_bytes(b"weights")


def test_text_only_builtin_defaults_to_explicit_text_modality(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    cfg = build_config(model="nemotron-nano-4b-q8")

    assert cfg["multimodal"] is False
    assert cfg["modalities"] == ["text"]


def test_mutable_fallbacks_are_not_shared_between_runtime_configs(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    first = build_config(model="nemotron-nano-4b-q8")
    second = build_config(model="nemotron-nano-4b-q8")
    first["modalities"].append("image")

    assert second["modalities"] == ["text"]


def test_multimodal_config_prefers_complete_lmstudio_model(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    local_model = (
        tmp_path / ".lmstudio" / "models" / "lmstudio-community"
        / "Qwen3-VL-4B-Instruct-MLX-4bit"
    )
    _write_complete_vlm(local_model)

    cfg = build_config(model="qwen3-vl-4b")

    assert cfg["backend"] == "mlx_vlm_server"
    assert cfg["multimodal"] is True
    assert "image" in cfg["modalities"]
    assert cfg["max_kv_size"] == 8192
    assert cfg["startup_timeout"] == 300
    assert cfg["thinking_mode"] == "none"
    assert cfg["max_concurrent_requests"] == 2
    assert cfg["model_path"] == str(local_model)
    assert cfg["mmproj_path"] is None


def test_multimodal_config_falls_back_to_huggingface(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    cfg = build_config(model="qwen3-vl-4b")

    assert cfg["model_path"] == "mlx-community/Qwen3-VL-4B-Instruct-4bit"


def test_multimodal_config_uses_complete_huggingface_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    snapshot = tmp_path / "hf-snapshot"
    _write_complete_vlm(snapshot)
    monkeypatch.setattr(
        "huggingface_hub.snapshot_download",
        lambda **kwargs: str(snapshot) if kwargs.get("local_files_only") else None,
    )

    cfg = build_config(model="qwen3-vl-4b")

    assert cfg["model_path"] == str(snapshot)
    assert cfg["model_source"] == "huggingface"
    assert cfg["model_downloaded"] is True


def test_qwen_instruct_rejects_thinking_override(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    with pytest.raises(ValueError, match="does not support thinking"):
        build_config(model="qwen3-vl-4b", enable_thinking=True)


def test_environment_backend_overrides_registry(monkeypatch):
    monkeypatch.setenv("LOCAL_LLM_BACKEND", "llama_server")

    cfg = build_config(model="qwen3-vl-4b")

    assert cfg["backend"] == "llama_server"


def test_float_environment_values_are_parsed(monkeypatch):
    monkeypatch.setenv("LOCAL_LLM_DEFAULT_TEMPERATURE", "0.25")

    cfg = build_config(model="qwen3-vl-4b")

    assert cfg["default_temperature"] == 0.25
    assert isinstance(cfg["default_temperature"], float)


def test_remote_code_is_disabled_by_default(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    cfg = build_config(model="qwen3-vl-4b")

    assert cfg["trust_remote_code"] is False
    assert cfg["tokenizer_config"] == {"trust_remote_code": False}


def test_remote_code_requires_explicit_opt_in(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("LOCAL_LLM_TRUST_REMOTE_CODE", "true")

    cfg = build_config(model="qwen3-vl-4b")

    assert cfg["trust_remote_code"] is True
    assert cfg["tokenizer_config"] == {"trust_remote_code": True}


def test_remote_media_is_disabled_by_default(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    cfg = build_config(model="qwen3-vl-4b")

    assert cfg["allow_remote_media"] is False


def test_lmstudio_model_can_be_listed_as_downloaded(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    local_model = (
        tmp_path / ".lmstudio" / "models" / "lmstudio-community"
        / "Qwen3-VL-4B-Instruct-MLX-4bit"
    )
    _write_complete_vlm(local_model)

    qwen = next(model for model in list_models() if model["key"] == "qwen3-vl-4b")

    assert qwen["path"] == str(local_model)
    assert qwen["downloaded"] is True
    assert qwen["source"] == "lmstudio"


def test_qwen_image_config_is_explicit_and_image_only(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    cfg = build_config(model="qwen-image-2.1")

    assert cfg["backend"] == "diffusers_image"
    assert cfg["tasks"] == ["image_generation"]
    assert cfg["input_modalities"] == ["text"]
    assert cfg["output_modalities"] == ["image"]
    assert cfg["image_device"] == "auto"
    assert cfg["image_dtype"] == "bfloat16"
    assert cfg["image_width"] == 1024
    assert cfg["image_height"] == 1024
    assert cfg["image_num_inference_steps"] == 40
    assert cfg["image_max_inference_steps"] == 100
    assert cfg["image_max_pixels"] == 4194304
    assert cfg["image_output_format"] == "png"
    assert cfg["max_concurrent_requests"] == 1
    assert cfg["model_path"] == "Qwen/Qwen-Image-2.1"


def test_qwen_image_mflux_q8_config_preserves_quantization_and_resource_evidence(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    cfg = build_config(model="qwen-image-2.1-mflux-q8")

    assert cfg["backend"] == "mflux_image"
    assert cfg["model_id"] == "mflux-community/qwen-image-2-1-mflux-q8"
    assert cfg["model_path"] == "mflux-community/qwen-image-2-1-mflux-q8"
    assert cfg["quantization"] == "Q8"
    assert cfg["tasks"] == ["image_generation"]
    assert cfg["input_modalities"] == ["text"]
    assert cfg["output_modalities"] == ["image"]
    assert cfg["image_width"] == 1024
    assert cfg["image_height"] == 1024
    assert cfg["image_num_inference_steps"] == 40
    assert cfg["image_quantization_bits"] == 8
    assert cfg["image_guidance_scale"] == 1.0
    assert cfg["resource_model_weights_bytes"] == 24025558302
    assert cfg["max_concurrent_requests"] == 1



def test_qwen_image_gguf_q4km_config_resolves_managed_bundle(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    cfg = build_config(model="qwen-image-2.1-gguf-q4km")

    bundle_root = tmp_path / ".local-llm" / "models" / "qwen-image-2.1-gguf-q4km"
    assert cfg["backend"] == "stable_diffusion_cpp_image"
    assert cfg["model_id"] == "unsloth/Qwen-Image-2.1-GGUF"
    assert cfg["model_path"] == str(bundle_root.resolve())
    assert cfg["model_downloaded"] is False
    assert cfg["quantization"] == "Q4_K_M"
    assert cfg["image_width"] == 1024
    assert cfg["image_height"] == 1024
    assert cfg["image_num_inference_steps"] == 20
    assert cfg["image_guidance_scale"] == 6.0
    assert cfg["image_sampling_method"] == "euler"
    assert cfg["image_diffusion_flash_attention"] is True
    assert cfg["image_offload_to_cpu"] is True
    assert cfg["sd_server_port"] == 8093
    assert cfg["model_artifacts"] == {
        "diffusion_model": str(
            bundle_root / "diffusion" / "qwen-image-2.1-Q4_K_M.gguf"
        ),
        "text_encoder": str(
            bundle_root
            / "text_encoder"
            / "Qwen3-VL-8B-Instruct-UD-Q4_K_XL.gguf"
        ),
        "vae": str(
            bundle_root / "vae" / "qwen_image_2.1_vae_bf16.safetensors"
        ),
    }
