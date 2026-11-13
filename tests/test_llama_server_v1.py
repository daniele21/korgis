"""Deterministic v1 identity, optional feature and resource contracts."""
from __future__ import annotations

import pytest

from local_llm_server.llama_server_compat import (
    LlamaServerBuildIdentity,
    LlamaServerCompatibility,
    build_llama_server_command,
    parse_llama_server_version,
    validate_llama_server_binary,
)
from local_llm_server.memory_envelope import resident_memory_envelope
from local_llm_server.runtime_identity import resolved_config_digest, resolved_config_payload

_TAG = "2d8a32502909ca065c41c4cf5f2fb1c427c047fc"
_HELP = (
    "--moe-cache-mib N --moe-cache-cpu-share F "
    "--cache-type-k f8_e4m3 --cache-type-v f8_e4m3"
)


def _compatible(*, commit: str = _TAG[:9], build: int = 12360):
    return LlamaServerCompatibility(
        identity=LlamaServerBuildIdentity(build=build, commit=commit),
        supported=True,
    )


def _cmd(config=None, *, help_text: str = _HELP, compat=None):
    return build_llama_server_command(
        binary="/tmp/llama-server",
        model_path="/tmp/model.gguf",
        mmproj_path=None,
        host="127.0.0.1",
        port=8091,
        cfg={"ctx_size": 4096, **(config or {})},
        compatibility=compat or _compatible(),
        v1_help_runner=lambda _binary: help_text,
    )


def test_v1_tag_attribution_is_not_a_build_number_guess():
    identity = parse_llama_server_version(
        "version: 1.0.0 (build 12360, commit `2d8a32502909`)\n"
    )
    assert identity is not None
    assert identity.matches_v1_release
    info = validate_llama_server_binary(
        "/tmp/llama-server",
        run_command=lambda _: "version: 12360 (2d8a32502909)\n",
    )
    assert info.profile == "attributed-v1.0.0"
    assert _compatible(commit="deadbee").profile == "forward-compatible-v0.3"
    assert _compatible(build=12368).profile == "forward-compatible-v0.3"


def test_v1_default_keeps_v03_compatible_command_contract():
    command = _cmd({"llama_server_cache_type_k": "q8_0"})
    assert "--parallel" in command
    assert "--moe-cache-mib" not in command
    assert "--moe-cache-cpu-share" not in command


def test_v1_fp8_and_moe_host_cache_are_opt_in():
    command = _cmd({
        "llama_server_cache_type_k": "f8_e4m3",
        "llama_server_cache_type_v": "f8_e4m3",
        "llama_server_moe_cache_mib": 1024,
        "llama_server_moe_cache_cpu_share": 0.25,
    })
    assert command[command.index("--cache-type-k") + 1] == "f8_e4m3"
    assert command[command.index("--cache-type-v") + 1] == "f8_e4m3"
    assert command[command.index("--moe-cache-mib") + 1] == "1024"
    assert command[command.index("--moe-cache-cpu-share") + 1] == "0.25"


def test_v1_options_require_advertised_flags_and_fp8_type():
    with pytest.raises(RuntimeError, match="does not advertise"):
        _cmd({"llama_server_moe_cache_mib": 1024}, help_text="--parallel")
    with pytest.raises(RuntimeError, match="does not advertise"):
        _cmd(
            {"llama_server_cache_type_k": "f8_e4m3"},
            help_text="--cache-type-k f16 --cache-type-v f16",
        )


def test_future_build_and_unproven_fork_cannot_enable_v1_features():
    with pytest.raises(ValueError, match="attributable v1.0.0"):
        _cmd({"llama_server_moe_cache_mib": 128}, compat=_compatible(build=12368))
    with pytest.raises(ValueError, match="must be one of"):
        _cmd({"llama_server_cache_type_k": "f8_e4m3"}, compat=_compatible(build=12368))


@pytest.mark.parametrize("value", [0, -1, True, 0.5, "12"])
def test_host_cache_requires_positive_integer(value):
    with pytest.raises(ValueError, match="positive integer"):
        _cmd({"llama_server_moe_cache_mib": value})


@pytest.mark.parametrize("value", [-0.1, 1.1, float("nan"), float("inf"), True, "0.5"])
def test_cpu_share_requires_finite_number(value):
    with pytest.raises(ValueError, match="between 0 and 1"):
        _cmd({
            "llama_server_moe_cache_mib": 64,
            "llama_server_moe_cache_cpu_share": value,
        })


def test_cpu_share_without_cache_or_combining_ssd_streaming_is_rejected():
    with pytest.raises(ValueError, match="requires a cache budget"):
        _cmd({"llama_server_moe_cache_cpu_share": 0.5})
    with pytest.raises(ValueError, match="cannot be combined"):
        _cmd({
            "llama_server_moe_cache_mib": 64,
            "llama_server_moe_stream_cache_gib": 2,
        }, help_text=_HELP + " --moe-stream-cache")


def test_moe_host_cache_accounting_does_not_discount_weights(tmp_path):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"content")
    env = resident_memory_envelope({
        "model_path": str(model),
        "llama_server_moe_cache_mib": 64,
        "resource_backend_overhead_bytes": 0,
        "resource_context_cache_bytes": 0,
        "resource_safety_margin_bytes": 0,
    })
    parts = env.as_dict()["components"]
    assert parts["model_weights"]["bytes"] == 7
    assert parts["moe_host_cache"]["bytes"] == 64 * 1024**2
    assert env.accounted_bytes == 7 + 64 * 1024**2


def test_v1_options_have_path_free_configuration_identity():
    base = {"backend": "llama_server", "llama_server_cache_type_k": "f16"}
    newer = {
        **base,
        "llama_server_cache_type_k": "f8_e4m3",
        "llama_server_moe_cache_mib": 64,
        "llama_server_moe_cache_cpu_share": 0.5,
        "model_path": "/private/example/model.gguf",
    }
    assert resolved_config_digest(base) != resolved_config_digest(newer)
    payload = resolved_config_payload(newer)
    assert payload["llama_server_moe_cache_mib"] == 64
    assert payload["llama_server_moe_cache_cpu_share"] == 0.5
    assert "model_path" not in payload
