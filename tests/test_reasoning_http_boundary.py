from __future__ import annotations

import json

from fastapi.testclient import TestClient

from local_llm_server.product_composition import install_product_http_stack
from local_llm_server.product_runtime_manager import ProductRuntimeManager
from local_llm_server.server import ServerSettings, create_app


class _ChunkEngine:
    backend = "fake"

    def __init__(self, chunks):
        self.chunks = list(chunks)

    def stream(self, payload):
        yield from self.chunks

    def complete(self, payload):
        return {
            "choices": [{"message": {"role": "assistant", "content": "unused"}}]
        }

    def close(self):
        pass


def _client(chunks, *, enable_thinking=True, show_thinking=False):
    manager = ProductRuntimeManager(default_model="demo")
    manager.add(
        {
            "model": "demo",
            "model_id": "org/demo",
            "model_path": "/demo",
            "backend": "fake",
            "modalities": ["text"],
            "thinking_mode": "switchable",
            "enable_thinking": enable_thinking,
            "show_thinking": show_thinking,
            "force_json": False,
            "default_temperature": 0.0,
            "default_top_p": 1.0,
            "default_top_k": 40,
            "default_min_p": 0.0,
            "default_repeat_penalty": 1.0,
            "max_concurrent_requests": 1,
        },
        _ChunkEngine(chunks),
    )
    application = create_app(manager, settings=ServerSettings())
    install_product_http_stack(application)
    return TestClient(application), manager


def _event(content=None, *, finish_reason=None, usage=None, timings=None):
    delta = {} if content is None else {"content": content}
    payload = {
        "id": "chatcmpl-test",
        "object": "chat.completion.chunk",
        "model": "org/demo",
        "choices": [
            {"index": 0, "delta": delta, "finish_reason": finish_reason}
        ],
    }
    if usage is not None:
        payload["usage"] = usage
    if timings is not None:
        payload["timings"] = timings
    return payload


def _stream_payload(**overrides):
    payload = {
        "model": "demo",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": True,
        "temperature": 0.0,
    }
    payload.update(overrides)
    return payload


def _parse_sse(text):
    events = []
    for block in text.split("\n\n"):
        block = block.strip()
        if not block.startswith("data:"):
            continue
        data = block[5:].strip()
        if data == "[DONE]":
            events.append("DONE")
        else:
            events.append(json.loads(data))
    return events


def _aggregate_content(events):
    parts = []
    for event in events:
        if not isinstance(event, dict):
            continue
        for choice in event.get("choices") or []:
            delta = choice.get("delta") or {}
            content = delta.get("content")
            if isinstance(content, str):
                parts.append(content)
    return "".join(parts)


def test_hidden_reasoning_is_chunk_safe_at_real_http_client_boundary():
    chunks = [
        _event("<thi"),
        _event("nk>private chain"),
        _event("</th"),
        _event("ink>{\"answer\":"),
        _event("42}"),
        _event(None, finish_reason="stop"),
    ]
    client, _manager = _client(chunks)

    response = client.post(
        "/v1/chat/completions",
        json=_stream_payload(enable_thinking=True, show_thinking=False),
    )

    assert response.status_code == 200, response.text
    events = _parse_sse(response.text)
    assert _aggregate_content(events) == '{"answer":42}'
    assert "private chain" not in response.text
    assert "<think>" not in response.text
    assert events[-1] == "DONE"


def test_close_without_opening_tag_does_not_leak_reasoning():
    client, _manager = _client(
        [
            _event("secret reasoning"),
            _event("</thi"),
            _event("nk>FINAL"),
        ]
    )

    response = client.post(
        "/v1/chat/completions",
        json=_stream_payload(enable_thinking=True, show_thinking=False),
    )

    assert _aggregate_content(_parse_sse(response.text)) == "FINAL"
    assert "secret reasoning" not in response.text


def test_missing_close_tag_fails_closed_when_reasoning_is_expected():
    client, _manager = _client(
        [_event("private reasoning without any delimiter")]
    )

    response = client.post(
        "/v1/chat/completions",
        json=_stream_payload(enable_thinking=True, show_thinking=False),
    )

    assert response.status_code == 200
    assert _aggregate_content(_parse_sse(response.text)) == ""
    assert "private reasoning" not in response.text


def test_show_thinking_true_preserves_intentional_exposure():
    client, _manager = _client(
        [_event("<think>private</think>FINAL")]
    )

    response = client.post(
        "/v1/chat/completions",
        json=_stream_payload(enable_thinking=True, show_thinking=True),
    )

    assert _aggregate_content(_parse_sse(response.text)) == "<think>private</think>FINAL"


def test_metrics_only_terminal_event_reaches_client_transport_and_inner_telemetry():
    client, manager = _client(
        [
            _event("reason</think>FINAL"),
            {
                "id": "chatcmpl-test",
                "object": "chat.completion.chunk",
                "model": "org/demo",
                "choices": [],
                "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
                "timings": {"predicted_ms": 20.0},
            },
        ]
    )

    response = client.post(
        "/v1/chat/completions",
        json=_stream_payload(enable_thinking=True, show_thinking=False),
    )

    events = _parse_sse(response.text)
    metric_events = [
        event for event in events if isinstance(event, dict) and event.get("usage")
    ]
    assert len(metric_events) == 1
    assert metric_events[0]["usage"]["completion_tokens"] == 4
    runtime = manager.resolve("demo")
    metrics = getattr(runtime, "latest_inference_metrics", None)
    assert metrics is not None
    assert metrics.counts.output_tokens == 4


def test_nonstream_structured_output_accepts_backend_separated_reasoning_content():
    client, _ = _client([])
    runtime = client.app.state.runtime_manager.resolve("demo")
    runtime.engine.complete = lambda payload: {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "model": "org/demo",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": '{"answer": 42}',
                    "reasoning_content": "private chain",
                },
                "finish_reason": "stop",
            }
        ],
    }

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "demo",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": False,
            "response_format": {"type": "json_object"},
            "enable_thinking": True,
            "show_thinking": False,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["choices"][0]["message"]["content"] == '{"answer": 42}'
    assert body["structured_output"] == {"answer": 42}
    assert body["thinking"] == ""


def test_stream_structured_output_accepts_backend_separated_reasoning_content():
    chunks = [
        {
            "id": "chatcmpl-test",
            "object": "chat.completion.chunk",
            "model": "org/demo",
            "choices": [
                {"index": 0, "delta": {"reasoning_content": "private chain"}, "finish_reason": None}
            ],
        },
        _event('{"answer": 42}'),
        _event(None, finish_reason="stop"),
    ]
    client, _ = _client(chunks)

    response = client.post(
        "/v1/chat/completions",
        json=_stream_payload(
            response_format={"type": "json_object"},
            enable_thinking=True,
            show_thinking=False,
        ),
    )

    assert response.status_code == 200
    assert _aggregate_content(_parse_sse(response.text)) == '{"answer": 42}'
