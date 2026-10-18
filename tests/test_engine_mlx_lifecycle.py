
"""MLX owner reference release, without importing GPU dependencies in CI."""
from __future__ import annotations

import weakref

import pytest

from local_llm_server.engine import MLXEngine
from local_llm_server.product_runtime_manager import ProductRuntimeManager


class _Held:
    pass


def _engine() -> tuple[MLXEngine, weakref.ReferenceType[_Held], weakref.ReferenceType[_Held]]:
    model, tokenizer = _Held(), _Held()
    model_ref, tokenizer_ref = weakref.ref(model), weakref.ref(tokenizer)
    engine = MLXEngine.__new__(MLXEngine)
    engine.model = model
    engine.tokenizer = tokenizer
    engine.model_ref = "test-model"
    engine.cfg = {"model": "test-model", "max_kv_size": 128}
    return engine, model_ref, tokenizer_ref


def test_mlx_close_drops_both_owned_references_and_is_idempotent():
    engine, model_ref, tokenizer_ref = _engine()
    assert model_ref() is not None and tokenizer_ref() is not None
    engine.close()
    assert engine.model is None
    assert engine.tokenizer is None
    assert model_ref() is None
    assert tokenizer_ref() is None
    engine.close()


def test_mlx_closed_runtime_cannot_start_new_generation_without_importing_mlx():
    engine, _, _ = _engine()
    engine.close()
    with pytest.raises(RuntimeError, match="mlx runtime is closed"):
        next(engine.stream({"messages": [{"role": "user", "content": "hello"}]}))


def test_lease_prevents_unload_and_other_mlx_residency_survives():
    manager = ProductRuntimeManager(default_model="first")
    first_engine, model_ref, tokenizer_ref = _engine()
    other_engine, other_ref, _ = _engine()
    cfg = {
        "backend": "mlx",
        "modalities": ["text"],
        "max_concurrent_requests": 1,
    }
    first = manager.add(
        {"model": "first", "model_id": "org/first", **cfg},
        first_engine, key="first",
    )
    manager.add(
        {"model": "second", "model_id": "org/second", **cfg},
        other_engine, key="second",
    )
    with manager.lease_runtime(first):
        with pytest.raises(RuntimeError, match="active request"):
            manager.unload("first")
        assert model_ref() is not None
    manager.unload("first")
    assert model_ref() is None
    assert tokenizer_ref() is None
    assert first_engine.model is None
    assert other_ref() is not None
    assert other_engine.model is not None
    assert [runtime.key for runtime in manager.list()] == ["second"]
    manager.shutdown()
    assert other_ref() is None
