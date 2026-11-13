"""Runtime lifecycle regressions for an attributed llama.cpp v1 server.

Deterministic fake subprocess tests do not establish real-model performance.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from local_llm_server.engine import LlamaServerEngine
from local_llm_server.llama_server_compat import (
    LlamaServerBuildIdentity,
    LlamaServerCompatibility,
)


class _OwnedProcess:
    instances: list["_OwnedProcess"] = []
    readiness_fails = False

    def __init__(self, command, *, name, logger, env=None):
        self.command = list(command)
        self.started = False
        self.closed = False
        self.__class__.instances.append(self)

    def start(self):
        self.started = True

    def wait_ready(self, predicate, *, timeout):
        if self.__class__.readiness_fails:
            raise RuntimeError("v1 readiness failed")

    def close(self):
        self.closed = True


def _configure(monkeypatch, tmp_path: Path):
    _OwnedProcess.instances.clear()
    _OwnedProcess.readiness_fails = False
    binary = tmp_path / "llama-server"
    compatibility = LlamaServerCompatibility(
        identity=LlamaServerBuildIdentity(build=12360, commit="2d8a32502909"),
        supported=True,
    )
    monkeypatch.setattr("local_llm_server.engine.ensure_model", lambda **kwargs: None)
    monkeypatch.setattr(
        "local_llm_server.engine.resolve_llama_server_binary",
        lambda cfg: (binary, compatibility),
    )
    monkeypatch.setattr("local_llm_server.engine.ManagedProcess", _OwnedProcess)
    monkeypatch.setattr(
        "local_llm_server.llama_server_v1_options._read_help",
        lambda binary, *, help_runner=None: "--moe-cache-mib --moe-cache-cpu-share "
                                         "--cache-type-k f8_e4m3 --cache-type-v f8_e4m3",
    )
    return {
        "backend": "llama_server",
        "model": "demo",
        "model_id": "org/demo",
        "model_path": str(tmp_path / "model.gguf"),
        "no_download": True,
        "ctx_size": 4096,
        "max_concurrent_requests": 1,
        "llama_server_moe_cache_mib": 128,
    }


def test_v1_engine_flags_are_owned_and_unload_cleans_process(monkeypatch, tmp_path):
    config = _configure(monkeypatch, tmp_path)
    engine = LlamaServerEngine(config)
    [owned] = _OwnedProcess.instances
    assert engine.backend_compatibility_profile == "attributed-v1.0.0"
    assert owned.started
    assert owned.command[owned.command.index("--moe-cache-mib") + 1] == "128"
    engine.close()
    engine.close()
    assert owned.closed
    assert engine.process is None


def test_v1_partial_startup_failure_reaps_owned_process(monkeypatch, tmp_path):
    config = _configure(monkeypatch, tmp_path)
    _OwnedProcess.readiness_fails = True
    with pytest.raises(RuntimeError, match="v1 readiness failed"):
        LlamaServerEngine(config)
    [owned] = _OwnedProcess.instances
    assert owned.started
    assert owned.closed


def test_v1_incompatible_options_fail_before_spawning(monkeypatch, tmp_path):
    config = _configure(monkeypatch, tmp_path)
    config["llama_server_moe_stream_cache_gib"] = 2
    with pytest.raises(ValueError, match="cannot be combined"):
        LlamaServerEngine(config)
    assert _OwnedProcess.instances == []
