from __future__ import annotations

from pathlib import Path

import pytest

from local_llm_server.llama_server_compat import (
    LLAMA_CPP_VALIDATED_RELEASE_COMMIT,
    LlamaServerCompatibility,
    LlamaServerBuildIdentity,
    build_llama_server_command,
    parse_llama_server_version,
    resolve_llama_server_binary,
    validate_llama_server_binary,
)


def _runner_for(versions: dict[str, str]):
    def run(path: Path) -> str:
        return versions[str(path)]

    return run


def _make_executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def test_parse_version_captures_build_and_commit_only():
    identity = parse_llama_server_version(
        "version: 10621 (`c1d0e7a`)\nbuilt with AppleClang for Darwin arm64\n"
    )

    assert identity == LlamaServerBuildIdentity(build=10621, commit="c1d0e7a")
    assert identity.backend_version == "build-10621@c1d0e7a"
    assert identity.matches_validated_release is True


def test_parse_modern_v030_version_format_keeps_validated_identity():
    identity = parse_llama_server_version(
        "version: 0.3.0-dev (build 10621, commit `c1d0e7a`)\n"
        "built with AppleClang for Darwin arm64\n"
    )

    assert identity == LlamaServerBuildIdentity(build=10621, commit="c1d0e7a")
    assert identity.matches_validated_release is True


def test_parse_modern_forward_build_format_keeps_exact_identity():
    identity = parse_llama_server_version(
        "version: 0.3.0-dev (build 10665, commit `ca3d5a3`)\n"
    )

    assert identity == LlamaServerBuildIdentity(build=10665, commit="ca3d5a3")
    assert identity.backend_version == "build-10665@ca3d5a3"
    assert identity.matches_validated_release is False


def test_parse_version_still_rejects_unattributable_semver_only_output():
    assert parse_llama_server_version("version: 0.3.0\n") is None


def test_exact_v030_release_uses_validated_profile():
    compatibility = validate_llama_server_binary(
        "/tmp/llama-server",
        run_command=lambda _path: (
            "version: 10621 "
            f"({LLAMA_CPP_VALIDATED_RELEASE_COMMIT[:9]})\n"
        ),
    )

    assert compatibility.supported is True
    assert compatibility.modern_runtime_options is True
    assert compatibility.exact_validated_release is True
    assert compatibility.profile == "validated-v0.3.0"


def test_modern_v030_release_uses_validated_profile():
    compatibility = validate_llama_server_binary(
        "/tmp/llama-server",
        run_command=lambda _path: (
            "version: 0.3.0-dev (build 10621, commit "
            f"`{LLAMA_CPP_VALIDATED_RELEASE_COMMIT[:9]}`)\n"
        ),
    )

    assert compatibility.supported is True
    assert compatibility.modern_runtime_options is True
    assert compatibility.exact_validated_release is True
    assert compatibility.profile == "validated-v0.3.0"


def test_newer_attributable_build_keeps_exact_identity_and_feature_floor():
    compatibility = validate_llama_server_binary(
        "/tmp/llama-server",
        run_command=lambda _path: "version: 10699 (abcdef1)\n",
    )

    assert compatibility.supported is True
    assert compatibility.exact_validated_release is False
    assert compatibility.profile == "forward-compatible-v0.3"
    assert compatibility.backend_version == "build-10699@abcdef1"


def test_old_build_is_rejected_unless_legacy_escape_hatch_is_explicit():
    runner = lambda _path: "version: 9261 (ad27757)\n"

    with pytest.raises(RuntimeError, match="older than the validated runtime floor"):
        validate_llama_server_binary("/tmp/llama-server", run_command=runner)

    compatibility = validate_llama_server_binary(
        "/tmp/llama-server",
        allow_unvalidated=True,
        run_command=runner,
    )
    assert compatibility.supported is False
    assert compatibility.modern_runtime_options is False
    assert compatibility.profile == "legacy-unvalidated"
    assert compatibility.backend_version == "build-9261@ad27757"


def test_auto_discovery_skips_old_lmstudio_candidate_for_supported_binary(tmp_path):
    old = _make_executable(
        tmp_path
        / ".lmstudio"
        / "extensions"
        / "backends"
        / "llama.cpp-old"
        / "llama-server"
    )
    supported = _make_executable(tmp_path / "bin" / "llama-server")
    runner = _runner_for(
        {
            str(old): "version: 9000 (1111111)\n",
            str(supported): "version: 10621 (c1d0e7a)\n",
        }
    )

    binary, compatibility = resolve_llama_server_binary(
        {},
        home=tmp_path,
        which_resolver=lambda _name: str(supported),
        run_command=runner,
    )

    assert binary == supported
    assert compatibility.exact_validated_release is True


def test_explicit_binary_is_authoritative_and_fails_closed(tmp_path):
    explicit = _make_executable(tmp_path / "explicit" / "llama-server")
    supported = _make_executable(tmp_path / "bin" / "llama-server")
    runner = _runner_for(
        {
            str(explicit): "version: 9000 (1111111)\n",
            str(supported): "version: 10621 (c1d0e7a)\n",
        }
    )

    with pytest.raises(RuntimeError, match="older than the validated runtime floor"):
        resolve_llama_server_binary(
            {"llama_server_bin": str(explicit)},
            home=tmp_path,
            which_resolver=lambda _name: str(supported),
            run_command=runner,
        )


def test_modern_command_aligns_server_slots_and_runtime_controls():
    compatibility = LlamaServerCompatibility(
        identity=LlamaServerBuildIdentity(build=10621, commit="c1d0e7a"),
        supported=True,
    )
    command = build_llama_server_command(
        binary="/bin/llama-server",
        model_path="/models/model.gguf",
        mmproj_path="/models/mmproj.gguf",
        host="127.0.0.1",
        port=8091,
        cfg={
            "ctx_size": 32768,
            "max_concurrent_requests": 3,
            "n_threads": 8,
            "n_batch": 1024,
            "n_ubatch": 256,
            "flash_attn": True,
            "llama_server_cont_batching": True,
            "llama_server_kv_unified": True,
            "llama_server_gpu_layers": "auto",
            "llama_server_load_mode": "auto",
            "llama_server_fit": True,
            "llama_server_fit_target_mib": 1536,
            "llama_server_fit_ctx": 8192,
            "llama_server_cache_type_k": "q8_0",
            "llama_server_cache_type_v": "q8_0",
            "llama_server_cache_ram_mib": 2048,
        },
        compatibility=compatibility,
    )

    assert command[:3] == ["/bin/llama-server", "-m", "/models/model.gguf"]
    assert ["--parallel", "3"] == command[command.index("--parallel") : command.index("--parallel") + 2]
    assert "--cont-batching" in command
    assert "--kv-unified" in command
    assert ["--gpu-layers", "auto"] == command[command.index("--gpu-layers") : command.index("--gpu-layers") + 2]
    assert ["--load-mode", "auto"] == command[command.index("--load-mode") : command.index("--load-mode") + 2]
    assert ["--fit", "on"] == command[command.index("--fit") : command.index("--fit") + 2]
    assert ["--cache-type-k", "q8_0"] == command[command.index("--cache-type-k") : command.index("--cache-type-k") + 2]
    assert ["--cache-ram", "2048"] == command[command.index("--cache-ram") : command.index("--cache-ram") + 2]


def test_legacy_command_does_not_receive_modern_only_options():
    compatibility = LlamaServerCompatibility(
        identity=LlamaServerBuildIdentity(build=9000, commit="1111111"),
        supported=False,
    )
    command = build_llama_server_command(
        binary="/bin/llama-server",
        model_path="/models/model.gguf",
        mmproj_path=None,
        host="127.0.0.1",
        port=8091,
        cfg={"ctx_size": 4096, "max_concurrent_requests": 4},
        compatibility=compatibility,
    )

    assert "--parallel" not in command
    assert "--kv-unified" not in command
    assert "--load-mode" not in command
    assert "--fit" not in command


def test_model_specific_minimum_build_rejects_globally_supported_older_binary(tmp_path):
    binary = _make_executable(tmp_path / "llama-server")

    with pytest.raises(RuntimeError, match="require build 10828"):
        resolve_llama_server_binary(
            {
                "llama_server_bin": str(binary),
                "llama_server_min_build": 10828,
            },
            run_command=lambda _path: "version: 10700 (abcdef1)\n",
        )


def test_model_specific_minimum_build_accepts_required_binary(tmp_path):
    binary = _make_executable(tmp_path / "llama-server")

    resolved, compatibility = resolve_llama_server_binary(
        {
            "llama_server_bin": str(binary),
            "llama_server_min_build": 10828,
        },
        run_command=lambda _path: "version: 10828 (abcdef2)\n",
    )

    assert resolved == binary
    assert compatibility.supported is True
    assert compatibility.minimum_build == 10828
    assert compatibility.backend_version == "build-10828@abcdef2"


def test_auto_discovery_finds_standard_system_dir_candidate_when_which_is_empty(tmp_path):
    system_dir = tmp_path / "opt" / "homebrew" / "bin"
    supported = _make_executable(system_dir / "llama-server")
    runner = _runner_for({str(supported): "version: 10621 (c1d0e7a)\n"})

    binary, compatibility = resolve_llama_server_binary(
        {},
        home=tmp_path / "home",
        which_resolver=lambda _name: None,
        system_dirs=(system_dir,),
        run_command=runner,
    )

    assert binary == supported
    assert compatibility.exact_validated_release is True


def test_auto_discovery_prefers_which_before_lmstudio(tmp_path):
    lmstudio_dir = (
        tmp_path
        / ".lmstudio"
        / "extensions"
        / "backends"
        / "llama.cpp-new"
    )
    lmstudio_binary = _make_executable(lmstudio_dir / "llama-server")
    which_binary = _make_executable(tmp_path / "bin" / "llama-server")

    probed_order: list[str] = []

    def tracking_runner(path: Path) -> str:
        probed_order.append(str(path))
        if path == which_binary:
            return "version: 10621 (c1d0e7a)\n"
        return "version: 1 (legacy)\n"

    binary, compatibility = resolve_llama_server_binary(
        {},
        home=tmp_path,
        which_resolver=lambda _name: str(which_binary),
        run_command=tracking_runner,
    )

    assert binary == which_binary
    assert compatibility.exact_validated_release is True
    # The PATH binary must be evaluated first, avoiding scanning lmstudio binaries
    assert probed_order == [str(which_binary)]

