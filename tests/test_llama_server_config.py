from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from local_llm_server.config import build_config


def _patch_registry(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "local_llm_server.config.load_registry",
        lambda: {
            "models_dir": tmp_path / "models",
            "default_model": "demo",
            "defaults": {},
            "models": {
                "demo": {
                    "model_id": "org/demo",
                    "backend": "llama_server",
                    "params": {},
                }
            },
        },
    )
    monkeypatch.setattr(
        "local_llm_server.config.resolve_registry_model",
        lambda *_args, **_kwargs: SimpleNamespace(
            model_path=str(tmp_path / "model.gguf"),
            source_type="managed",
            downloaded=True,
            mmproj_path=None,
        ),
    )


def test_llama_server_modern_profile_defaults_are_explicit(monkeypatch, tmp_path):
    _patch_registry(monkeypatch, tmp_path)

    cfg = build_config(model="demo")

    assert cfg["llama_server_allow_unvalidated"] is False
    assert cfg["llama_server_cont_batching"] is True
    assert cfg["llama_server_kv_unified"] is True
    assert cfg["llama_server_gpu_layers"] == "auto"
    assert cfg["llama_server_load_mode"] == "auto"
    assert cfg["llama_server_fit"] is True
    assert cfg["llama_server_fit_target_mib"] is None
    assert cfg["llama_server_fit_ctx"] is None
    assert cfg["llama_server_cache_type_k"] is None
    assert cfg["llama_server_cache_type_v"] is None
    assert cfg["llama_server_cache_ram_mib"] is None


def test_llama_server_environment_controls_are_typed(monkeypatch, tmp_path):
    _patch_registry(monkeypatch, tmp_path)
    monkeypatch.setenv("LOCAL_LLM_SERVER_ALLOW_UNVALIDATED", "true")
    monkeypatch.setenv("LOCAL_LLM_SERVER_CONT_BATCHING", "false")
    monkeypatch.setenv("LOCAL_LLM_SERVER_KV_UNIFIED", "false")
    monkeypatch.setenv("LOCAL_LLM_SERVER_GPU_LAYERS", "all")
    monkeypatch.setenv("LOCAL_LLM_SERVER_LOAD_MODE", "mmap")
    monkeypatch.setenv("LOCAL_LLM_SERVER_FIT", "false")
    monkeypatch.setenv("LOCAL_LLM_SERVER_FIT_TARGET_MIB", "2048")
    monkeypatch.setenv("LOCAL_LLM_SERVER_FIT_CTX", "8192")
    monkeypatch.setenv("LOCAL_LLM_SERVER_CACHE_TYPE_K", "q8_0")
    monkeypatch.setenv("LOCAL_LLM_SERVER_CACHE_TYPE_V", "q4_0")
    monkeypatch.setenv("LOCAL_LLM_SERVER_CACHE_RAM_MIB", "4096")

    cfg = build_config(model="demo")

    assert cfg["llama_server_allow_unvalidated"] is True
    assert cfg["llama_server_cont_batching"] is False
    assert cfg["llama_server_kv_unified"] is False
    assert cfg["llama_server_gpu_layers"] == "all"
    assert cfg["llama_server_load_mode"] == "mmap"
    assert cfg["llama_server_fit"] is False
    assert cfg["llama_server_fit_target_mib"] == 2048
    assert cfg["llama_server_fit_ctx"] == 8192
    assert cfg["llama_server_cache_type_k"] == "q8_0"
    assert cfg["llama_server_cache_type_v"] == "q4_0"
    assert cfg["llama_server_cache_ram_mib"] == 4096



def test_llama_server_moe_streaming_environment_is_typed(monkeypatch, tmp_path):
    _patch_registry(monkeypatch, tmp_path)
    monkeypatch.setenv("LOCAL_LLM_SERVER_MOE_STREAM_CACHE_GIB", "4")
    monkeypatch.setenv("LOCAL_LLM_SERVER_MOE_STREAM_IO_THREADS", "2")

    cfg = build_config(model="demo")

    assert cfg["llama_server_moe_stream_cache_gib"] == 4
    assert cfg["llama_server_moe_stream_io_threads"] == 2


def test_llama_server_moe_streaming_rejects_non_llama_backend(monkeypatch, tmp_path):
    import pytest

    _patch_registry(monkeypatch, tmp_path)
    monkeypatch.setenv("LOCAL_LLM_BACKEND", "llama_cpp")
    monkeypatch.setenv("LOCAL_LLM_SERVER_MOE_STREAM_CACHE_GIB", "4")

    with pytest.raises(ValueError, match="requires the llama_server backend"):
        build_config(model="demo")


def test_llama_server_moe_io_threads_require_cache(monkeypatch, tmp_path):
    import pytest

    _patch_registry(monkeypatch, tmp_path)
    monkeypatch.setenv("LOCAL_LLM_SERVER_MOE_STREAM_IO_THREADS", "4")

    with pytest.raises(ValueError, match="require a cache budget"):
        build_config(model="demo")


def test_llama_server_v1_moe_cache_environment_is_typed(monkeypatch, tmp_path):
    _patch_registry(monkeypatch, tmp_path)
    monkeypatch.setenv("LOCAL_LLM_SERVER_MOE_CACHE_MIB", "512")
    monkeypatch.setenv("LOCAL_LLM_SERVER_MOE_CACHE_CPU_SHARE", "0.25")

    cfg = build_config(model="demo")
    assert cfg["llama_server_moe_cache_mib"] == 512
    assert cfg["llama_server_moe_cache_cpu_share"] == 0.25


def test_llama_server_v1_moe_cache_rejects_wrong_backend(monkeypatch, tmp_path):
    import pytest

    _patch_registry(monkeypatch, tmp_path)
    monkeypatch.setenv("LOCAL_LLM_BACKEND", "mlx")
    monkeypatch.setenv("LOCAL_LLM_SERVER_MOE_CACHE_MIB", "128")

    with pytest.raises(ValueError, match="requires the llama_server backend"):
        build_config(model="demo")
