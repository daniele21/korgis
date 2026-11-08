from __future__ import annotations

from pathlib import Path

from local_llm_server.model_sources import artifact_download_url, resolve_bundle_artifacts
from local_llm_server.registry import load_registry, validate_registry

TURBO = "qwen-image-2.1-turbo-gguf-q4km"
REVISION = "24d33ddeffd21374e8b04a84f93eb73a645fc74e"
EXPECTED_NAMES = {
    "diffusion_model": "qwen-image-2.1-turbo-Q4_K_M.gguf",
    "text_encoder": "qwen3vl_8b_int8_convrot.safetensors",
    "vae": "qwen_image_2.1_vae_bf16.safetensors",
}


def test_turbo_is_pinned_distinct_registry_profile() -> None:
    registry = load_registry()
    validate_registry(registry)
    entry = registry["models"][TURBO]
    standard = registry["models"]["qwen-image-2.1-gguf-q4km"]
    assert entry["model_id"] != standard["model_id"]
    assert entry["backend"] == "stable_diffusion_cpp_image"
    assert entry["license"] == "qwen-research"
    assert entry["params"]["image_num_inference_steps"] == 8
    assert entry["params"]["image_sigma_schedule"] == "qwen-image-2.1-turbo-v1"

    for role, filename in EXPECTED_NAMES.items():
        artifact = entry["artifacts"][role]
        assert artifact["revision"] == REVISION
        assert artifact["sha256"] and len(artifact["sha256"]) == 64
        assert artifact["local_path"].endswith(filename)
        url = artifact_download_url(artifact)
        assert f"/resolve/{REVISION}/" in url
        assert artifact["filename"] in url


def test_turbo_bundle_paths_are_owned_and_do_not_collide_with_standard(
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
    assert set(turbo) == set(EXPECTED_NAMES)
    assert all(path.is_relative_to(tmp_path / TURBO) for path in turbo.values())
    assert not set(turbo.values()) & set(standard.values())
