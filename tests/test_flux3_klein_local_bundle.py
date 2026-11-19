"""FLUX.3 Klein integration: local-only transformer, separate VAE/text encoder."""
from __future__ import annotations

from pathlib import Path

import pytest

from local_llm_server.config import build_config
from local_llm_server.model_sources import resolve_bundle_artifacts
from local_llm_server.stable_diffusion_cpp_compat import build_sd_server_command
from local_llm_server.stable_diffusion_cpp_image_engine import StableDiffusionCppImageEngine

FLUX = "flux-3-image-klein-gguf-q5km"
GIB = 1024**3


def _local_gguf(home: Path, *, valid: bool = True) -> Path:
    gguf = (
        home / ".lmstudio" / "models" / "unsloth"
        / "flux-3-image-klein-GGUF"
        / "flux-3-image-klein-Q5_K_M.gguf"
    )
    gguf.parent.mkdir(parents=True)
    with gguf.open("wb") as handle:
        handle.write(b"GGUF" if valid else b"NOPE")
        handle.truncate(3_550_000_000)
    return gguf


def test_flux3_registry_reuses_local_gguf_without_copy_or_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    local = _local_gguf(tmp_path)
    cfg = build_config(FLUX, no_download=True)
    assert cfg["backend"] == "stable_diffusion_cpp_image"
    assert cfg["model_source"] == "lmstudio"
    assert Path(cfg["model_artifacts"]["diffusion_model"]) == local.resolve()
    assert cfg["model_artifacts"]["text_encoder"].endswith(
        "text_encoder/Qwen3-VL-4B-Instruct-Q4_K_M.gguf"
    )
    assert cfg["model_artifacts"]["vae"].endswith("vae/ae.safetensors")
    assert cfg["model_downloaded"] is False  # two auxiliary files are missing
    assert cfg["image_num_inference_steps"] == 16
    assert cfg["image_max_inference_steps"] == 16
    assert cfg["image_guidance_scale"] == 1.0
    assert cfg["image_sigma_schedule"] is None  # never reuse Qwen Turbo sigmas
    assert cfg["resource_admission_profile"] == "disk_backed_image"
    assert cfg["resource_estimate_bytes"] == 9 * GIB
    assert cfg["license"] == "flux-kommunity-license"


def test_flux3_missing_lmstudio_transformer_is_not_silently_downloaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    cfg = build_config(FLUX, no_download=False)
    assert cfg["model_source"] == "lmstudio"
    engine = object.__new__(StableDiffusionCppImageEngine)
    engine.cfg = cfg
    engine.artifacts = {
        name: Path(value)
        for name, value in cfg["model_artifacts"].items()
    }
    with pytest.raises(FileNotFoundError, match="LM Studio transformer missing"):
        engine._ensure_artifacts(cfg["artifact_specs"])


def test_flux3_refuses_truncated_or_wrong_gguf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    local = _local_gguf(tmp_path, valid=False)
    cfg = build_config(FLUX, no_download=True)
    engine = object.__new__(StableDiffusionCppImageEngine)
    engine.cfg = cfg
    engine.artifacts = {
        name: Path(value)
        for name, value in cfg["model_artifacts"].items()
    }
    with pytest.raises(ValueError, match="GGUF header"):
        engine._ensure_artifacts(cfg["artifact_specs"])
    with local.open("wb") as handle:
        handle.write(b"GGUF")
    with pytest.raises(ValueError, match="incomplete"):
        engine._ensure_artifacts(cfg["artifact_specs"])


def test_flux3_native_command_has_three_matching_components_and_no_qwen_projector(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    _local_gguf(tmp_path)
    cfg = build_config(FLUX, no_download=True)
    command = build_sd_server_command(
        binary=tmp_path / "sd-server",
        artifacts=cfg["model_artifacts"],
        host="127.0.0.1", port=8093, cfg=cfg,
    )
    assert command[command.index("--diffusion-model") + 1].endswith(
        "flux-3-image-klein-Q5_K_M.gguf"
    )
    assert command[command.index("--llm") + 1].endswith(
        "Qwen3-VL-4B-Instruct-Q4_K_M.gguf"
    )
    assert command[command.index("--vae") + 1].endswith("ae.safetensors")
    assert "--llm_vision" not in command
    assert "--disable-prefetch" in command
    assert "diffusion=disk,te=cpu,vae=cpu" in command


def test_bundle_lmstudio_path_cannot_escape_user_model_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    spec = {
        "artifacts": {
            "diffusion_model": {
                "local_path": "diffusion/model.gguf",
                "lmstudio_relative_path": "../../outside.gguf",
                "lmstudio_required": True,
            },
        },
    }
    with pytest.raises(ValueError, match="must be relative"):
        resolve_bundle_artifacts("unsafe", spec, tmp_path / "managed")
    spec["artifacts"]["diffusion_model"]["lmstudio_relative_path"] = "/etc/passwd"
    with pytest.raises(ValueError, match="must be relative"):
        resolve_bundle_artifacts("unsafe", spec, tmp_path / "managed")
