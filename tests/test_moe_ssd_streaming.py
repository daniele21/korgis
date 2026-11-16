"""Fail-closed experimental llama.cpp MoE SSD expert-streaming contract.

These deterministic tests do not establish actual SSD streaming or safe Mac peaks.
"""
from __future__ import annotations

import pytest

from local_llm_server.llama_server_compat import (
    LlamaServerBuildIdentity,
    LlamaServerCompatibility,
    build_llama_server_command,
)
from local_llm_server.memory_envelope import resident_memory_envelope
from local_llm_server.runtime_identity import resolved_config_digest, resolved_config_payload


def _compat(*, supported: bool = True) -> LlamaServerCompatibility:
    return LlamaServerCompatibility(
        identity=LlamaServerBuildIdentity(build=10621, commit="c1d0e7a"),
        supported=supported,
    )


def _command(cfg, *, help_text="--moe-stream-cache --moe-stream-io-threads", supported=True):
    return build_llama_server_command(
        binary="/bin/llama-server",
        model_path="/models/experimental-moe.gguf",
        mmproj_path=None,
        host="127.0.0.1",
        port=8091,
        cfg={"ctx_size": 4096, **cfg},
        compatibility=_compat(supported=supported),
        moe_stream_help_runner=lambda _binary: help_text,
    )


def test_default_command_has_no_experimental_streaming_flags():
    cmd = _command({})
    assert "--moe-stream-cache" not in cmd
    assert "--moe-stream-io-threads" not in cmd


def test_opt_in_streaming_requires_supported_binary_flags():
    cmd = _command({
        "llama_server_moe_stream_cache_gib": 4,
        "llama_server_moe_stream_io_threads": 2,
    })
    assert cmd[cmd.index("--moe-stream-cache") + 1] == "4G"
    assert cmd[cmd.index("--moe-stream-io-threads") + 1] == "2"


def test_unadvertised_experimental_flag_fails_before_process_start():
    with pytest.raises(RuntimeError, match="does not advertise"):
        _command({"llama_server_moe_stream_cache_gib": 4}, help_text="--load-mode mmap")


def test_missing_optional_io_flag_fails_closed():
    with pytest.raises(RuntimeError, match="does not advertise"):
        _command({
            "llama_server_moe_stream_cache_gib": 4,
            "llama_server_moe_stream_io_threads": 2,
        }, help_text="--moe-stream-cache")


@pytest.mark.parametrize("mode", ["mmap", "mmap+mlock", "mlock", "dio"])
def test_streaming_refuses_incompatible_load_mode(mode):
    with pytest.raises(ValueError, match="cannot be combined"):
        _command({
            "llama_server_moe_stream_cache_gib": 4,
            "llama_server_load_mode": mode,
        })


@pytest.mark.parametrize("budget", [0, -1, True, 0.5, "4.5", "oops"])
def test_invalid_cache_budget_rejected(budget):
    with pytest.raises(ValueError, match="positive integer"):
        _command({"llama_server_moe_stream_cache_gib": budget})


def test_threads_without_streaming_cache_are_invalid():
    with pytest.raises(ValueError, match="require a cache budget"):
        _command({"llama_server_moe_stream_io_threads": 2})


def test_old_or_unattributable_runtime_never_silently_ignores_stream_request():
    with pytest.raises(ValueError, match="requires an attributable modern"):
        _command({"llama_server_moe_stream_cache_gib": 4}, supported=False)


def test_conservative_memory_adds_cache_without_discounting_model_file(tmp_path):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"1234567890")
    envelope = resident_memory_envelope({
        "model_path": str(model),
        "llama_server_moe_stream_cache_gib": 2,
        "resource_backend_overhead_bytes": 0,
        "resource_context_cache_bytes": 0,
        "resource_safety_margin_bytes": 0,
    })
    meta = envelope.as_dict()
    assert meta["components"]["model_weights"]["bytes"] == 10
    assert meta["components"]["moe_stream_cache"]["source"] == "configured_cache_upper_budget"
    assert meta["components"]["moe_stream_cache"]["bytes"] == 2 * 1024**3
    assert envelope.accounted_bytes == 10 + 2 * 1024**3
    assert envelope.complete is True


def test_experimental_budget_is_part_of_safe_runtime_identity():
    default = {"backend": "llama_server"}
    streamed = {
        **default,
        "llama_server_moe_stream_cache_gib": 4,
        "llama_server_moe_stream_io_threads": 2,
    }
    assert resolved_config_digest(default) != resolved_config_digest(streamed)
    assert resolved_config_payload(streamed)["llama_server_moe_stream_cache_gib"] == 4
    assert "model_path" not in resolved_config_payload(streamed)
