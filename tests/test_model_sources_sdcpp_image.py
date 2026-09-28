from __future__ import annotations

from pathlib import Path

import pytest

from local_llm_server.model_sources import (
    artifact_download_url,
    is_complete_sdcpp_image_bundle,
    resolve_bundle_artifacts,
    resolve_registry_model,
)


def _entry() -> dict:
    return {
        "model_id": "unsloth/Qwen-Image-2.1-GGUF",
        "backend": "stable_diffusion_cpp_image",
        "artifacts": {
            "diffusion_model": {
                "repo": "unsloth/Qwen-Image-2.1-GGUF",
                "filename": "qwen-image-2.1-Q4_K_M.gguf",
                "local_path": "diffusion/model.gguf",
            },
            "text_encoder": {
                "repo": "unsloth/Qwen3-VL-8B-Instruct-GGUF",
                "filename": "encoder.gguf",
                "local_path": "text_encoder/encoder.gguf",
            },
            "vae": {
                "repo": "Comfy-Org/Qwen-Image-2.1",
                "filename": "vae/model.safetensors",
                "local_path": "vae/model.safetensors",
            },
        },
    }


def test_sdcpp_bundle_resolution_is_bounded_and_complete(tmp_path: Path) -> None:
    entry = _entry()
    artifacts = resolve_bundle_artifacts("image", entry, tmp_path)

    assert artifacts == {
        "diffusion_model": tmp_path / "image" / "diffusion" / "model.gguf",
        "text_encoder": tmp_path / "image" / "text_encoder" / "encoder.gguf",
        "vae": tmp_path / "image" / "vae" / "model.safetensors",
    }
    assert not is_complete_sdcpp_image_bundle(artifacts)

    for artifact in artifacts.values():
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_bytes(b"weights")

    assert is_complete_sdcpp_image_bundle(artifacts)
    resolved = resolve_registry_model(
        "image",
        entry,
        tmp_path,
        backend="stable_diffusion_cpp_image",
    )
    assert resolved.downloaded is True
    assert resolved.model_path == str((tmp_path / "image").resolve())
    assert resolved.artifacts == artifacts


def test_sdcpp_bundle_rejects_path_escape(tmp_path: Path) -> None:
    entry = _entry()
    entry["artifacts"]["vae"]["local_path"] = "../outside.safetensors"

    with pytest.raises(ValueError, match="escapes"):
        resolve_bundle_artifacts("image", entry, tmp_path)


def test_artifact_download_url_preserves_huggingface_subpath() -> None:
    assert artifact_download_url(
        {
            "repo": "Comfy-Org/Qwen-Image-2.1",
            "filename": "vae/qwen_image_2.1_vae_bf16.safetensors",
            "revision": "main",
        }
    ) == (
        "https://huggingface.co/Comfy-Org/Qwen-Image-2.1/resolve/main/"
        "vae/qwen_image_2.1_vae_bf16.safetensors?download=true"
    )
