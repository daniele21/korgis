from __future__ import annotations

import os
from pathlib import Path

import pytest

from local_llm_server.stable_diffusion_cpp_compat import (
    build_sd_server_command,
    probe_sd_server_version,
    resolve_sd_server_binary,
)


def _executable(path: Path) -> Path:
    path.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | 0o111)
    return path


def test_resolve_sd_server_binary_prefers_explicit_path(tmp_path: Path) -> None:
    binary = _executable(tmp_path / "sd-server")

    assert resolve_sd_server_binary({"sd_server_bin": str(binary)}) == binary


def test_resolve_sd_server_binary_rejects_missing_explicit_path(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="does not exist"):
        resolve_sd_server_binary(
            {"sd_server_bin": str(tmp_path / "missing")}
        )


def test_probe_sd_server_version_is_best_effort(tmp_path: Path) -> None:
    binary = _executable(tmp_path / "sd-server")

    version = probe_sd_server_version(
        binary,
        run_command=lambda _path: "stable-diffusion.cpp version: v1.2.3 abcdef",
    )

    assert version == "v1.2.3 abcdef"


def test_build_sd_server_command_owns_bundle_and_runtime_flags(tmp_path: Path) -> None:
    binary = _executable(tmp_path / "sd-server")
    artifacts = {
        "diffusion_model": tmp_path / "diffusion.gguf",
        "text_encoder": tmp_path / "encoder.gguf",
        "vae": tmp_path / "vae.safetensors",
    }

    command = build_sd_server_command(
        binary=binary,
        artifacts=artifacts,
        host="127.0.0.1",
        port=8093,
        cfg={
            "image_diffusion_flash_attention": True,
            "image_offload_to_cpu": True,
        },
    )

    assert command == [
        str(binary),
        "--diffusion-model",
        str(artifacts["diffusion_model"]),
        "--vae",
        str(artifacts["vae"]),
        "--llm",
        str(artifacts["text_encoder"]),
        "--listen-ip",
        "127.0.0.1",
        "--listen-port",
        "8093",
        "--diffusion-fa",
        "--offload-to-cpu",
    ]



def test_build_sd_server_command_adds_low_memory_controls(tmp_path: Path) -> None:
    binary = _executable(tmp_path / "sd-server")
    artifacts = {
        "diffusion_model": tmp_path / "diffusion.gguf",
        "text_encoder": tmp_path / "encoder.gguf",
        "vae": tmp_path / "vae.safetensors",
    }

    command = build_sd_server_command(
        binary=binary,
        artifacts=artifacts,
        host="127.0.0.1",
        port=8093,
        cfg={
            "image_diffusion_flash_attention": True,
            "image_offload_to_cpu": True,
            "sd_server_params_backend": "diffusion=disk,te=cpu,vae=cpu",
            "sd_server_max_vram": "-2",
            "sd_server_model_args": "qwen_image_2_1_prefix_cache=false",
            "sd_server_mmap": True,
            "sd_server_disable_prefetch": True,
            "image_vae_tiling": True,
            "image_vae_tile_size": "256x256",
        },
    )

    assert command[-11:] == [
        "--params-backend",
        "diffusion=disk,te=cpu,vae=cpu",
        "--max-vram",
        "-2",
        "--model-args",
        "qwen_image_2_1_prefix_cache=false",
        "--mmap",
        "--disable-prefetch",
        "--vae-tiling",
        "--vae-tile-size",
        "256x256",
    ]
    assert "--diffusion-fa" in command
    assert "--offload-to-cpu" in command


def test_build_sd_server_command_rejects_tile_size_without_tiling(
    tmp_path: Path,
) -> None:
    binary = _executable(tmp_path / "sd-server")
    artifacts = {
        "diffusion_model": tmp_path / "diffusion.gguf",
        "text_encoder": tmp_path / "encoder.gguf",
        "vae": tmp_path / "vae.safetensors",
    }

    with pytest.raises(ValueError, match="requires image_vae_tiling"):
        build_sd_server_command(
            binary=binary,
            artifacts=artifacts,
            host="127.0.0.1",
            port=8093,
            cfg={
                "image_vae_tiling": False,
                "image_vae_tile_size": "256x256",
            },
        )
