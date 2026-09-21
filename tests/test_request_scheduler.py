from __future__ import annotations

import asyncio

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from local_llm_server.async_scheduler import AsyncRuntimeGate
from local_llm_server.core.contracts import InferenceRequest, TaskType
from local_llm_server.global_execution_governor import GlobalExecutionGovernor
from local_llm_server.request_middleware import install_request_policy
from local_llm_server.request_scheduler import (
    _hold_gate_for_stream,
    install_request_scheduler,
)
from local_llm_server.runtime import ModelRuntimeManager
from local_llm_server.scheduler_policy import RequestSchedulerSettings


class _Engine:
    backend = "fake"

    def close(self):
        pass


def _cfg(model: str):
    return {
        "model": model,
        "model_id": f"org/{model}",
        "backend": "fake",
        "modalities": ["text"],
        "max_concurrent_requests": 1,
    }


def _request_payload(model: str):
    return {
        "model": model,
        "messages": [{"role": "user", "content": "hello"}],
        "stream": False,
    }


def _app(
    *,
    models=("demo",),
    capacity: int | None = 1,
    global_max_running: int | None = None,
    global_queue_capacity: int | None = None,
):
    manager = ModelRuntimeManager(default_model=models[0])
    for model in models:
        manager.add(_cfg(model), _Engine())
    app = FastAPI()
    app.state.runtime_manager = manager
    install_request_scheduler(
        app,
        settings=RequestSchedulerSettings(
            queue_capacity=capacity,
            global_max_running=global_max_running,
            global_queue_capacity=global_queue_capacity,
        ),
    )
    # Last-added middleware executes outermost, so policy prepares the request
    # before scheduler/global-governor admission runs.
    install_request_policy(app)
    return app, manager


def test_queue_overflow_returns_429_before_backend_route_is_invoked():
    async def scenario():
        app, manager = _app(capacity=1)
        started = asyncio.Event()
        release = asyncio.Event()
        route_calls = 0

        @app.post("/v1/chat/completions")
        async def chat(request: Request):
            nonlocal route_calls
            route_calls += 1
            started.set()
            await release.wait()
            return JSONResponse({"ok": True})

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            first = asyncio.create_task(client.post("/v1/chat/completions", json=_request_payload("demo")))
            await asyncio.wait_for(started.wait(), timeout=1)
            second = asyncio.create_task(client.post("/v1/chat/completions", json=_request_payload("demo")))
            await asyncio.sleep(0.02)

            overflow = await client.post("/v1/chat/completions", json=_request_payload("demo"))
            assert overflow.status_code == 429
            assert overflow.json()["detail"]["code"] == "resource_exhausted"
            assert route_calls == 1

            release.set()
            first_response, second_response = await asyncio.gather(first, second)
            assert first_response.status_code == 200
            assert second_response.status_code == 200
            assert route_calls == 2
            assert float(second_response.headers["x-local-llm-queue-wait-ms"]) > 0

        runtime = manager.resolve("demo")
        metrics = runtime.latest_inference_metrics
        assert metrics.durations.queue_wait_ms is not None
        assert metrics.durations.queue_wait_ms > 0
        gate = app.state.runtime_gate_registry.gate_for(runtime)
        assert (await gate.snapshot()).requests == ()

    asyncio.run(scenario())


def test_explicit_queue_timeout_returns_408_without_invoking_route():
    async def scenario():
        app, _ = _app(capacity=1)
        started = asyncio.Event()
        release = asyncio.Event()
        route_calls = 0

        @app.post("/v1/chat/completions")
        async def chat(request: Request):
            nonlocal route_calls
            route_calls += 1
            started.set()
            await release.wait()
            return JSONResponse({"ok": True})

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            first = asyncio.create_task(client.post("/v1/chat/completions", json=_request_payload("demo")))
            await asyncio.wait_for(started.wait(), timeout=1)
            timed_out = await client.post(
                "/v1/chat/completions",
                json=_request_payload("demo"),
                headers={"x-local-llm-queue-timeout-ms": "10"},
            )
            assert timed_out.status_code == 408
            assert timed_out.json()["detail"]["code"] == "timeout"
            assert route_calls == 1
            release.set()
            assert (await first).status_code == 200

    asyncio.run(scenario())


def test_runtime_gates_are_isolated_per_resident_model():
    async def scenario():
        app, _ = _app(models=("a", "b"), capacity=1)
        a_started = asyncio.Event()
        a_release = asyncio.Event()
        route_calls = {"a": 0, "b": 0}

        @app.post("/v1/chat/completions")
        async def chat(request: Request):
            model = request.state.prepared_inference_request.canonical.model
            route_calls[model] += 1
            if model == "a":
                a_started.set()
                await a_release.wait()
            return JSONResponse({"model": model})

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            a_request = asyncio.create_task(client.post("/v1/chat/completions", json=_request_payload("a")))
            await asyncio.wait_for(a_started.wait(), timeout=1)
            b_response = await asyncio.wait_for(
                client.post("/v1/chat/completions", json=_request_payload("b")),
                timeout=1,
            )
            assert b_response.status_code == 200
            assert route_calls == {"a": 1, "b": 1}
            a_release.set()
            assert (await a_request).status_code == 200

    asyncio.run(scenario())


def test_global_only_governor_bounds_two_resident_models():
    async def scenario():
        app, _ = _app(
            models=("a", "b"),
            capacity=None,
            global_max_running=1,
            global_queue_capacity=2,
        )
        a_started = asyncio.Event()
        a_release = asyncio.Event()
        route_calls = {"a": 0, "b": 0}

        @app.post("/v1/chat/completions")
        async def chat(request: Request):
            model = request.state.prepared_inference_request.canonical.model
            route_calls[model] += 1
            if model == "a":
                a_started.set()
                await a_release.wait()
            return JSONResponse({"model": model})

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            a_request = asyncio.create_task(client.post("/v1/chat/completions", json=_request_payload("a")))
            await asyncio.wait_for(a_started.wait(), timeout=1)
            b_request = asyncio.create_task(client.post("/v1/chat/completions", json=_request_payload("b")))
            await asyncio.sleep(0.02)

            assert route_calls == {"a": 1, "b": 0}
            assert app.state.runtime_gate_registry is None
            assert app.state.global_execution_governor.snapshot().queued == 1

            a_release.set()
            a_response, b_response = await asyncio.gather(a_request, b_request)
            assert a_response.status_code == 200
            assert b_response.status_code == 200
            assert route_calls == {"a": 1, "b": 1}
            assert float(b_response.headers["x-local-llm-global-wait-ms"]) > 0

        snapshot = app.state.global_execution_governor.snapshot()
        assert snapshot.inflight == 0
        assert snapshot.queued == 0

    asyncio.run(scenario())


def test_global_wait_uses_existing_admission_timeout_contract():
    async def scenario():
        app, _ = _app(
            models=("a", "b"),
            capacity=None,
            global_max_running=1,
            global_queue_capacity=2,
        )
        started = asyncio.Event()
        release = asyncio.Event()
        route_calls = {"a": 0, "b": 0}

        @app.post("/v1/chat/completions")
        async def chat(request: Request):
            model = request.state.prepared_inference_request.canonical.model
            route_calls[model] += 1
            if model == "a":
                started.set()
                await release.wait()
            return JSONResponse({"model": model})

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            first = asyncio.create_task(client.post("/v1/chat/completions", json=_request_payload("a")))
            await asyncio.wait_for(started.wait(), timeout=1)
            response = await client.post(
                "/v1/chat/completions",
                json=_request_payload("b"),
                headers={"x-local-llm-queue-timeout-ms": "10"},
            )
            assert response.status_code == 408
            assert response.json()["detail"]["code"] == "timeout"
            assert route_calls == {"a": 1, "b": 0}
            release.set()
            assert (await first).status_code == 200

        assert app.state.global_execution_governor.snapshot().queued == 0

    asyncio.run(scenario())


def test_cancelling_global_wait_does_not_leak_execution_slot():
    async def scenario():
        app, _ = _app(
            models=("a", "b"),
            capacity=None,
            global_max_running=1,
            global_queue_capacity=2,
        )
        started = asyncio.Event()
        release = asyncio.Event()

        @app.post("/v1/chat/completions")
        async def chat(request: Request):
            model = request.state.prepared_inference_request.canonical.model
            if model == "a":
                started.set()
                await release.wait()
            return JSONResponse({"model": model})

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            first = asyncio.create_task(client.post("/v1/chat/completions", json=_request_payload("a")))
            await asyncio.wait_for(started.wait(), timeout=1)
            waiting = asyncio.create_task(client.post("/v1/chat/completions", json=_request_payload("b")))
            while app.state.global_execution_governor.snapshot().queued != 1:
                await asyncio.sleep(0.002)
            waiting.cancel()
            try:
                await waiting
            except asyncio.CancelledError:
                pass
            assert app.state.global_execution_governor.snapshot().queued == 0
            assert app.state.global_execution_governor.snapshot().inflight == 1
            release.set()
            assert (await first).status_code == 200

        assert app.state.global_execution_governor.snapshot().inflight == 0

    asyncio.run(scenario())


def test_invalid_timeout_header_fails_before_route():
    async def scenario():
        app, _ = _app(capacity=1)
        route_calls = 0

        @app.post("/v1/chat/completions")
        async def chat():
            nonlocal route_calls
            route_calls += 1
            return JSONResponse({"ok": True})

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/chat/completions",
                json=_request_payload("demo"),
                headers={"x-local-llm-queue-timeout-ms": "never"},
            )
            assert response.status_code == 400
            assert response.json()["detail"]["code"] == "invalid_request"
            assert route_calls == 0

    asyncio.run(scenario())


def test_streaming_body_holds_gate_until_iterator_finishes_and_then_prunes():
    async def scenario():
        gate = AsyncRuntimeGate(capacity=1, max_running=1)
        request = InferenceRequest(task=TaskType.CHAT, model="demo", input_text="hello", stream=True)
        await gate.acquire("stream", request)
        continue_stream = asyncio.Event()

        async def source():
            yield b"first"
            await continue_stream.wait()
            yield b"second"

        wrapped = _hold_gate_for_stream(source(), gate=gate, request_id="stream")
        assert await wrapped.__anext__() == b"first"
        assert (await gate.snapshot()).inflight == 1
        continue_stream.set()
        assert await wrapped.__anext__() == b"second"
        try:
            await wrapped.__anext__()
        except StopAsyncIteration:
            pass
        snapshot = await gate.snapshot()
        assert snapshot.inflight == 0
        assert snapshot.requests == ()

    asyncio.run(scenario())


def test_streaming_body_holds_global_permit_until_iterator_finishes():
    async def scenario():
        governor = GlobalExecutionGovernor(max_running=1, queue_capacity=1)
        governor.acquire("demo", "global-stream", runtime_max_running=1)
        continue_stream = asyncio.Event()

        async def source():
            yield b"first"
            await continue_stream.wait()
            yield b"second"

        wrapped = _hold_gate_for_stream(
            source(),
            gate=None,
            request_id=None,
            governor=governor,
            global_request_id="global-stream",
        )
        assert await wrapped.__anext__() == b"first"
        assert governor.snapshot().inflight == 1
        continue_stream.set()
        assert await wrapped.__anext__() == b"second"
        try:
            await wrapped.__anext__()
        except StopAsyncIteration:
            pass
        assert governor.snapshot().inflight == 0

    asyncio.run(scenario())


def test_invalid_workload_class_fails_before_route():
    async def scenario():
        app, _ = _app(capacity=1)
        route_calls = 0

        @app.post("/v1/chat/completions")
        async def chat():
            nonlocal route_calls
            route_calls += 1
            return JSONResponse({"ok": True})

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/chat/completions",
                json=_request_payload("demo"),
                headers={"x-local-llm-workload-class": "urgent"},
            )
            assert response.status_code == 400
            assert response.json()["detail"]["code"] == "invalid_request"
            assert route_calls == 0

    asyncio.run(scenario())


def test_http_workload_class_prioritizes_interactive_request_globally():
    async def scenario():
        app, _ = _app(
            models=("a", "b", "c"),
            capacity=None,
            global_max_running=1,
            global_queue_capacity=3,
        )
        first_started = asyncio.Event()
        first_release = asyncio.Event()
        execution_order: list[tuple[str, str]] = []

        @app.post("/v1/chat/completions")
        async def chat(request: Request):
            model = request.state.prepared_inference_request.canonical.model
            workload = request.state.scheduler_workload_class
            execution_order.append((model, workload))
            if model == "a":
                first_started.set()
                await first_release.wait()
            return JSONResponse({"model": model})

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            first = asyncio.create_task(
                client.post("/v1/chat/completions", json=_request_payload("a"))
            )
            await asyncio.wait_for(first_started.wait(), timeout=1)

            background = asyncio.create_task(
                client.post(
                    "/v1/chat/completions",
                    json=_request_payload("b"),
                    headers={"x-local-llm-workload-class": "background"},
                )
            )
            while app.state.global_execution_governor.snapshot().queued != 1:
                await asyncio.sleep(0.002)

            interactive = asyncio.create_task(
                client.post(
                    "/v1/chat/completions",
                    json=_request_payload("c"),
                    headers={"x-local-llm-workload-class": "interactive"},
                )
            )
            while app.state.global_execution_governor.snapshot().queued != 2:
                await asyncio.sleep(0.002)

            first_release.set()
            first_response, background_response, interactive_response = await asyncio.gather(
                first,
                background,
                interactive,
            )

        assert [first_response.status_code, background_response.status_code, interactive_response.status_code] == [200, 200, 200]
        assert execution_order == [
            ("a", "standard"),
            ("c", "interactive"),
            ("b", "background"),
        ]
        assert interactive_response.headers["x-local-llm-workload-class"] == "interactive"
        assert background_response.headers["x-local-llm-workload-class"] == "background"

    asyncio.run(scenario())
