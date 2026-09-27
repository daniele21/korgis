from __future__ import annotations

import asyncio

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from local_llm_server.core import TaskType
from local_llm_server.request_middleware import install_request_policy
from local_llm_server.request_resource_admission import install_request_resource_admission
from local_llm_server.request_scheduler import install_request_scheduler
from local_llm_server.resource_manager import ReservationKind, ResourceManager
from local_llm_server.resources import ResourceBudget
from local_llm_server.runtime import ModelRuntimeManager
from local_llm_server.scheduler_policy import RequestSchedulerSettings


class _ImageEngine:
    backend = "mflux_image"

    def close(self):
        pass


def _cfg(
    *,
    request_bytes: int | None = 60,
    max_concurrent_requests: int = 2,
) -> dict:
    return {
        "model": "image",
        "model_id": "org/image",
        "backend": "mflux_image",
        "modalities": ["text"],
        "tasks": ["image_generation"],
        "input_modalities": ["text"],
        "output_modalities": ["image"],
        "max_concurrent_requests": max_concurrent_requests,
        "image_width": 512,
        "image_height": 512,
        "image_max_pixels": 1048576,
        "image_max_inference_steps": 50,
        "resource_request_estimate_bytes": request_bytes,
    }


def _payload() -> dict:
    return {
        "model": "image",
        "prompt": "A red cube",
        "size": "512x512",
        "seed": 42,
        "num_inference_steps": 12,
    }


def _app(
    *,
    memory_limit: int = 200,
    request_bytes: int | None = 60,
    settings: RequestSchedulerSettings | None = None,
) -> tuple[FastAPI, ModelRuntimeManager, ResourceManager]:
    resources = ResourceManager(ResourceBudget(limit_bytes=memory_limit))
    manager = ModelRuntimeManager(default_model="image", resource_manager=resources)
    manager.add(_cfg(request_bytes=request_bytes), _ImageEngine())
    app = FastAPI()
    app.state.runtime_manager = manager
    install_request_resource_admission(app)
    install_request_scheduler(
        app,
        settings=settings or RequestSchedulerSettings(),
    )
    # Product order: policy runs outermost and prepares the canonical image
    # request before scheduler and transient-resource admission inspect it.
    install_request_policy(app)
    return app, manager, resources


def test_image_requests_use_global_governor_before_transient_memory() -> None:
    async def scenario() -> None:
        app, _, resources = _app(
            memory_limit=200,
            request_bytes=60,
            settings=RequestSchedulerSettings(
                queue_capacity=None,
                global_max_running=1,
                global_queue_capacity=2,
            ),
        )
        first_started = asyncio.Event()
        release_first = asyncio.Event()
        route_calls = 0

        @app.post("/v1/images/generations")
        async def image_route(request: Request):
            nonlocal route_calls
            prepared = request.state.prepared_inference_request
            assert prepared.canonical.task is TaskType.IMAGE_GENERATION
            route_calls += 1
            if route_calls == 1:
                first_started.set()
                await release_first.wait()
            return JSONResponse({"ok": True})

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            first = asyncio.create_task(
                client.post("/v1/images/generations", json=_payload())
            )
            await asyncio.wait_for(first_started.wait(), timeout=1)

            active = resources.snapshot(kind=ReservationKind.TRANSIENT)
            assert len(active) == 1
            assert active[0].accounted_bytes == 60

            second = asyncio.create_task(
                client.post("/v1/images/generations", json=_payload())
            )
            while app.state.global_execution_governor.snapshot().queued != 1:
                await asyncio.sleep(0.002)

            # Queued work must not reserve transient memory.
            assert route_calls == 1
            active = resources.snapshot(kind=ReservationKind.TRANSIENT)
            assert len(active) == 1
            assert active[0].accounted_bytes == 60

            release_first.set()
            first_response, second_response = await asyncio.gather(first, second)

        assert first_response.status_code == 200
        assert second_response.status_code == 200
        assert float(second_response.headers["x-local-llm-global-wait-ms"]) > 0
        assert second_response.headers["x-local-llm-transient-reserved-bytes"] == "60"
        assert route_calls == 2
        assert resources.snapshot() == ()
        governor = app.state.global_execution_governor.snapshot()
        assert governor.inflight == 0
        assert governor.queued == 0

    asyncio.run(scenario())


def test_image_requests_respect_shared_transient_memory_budget() -> None:
    async def scenario() -> None:
        app, _, resources = _app(
            memory_limit=100,
            request_bytes=60,
            settings=RequestSchedulerSettings(),
        )
        first_started = asyncio.Event()
        release_first = asyncio.Event()
        route_calls = 0

        @app.post("/v1/images/generations")
        async def image_route(request: Request):
            nonlocal route_calls
            assert (
                request.state.prepared_inference_request.canonical.task
                is TaskType.IMAGE_GENERATION
            )
            route_calls += 1
            if route_calls == 1:
                first_started.set()
                await release_first.wait()
            return JSONResponse({"ok": True})

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            first = asyncio.create_task(
                client.post("/v1/images/generations", json=_payload())
            )
            await asyncio.wait_for(first_started.wait(), timeout=1)

            rejected = await client.post(
                "/v1/images/generations",
                json=_payload(),
            )
            assert rejected.status_code == 429
            assert rejected.json()["detail"]["code"] == "resource_exhausted"
            assert route_calls == 1

            release_first.set()
            assert (await first).status_code == 200

        assert resources.snapshot() == ()

    asyncio.run(scenario())
