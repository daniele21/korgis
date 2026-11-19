"""FLUX.3 Klein integration: local-only transformer, separate VAE/text encoder."""
from __future__ import annotations

from pathlib import Path

import pytest

from local_llm_server.config import build_config
from local_llm_server.model_sources import artifact_download_url, resolve_bundle_artifacts
from local_llm_server.registry import load_registry
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
        "text_encoder/Qwen3VL-4B-Instruct-Q4_K_M.gguf"
    )
    assert cfg["model_artifacts"]["vae"].endswith("vae/flux2-vae.safetensors")
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
        "Qwen3VL-4B-Instruct-Q4_K_M.gguf"
    )
    assert command[command.index("--vae") + 1].endswith("flux2-vae.safetensors")
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


def test_flux3_auxiliary_artifacts_are_pinned_to_public_matching_weights() -> None:
    entry = load_registry()["models"][FLUX]
    artifacts = entry["artifacts"]
    encoder = artifacts["text_encoder"]
    vae = artifacts["vae"]

    assert entry["params"]["image_verify_existing_artifacts"] is True
    assert (encoder["repo"], encoder["revision"], encoder["filename"]) == (
        "Qwen/Qwen3-VL-4B-Instruct-GGUF",
        "bdc46888d3da9c4578c765ca9eb9f5a385a64e6c",
        "Qwen3VL-4B-Instruct-Q4_K_M.gguf",
    )
    assert encoder["sha256"] == (
        "66358cb18bb6b3b1b6675aa412c7a88ef01d228f481184d13668e5201c730a0a"
    )
    assert (vae["repo"], vae["revision"], vae["filename"]) == (
        "Comfy-Org/Flux-2-Dev",
        "ab9055628ea245000e610f2aa2c96f4746093546",
        "split_files/vae/flux2-vae.safetensors",
    )
    assert vae["sha256"] == (
        "d64f3a68e1cc4f9f4e29b6e0da38a0204fe9a49f2d4053f0ec1fa1ca02f9c4b5"
    )
    assert encoder["local_path"] == "text_encoder/Qwen3VL-4B-Instruct-Q4_K_M.gguf"
    assert vae["local_path"] == "vae/flux2-vae.safetensors"
    assert artifacts["diffusion_model"]["lmstudio_required"] is True
    assert artifacts["diffusion_model"]["lmstudio_relative_path"].endswith(
        "flux-3-image-klein-Q5_K_M.gguf"
    )
    for component in (encoder, vae):
        assert artifact_download_url(component) == (
            f"https://huggingface.co/{component['repo']}/resolve/"
            f"{component['revision']}/{component['filename']}?download=true"
        )
