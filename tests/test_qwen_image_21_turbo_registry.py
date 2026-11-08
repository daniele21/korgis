from __future__ import annotations

from pathlib import Path

from local_llm_server.model_sources import artifact_download_url, resolve_bundle_artifacts
from local_llm_server.registry import load_registry, validate_registry

TURBO = "qwen-image-2.1-turbo-gguf-q4km"
# Published by Hugging Face file pages (LFS/Xet SHA256, not Xet hash).
UNSLOTH_ARTIFACTS = {
    "diffusion_model": {
        "repo": "unsloth/Qwen-Image-2.1-Turbo-GGUF",
        "filename": "qwen-image-2.1-turbo-Q4_K_M.gguf",
        "local_path": "diffusion/qwen-image-2.1-turbo-Q4_K_M.gguf",
        "sha256": "8940e8b522af886e0c0f5afddf720e82dd223cb9a43580b5f61c4d740452ac20",
    },
    "text_encoder": {
        "repo": "unsloth/Qwen3-VL-8B-Instruct-GGUF",
        "filename": "Qwen3-VL-8B-Instruct-UD-Q4_K_XL.gguf",
        "local_path": "text_encoder/Qwen3-VL-8B-Instruct-UD-Q4_K_XL.gguf",
        "sha256": "e3d1a6e87c5cb31e054f2c3bc0dd82ffde052f613de5eef3665b7bd33c9b703e",
    },
    "vae": {
        "repo": "unsloth/Qwen-Image-2.1-FP8",
        "filename": "vae/qwen_image_2.1_vae_bf16.safetensors",
        "local_path": "vae/qwen_image_2.1_vae_bf16.safetensors",
        "sha256": "71879ffd5321e6d10c3c87513e2b474b1252efa7f3dec2969214a9bf06a6dd5c",
    },
}


def test_turbo_is_distinct_three_source_registry_profile() -> None:
    registry = load_registry()
    validate_registry(registry)
    entry = registry["models"][TURBO]
    standard = registry["models"]["qwen-image-2.1-gguf-q4km"]
    assert entry["model_id"] == "unsloth/Qwen-Image-2.1-Turbo-GGUF"
    assert entry["model_id"] != standard["model_id"]
    assert entry["backend"] == "stable_diffusion_cpp_image"
    assert entry["license"] == "qwen-research"
    assert entry["params"]["image_num_inference_steps"] == 8
    assert entry["params"]["image_sigma_schedule"] == "qwen-image-2.1-turbo-v1"
    assert entry["params"]["image_verify_existing_artifacts"] is True
    assert set(entry["artifacts"]) == set(UNSLOTH_ARTIFACTS)

    for role, expected in UNSLOTH_ARTIFACTS.items():
        artifact = entry["artifacts"][role]
        assert artifact["repo"] == expected["repo"]
        assert artifact["revision"] == "main"
        assert artifact["filename"] == expected["filename"]
        assert artifact["local_path"] == expected["local_path"]
        assert artifact["sha256"] == expected["sha256"]
        assert artifact_download_url(artifact) == (
            f"https://huggingface.co/{expected['repo']}/resolve/main/"
            f"{expected['filename']}?download=true"
        )


def test_turbo_bundle_paths_match_users_three_directories(
    tmp_path: Path,
) -> None:
    registry = load_registry()
    turbo = resolve_bundle_artifacts(
        TURBO, registry["models"][TURBO], tmp_path
    )
    standard = resolve_bundle_artifacts(
        "qwen-image-2.1-gguf-q4km",
        registry["models"]["qwen-image-2.1-gguf-q4km"],
        tmp_path,
    )
    assert set(turbo) == set(UNSLOTH_ARTIFACTS)
    assert all(path.is_relative_to(tmp_path / TURBO) for path in turbo.values())
    assert turbo["text_encoder"].suffix == ".gguf"
    assert not set(turbo.values()) & set(standard.values())


def test_local_bundle_is_detected_without_download(tmp_path: Path) -> None:
    from local_llm_server.model_sources import (
        is_complete_sdcpp_image_bundle,
    )

    registry = load_registry()
    paths = resolve_bundle_artifacts(TURBO, registry["models"][TURBO], tmp_path)
    assert not is_complete_sdcpp_image_bundle(paths)
    for path in paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"tiny fixture")
    assert is_complete_sdcpp_image_bundle(paths)
