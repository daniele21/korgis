from __future__ import annotations

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.testclient import TestClient

from local_llm_server.completion_metrics import install_completion_metrics
from local_llm_server.request_middleware import install_request_policy
from local_llm_server.runtime import ModelRuntimeManager


class _Engine:
    backend = "fake"

    def close(self):
        pass


def _manager():
    manager = ModelRuntimeManager(default_model="demo")
    runtime = manager.add(
        {
            "model": "demo",
            "model_id": "org/demo",
            "backend": "fake",
            "modalities": ["text"],
            "max_concurrent_requests": 1,
        },
        _Engine(),
    )
    return manager, runtime


def _payload(*, stream=False):
    return {
        "model": "demo",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": stream,
    }


def test_nonstreaming_json_response_records_real_usage_timings_and_wall_clock():
    manager, runtime = _manager()
    app = FastAPI()
    app.state.runtime_manager = manager
    install_request_policy(app)
    install_completion_metrics(app)

    @app.post("/v1/chat/completions")
    async def chat(request: Request):
        request.state.queue_wait_ms = 12.5
        request.state.resource_snapshot_id = "resource-test"
        return JSONResponse(
            {
                "choices": [{"message": {"role": "assistant", "content": "private answer"}}],
                "usage": {"prompt_tokens": 11, "completion_tokens": 7},
                "timings": {
                    "prompt_ms": 8.0,
                    "predicted_ms": 20.0,
                    "predicted_per_second": 350.0,
                },
            }
        )

    response = TestClient(app).post("/v1/chat/completions", json=_payload())

    assert response.status_code == 200
    metrics = runtime.latest_inference_metrics
    assert metrics.durations.queue_wait_ms == 12.5
    assert metrics.durations.prompt_prefill_ms == 8.0
    assert metrics.durations.decode_ms == 20.0
    assert metrics.durations.ttft_ms is None
    assert metrics.durations.total_ms is not None
    assert metrics.durations.total_ms >= 0
    assert metrics.counts.input_tokens == 11
    assert metrics.counts.output_tokens == 7
    assert metrics.throughput.decode_tokens_per_second == 350.0
    assert metrics.sources["input_tokens"] == "response.usage.prompt_tokens"
    assert metrics.sources["queue_wait_ms"] == "request_scheduler.admission_wall_clock"
    assert metrics.resource_snapshot_id == "resource-test"
    rendered = str(metrics.to_public_dict())
    assert "private answer" not in rendered
    assert "hello" not in rendered


def test_capture_overflow_keeps_total_time_but_does_not_guess_tokens():
    manager, runtime = _manager()
    app = FastAPI()
    app.state.runtime_manager = manager
    install_request_policy(app)
    install_completion_metrics(app, max_capture_bytes=16)

    @app.post("/v1/chat/completions")
    async def chat():
        return JSONResponse(
            {
                "choices": [{"message": {"content": "x" * 200}}],
                "usage": {"prompt_tokens": 99, "completion_tokens": 99},
            }
        )

    response = TestClient(app).post("/v1/chat/completions", json=_payload())

    assert response.status_code == 200
    metrics = runtime.latest_inference_metrics
    assert metrics.durations.total_ms is not None
    assert metrics.counts.input_tokens is None
    assert metrics.counts.output_tokens is None
    assert "input_tokens" not in metrics.sources


def test_invalid_json_keeps_completed_wall_clock_only():
    manager, runtime = _manager()
    app = FastAPI()
    app.state.runtime_manager = manager
    install_request_policy(app)
    install_completion_metrics(app)

    @app.post("/v1/chat/completions")
    async def chat():
        return Response(content=b"{invalid", media_type="application/json")

    response = TestClient(app).post("/v1/chat/completions", json=_payload())

    assert response.status_code == 200
    metrics = runtime.latest_inference_metrics
    assert metrics.durations.total_ms is not None
    assert metrics.counts.input_tokens is None
    assert metrics.counts.output_tokens is None


def test_streaming_request_is_not_recorded_by_completion_metrics():
    manager, runtime = _manager()
    app = FastAPI()
    app.state.runtime_manager = manager
    install_request_policy(app)
    install_completion_metrics(app)

    @app.post("/v1/chat/completions")
    async def chat():
        async def events():
            yield 'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
            yield "data: [DONE]\n\n"

        return StreamingResponse(events(), media_type="text/event-stream")

    response = TestClient(app).post("/v1/chat/completions", json=_payload(stream=True))

    assert response.status_code == 200
    assert not hasattr(runtime, "latest_inference_metrics")
