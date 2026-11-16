from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from local_llm_server.engine import LlamaCppEngine


class _FakeLlama:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.close_calls = 0
        self.__class__.instances.append(self)

    def close(self):
        self.close_calls += 1


def _cfg(tmp_path):
    return {
        "model_path": str(tmp_path / "model.gguf"),
        "download_url": "",
        "no_download": True,
        "ctx_size": 4096,
        "n_batch": 512,
        "n_ubatch": 512,
        "n_gpu_layers": 0,
        "n_threads": 4,
        "offload_kqv": True,
        "flash_attn": True,
        "use_mmap": True,
        "verbose": False,
        "chat_format": None,
    }


def test_llama_cpp_engine_close_releases_native_owner(monkeypatch, tmp_path):
    _FakeLlama.instances.clear()
    monkeypatch.setitem(sys.modules, "llama_cpp", SimpleNamespace(Llama=_FakeLlama))
    monkeypatch.setattr("local_llm_server.engine.ensure_model", lambda **_kwargs: None)

    engine = LlamaCppEngine(_cfg(tmp_path))
    [llm] = _FakeLlama.instances
    # Korgis may not expose upstream pickle-backed GGUF disk caching.
    assert "cache" not in llm.kwargs
    assert not hasattr(llm, "set_cache")

    engine.close()
    engine.close()

    assert llm.close_calls == 1
    assert engine.llm is None


def test_closed_llama_cpp_engine_cannot_accept_new_inference(monkeypatch, tmp_path):
    _FakeLlama.instances.clear()
    monkeypatch.setitem(sys.modules, "llama_cpp", SimpleNamespace(Llama=_FakeLlama))
    monkeypatch.setattr("local_llm_server.engine.ensure_model", lambda **_kwargs: None)

    engine = LlamaCppEngine(_cfg(tmp_path))
    engine.close()

    with pytest.raises(RuntimeError, match="closed"):
        engine.complete({"messages": []})


def test_llama_cpp_backend_ignores_untrusted_disk_cache_configuration(monkeypatch, tmp_path):
    _FakeLlama.instances.clear()
    monkeypatch.setitem(sys.modules, "llama_cpp", SimpleNamespace(Llama=_FakeLlama))
    monkeypatch.setattr("local_llm_server.engine.ensure_model", lambda **_kwargs: None)

    config = _cfg(tmp_path)
    config["cache"] = object()
    config["disk_cache"] = True
    config["cache_dir"] = str(tmp_path / "untrusted-cache")
    engine = LlamaCppEngine(config)
    [llm] = _FakeLlama.instances

    assert "cache" not in llm.kwargs
    assert "disk_cache" not in llm.kwargs
    assert "cache_dir" not in llm.kwargs
    engine.close()
