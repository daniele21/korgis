from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from local_llm_server.core import ErrorCode, InferenceError, InferenceRequest, OutputConstraints, TaskType
from local_llm_server.request_middleware import install_request_policy
from local_llm_server.runtime import ModelRuntimeManager
from local_llm_server.server import create_app
from local_llm_server.task_policy import enforce_request_capabilities


def test_explicit_chat_only_runtime_rejects_structured_generation():
    request = InferenceRequest(
        task=TaskType.STRUCTURED_GENERATION,
        model="demo",
        input_text="return json",
        output=OutputConstraints(format="json"),
    )
    cfg = {
        "tasks": ["chat"],
        "input_modalities": ["text"],
        "output_modalities": ["text"],
        "features": ["streaming"],
    }

    with pytest.raises(InferenceError) as exc_info:
        enforce_request_capabilities(request, runtime_config=cfg)

    assert exc_info.value.code is ErrorCode.UNSUPPORTED_TASK
    assert exc_info.value.details["task"] == "structured_generation"


def test_explicit_non_streaming_runtime_rejects_stream_request():
    request = InferenceRequest(
        task=TaskType.CHAT,
        model="demo",
        input_text="hello",
        stream=True,
    )
    cfg = {
        "tasks": ["chat"],
        "input_modalities": ["text"],
        "output_modalities": ["text"],
        "features": [],
    }

    with pytest.raises(InferenceError) as exc_info:
        enforce_request_capabilities(request, runtime_config=cfg)
    assert exc_info.value.code is ErrorCode.UNSUPPORTED_TASK


def test_legacy_text_runtime_conservatively_supports_chat_structured_and_streaming():
    cfg = {"modalities": ["text"], "thinking_mode": "none"}
    chat = InferenceRequest(task=TaskType.CHAT, model="demo", input_text="hello", stream=True)
    structured = InferenceRequest(
        task=TaskType.STRUCTURED_GENERATION,
        model="demo",
        input_text="json",
        output=OutputConstraints(format="json"),
    )

    assert enforce_request_capabilities(chat, runtime_config=cfg)
    assert enforce_request_capabilities(structured, runtime_config=cfg)


class _Engine:
    backend = "fake"

    def __init__(self):
        self.calls = 0

    def complete(self, payload):
        self.calls += 1
        return {
            "choices": [{"message": {"role": "assistant", "content": "{}"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }

    def close(self):
        pass


def test_middleware_rejects_unsupported_structured_task_before_backend():
    engine = _Engine()
    cfg = {
        "model": "chat-only",
        "model_id": "org/chat-only",
        "model_path": "/chat-only",
        "backend": "fake",
        "modalities": ["text"],
        "tasks": ["chat"],
        "input_modalities": ["text"],
        "output_modalities": ["text"],
        "features": ["streaming"],
        "default_temperature": 0.0,
        "default_top_p": 1.0,
        "default_top_k": 40,
        "default_min_p": 0.0,
        "default_repeat_penalty": 1.0,
        "thinking_mode": "none",
        "enable_thinking": False,
        "show_thinking": False,
        "force_json": False,
    }
    manager = ModelRuntimeManager(default_model="chat-only")
    manager.add(cfg, engine)
    app = create_app(manager)
    install_request_policy(app)

    response = TestClient(app).post(
        "/v1/chat/completions",
        json={
            "model": "chat-only",
            "messages": [{"role": "user", "content": "return json"}],
            "response_format": {"type": "json_object"},
            "stream": False,
        },
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "unsupported_task"
    assert detail["details"]["task"] == "structured_generation"
    assert engine.calls == 0
