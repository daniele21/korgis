from __future__ import annotations

import asyncio
import logging
import threading
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Request

from local_llm_server.runtime import ModelRuntimeManager
from local_llm_server.runtime_evidence import RuntimeIdentitySnapshot
from local_llm_server.server import (
    ChatCompletionRequest,
    ModelActivateRequest,
    _load_or_activate_model,
    app,
    chat_completions,
    configure_runtime,
)


def _request() -> Request:
    return Request({"type": "http", "app": app})


class _Engine:
    backend = "fake"

    def __init__(self, content: str, barrier: threading.Barrier | None = None):
        self.content = content
        self.barrier = barrier
        self.received_model = None
        self.complete_calls = 0

    def complete(self, payload):
        self.complete_calls += 1
        self.received_model = payload["model"]
        if self.barrier:
            self.barrier.wait(timeout=2)
        return {
            "choices": [{"index": 0, "message": {"role": "assistant", "content": self.content}}],
            "usage": {"completion_tokens": 1},
        }

    def close(self):
        pass


class _StreamingEngine(_Engine):
    def stream(self, payload):
        self.received_model = payload["model"]

        def chunks():
            for content in ("one", "two"):
                yield {"choices": [{"index": 0, "delta": {"content": content}}]}

        return chunks()


def _cfg(key: str, model_id: str):
    return {
        "model": key,
        "model_id": model_id,
        "model_path": f"/{key}",
        "backend": "fake",
        "host": "127.0.0.1",
        "port": 1235,
        "default_temperature": 0.0,
        "default_repeat_penalty": 1.1,
        "enable_thinking": False,
        "show_thinking": False,
        "force_json": False,
    }


def _install_manager(first_engine, second_engine):
    first_cfg = _cfg("text", "org/text")
    second_cfg = _cfg("vision", "org/vision")
    manager = ModelRuntimeManager(default_model="text")
    manager.add(first_cfg, first_engine)
    manager.add(second_cfg, second_engine)
    configure_runtime(first_cfg, first_engine, manager)
    return manager


def test_chat_routes_to_requested_resident_model():
    text = _Engine("text response")
    vision = _Engine("vision response")
    _install_manager(text, vision)

    response = chat_completions(
        _request(),
        ChatCompletionRequest(
            model="vision",
            messages=[{"role": "user", "content": "describe"}],
            stream=False,
        )
    )

    assert response["content"] == "vision response"
    assert vision.received_model == "org/vision"
    assert text.received_model is None


def test_chat_without_model_uses_default_runtime():
    text = _Engine("default response")
    vision = _Engine("vision response")
    _install_manager(text, vision)

    response = chat_completions(
        _request(),
        ChatCompletionRequest(messages=[{"role": "user", "content": "hello"}], stream=False)
    )

    assert response["content"] == "default response"
    assert text.received_model == "org/text"


def test_deterministic_non_streaming_response_uses_lru_cache(caplog):
    text = _Engine("cached response")
    _install_manager(text, _Engine("vision"))
    req = ChatCompletionRequest(
        messages=[{"role": "user", "content": "same prompt"}],
        temperature=0.0,
        stream=False,
    )

    first = chat_completions(_request(), req)
    with caplog.at_level(logging.INFO, logger="local-llm.server"):
        second = chat_completions(_request(), req)

    assert first["content"] == second["content"] == "cached response"
    assert text.complete_calls == 1
    assert "Inference cache hit | model=text" in caplog.text


def test_sampled_response_is_not_cached():
    text = _Engine("fresh response")
    _install_manager(text, _Engine("vision"))
    req = ChatCompletionRequest(
        messages=[{"role": "user", "content": "same prompt"}],
        temperature=0.7,
        stream=False,
    )

    chat_completions(_request(), req)
    chat_completions(_request(), req)

    assert text.complete_calls == 2


def test_chat_rejects_model_that_is_not_resident():
    _install_manager(_Engine("text"), _Engine("vision"))

    with pytest.raises(HTTPException) as exc_info:
        chat_completions(
            _request(),
            ChatCompletionRequest(
                model="missing",
                messages=[{"role": "user", "content": "hello"}],
                stream=False,
            )
        )

    assert exc_info.value.status_code == 404


def test_chat_rejects_thinking_for_non_thinking_model():
    _install_manager(_Engine("text"), _Engine("vision"))

    with pytest.raises(HTTPException) as exc_info:
        chat_completions(
            _request(),
            ChatCompletionRequest(
                model="vision",
                messages=[{"role": "user", "content": "hello"}],
                enable_thinking=True,
                stream=False,
            ),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["code"] == "unsupported_thinking_mode"


def test_text_model_rejects_image_before_calling_backend():
    text = _Engine("text")
    _install_manager(text, _Engine("vision"))

    with pytest.raises(HTTPException) as exc_info:
        chat_completions(
            _request(),
            ChatCompletionRequest(
                model="text",
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "describe"},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}},
                    ],
                }],
                stream=False,
            )
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["code"] == "unsupported_modality"
    assert text.received_model is None


def test_requests_for_different_models_run_in_parallel():
    barrier = threading.Barrier(2)
    _install_manager(_Engine("text", barrier), _Engine("vision", barrier))
    responses = []

    def request(model):
        responses.append(
            chat_completions(
                _request(),
                ChatCompletionRequest(
                    model=model,
                    messages=[{"role": "user", "content": "go"}],
                    stream=False,
                )
            )["content"]
        )

    threads = [threading.Thread(target=request, args=(model,)) for model in ("text", "vision")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)

    assert sorted(responses) == ["text", "vision"]


def test_streaming_response_releases_lease_when_client_disconnects():
    manager = _install_manager(_StreamingEngine("unused"), _Engine("vision"))
    runtime = manager.resolve("text")
    response = chat_completions(
        _request(),
        ChatCompletionRequest(
            model="text",
            messages=[{"role": "user", "content": "go"}],
            stream=True,
        )
    )

    async def consume_one_chunk_and_disconnect():
        iterator = response.body_iterator
        await anext(iterator)
        await iterator.aclose()

    asyncio.run(consume_one_chunk_and_disconnect())

    assert runtime.active_requests == 0
    manager.unload("text")


def test_model_activation_exposes_privacy_safe_runtime_identity():
    engine = _Engine("ready")
    runtime = SimpleNamespace(
        key="bonsai",
        model_id="prism-ml/Ternary-Bonsai-2-27B-gguf",
        engine=engine,
        cfg={
            "model": "bonsai",
            "model_id": "prism-ml/Ternary-Bonsai-2-27B-gguf",
            "model_path": "/private/models/bonsai.gguf",
            "backend": "llama_server",
            "quantization": "PTQ1_0",
            "llama_server_port": 8091,
            "mlx_vlm_server_port": None,
        },
        runtime_identity_snapshot=RuntimeIdentitySnapshot(
            fingerprint="a" * 64,
            payload={
                "schema_version": 1,
                "artifact_key": "b" * 64,
                "backend": {
                    "name": "llama_server",
                    "version": "build-10709@prism123",
                    "implementation": "LlamaServerEngine",
                },
                "config_digest": "c" * 64,
                "hardware_key": "d" * 64,
            },
            captured_at=1.0,
        ),
    )

    class _Manager:
        default_model = None

        def load(self, model, **explicit):
            assert model == "bonsai"
            assert explicit == {}
            return runtime, True

        def set_default(self, key):
            self.default_model = key

    state = SimpleNamespace(runtime_manager=_Manager(), cfg=None, llm=None)
    payload = _load_or_activate_model(
        ModelActivateRequest(model="bonsai"),
        set_default=True,
        app_state=state,
    )

    assert payload["runtime_identity"]["fingerprint"] == "a" * 64
    assert (
        payload["runtime_identity"]["identity"]["backend"]["version"]
        == "build-10709@prism123"
    )
    assert payload["cfg"]["quantization"] == "PTQ1_0"
    assert "/private/models" not in str(payload["runtime_identity"])
