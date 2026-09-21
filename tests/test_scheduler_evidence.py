from __future__ import annotations

import asyncio
import threading
import time

from fastapi.testclient import TestClient

from local_llm_server.control_plane_api import install_product_api
from local_llm_server.core.contracts import InferenceRequest, TaskType
from local_llm_server.request_scheduler import RuntimeGateRegistry, install_request_scheduler
from local_llm_server.runtime import ModelRuntimeManager
from local_llm_server.scheduler_evidence import scheduler_evidence_payload
from local_llm_server.scheduler_policy import RequestSchedulerSettings
from local_llm_server.server import ServerSettings, create_app


class _Engine:
    backend = "fake"

    def close(self):
        pass


def _manager():
    manager = ModelRuntimeManager(default_model="demo")
    manager.add(
        {
            "model": "demo",
            "model_id": "org/demo",
            "backend": "fake",
            "modalities": ["text"],
            "max_concurrent_requests": 1,
        },
        _Engine(),
    )
    return manager


def test_disabled_scheduler_evidence_is_explicit_not_fake_zero():
    async def scenario():
        manager = _manager()
        app = create_app(manager, settings=ServerSettings(enable_admin_api=True))
        app.state.request_scheduler_settings = RequestSchedulerSettings()
        app.state.runtime_gate_registry = None
        app.state.global_execution_governor = None

        payload = await scheduler_evidence_payload(app)

        assert payload["policy"]["enabled"] is False
        assert payload["policy"]["queue_capacity"] is None
        assert payload["runtimes"] == []
        assert payload["global"]["enabled"] is False
        assert payload["global"]["inflight"] is None
        assert payload["global"]["queued"] is None
        assert payload["global"]["workloads"] == []

    asyncio.run(scenario())


def test_scheduler_evidence_reports_aggregate_running_and_queued_without_request_ids():
    async def scenario():
        manager = _manager()
        settings = RequestSchedulerSettings(queue_capacity=2)
        registry = RuntimeGateRegistry(settings)
        app = create_app(manager, settings=ServerSettings(enable_admin_api=True))
        app.state.request_scheduler_settings = settings
        app.state.runtime_gate_registry = registry
        runtime = manager.resolve("demo")
        gate = registry.gate_for(runtime)
        request = InferenceRequest(task=TaskType.CHAT, model="demo", input_text="secret prompt")

        await gate.acquire("private-running-id", request)
        queued = asyncio.create_task(gate.acquire("private-queued-id", request))
        await asyncio.sleep(0)

        payload = await scheduler_evidence_payload(app)
        runtime_payload = payload["runtimes"][0]
        assert runtime_payload["inflight"] == 1
        assert runtime_payload["queued"] == 1
        assert runtime_payload["max_running"] == 1
        assert runtime_payload["queue_capacity"] == 2
        assert runtime_payload["queued_by_workload"] == {"standard": 1}
        assert runtime_payload["running_by_workload"] == {"standard": 1}
        rendered = str(payload)
        assert "private-running-id" not in rendered
        assert "private-queued-id" not in rendered
        assert "secret prompt" not in rendered

        queued.cancel()
        try:
            await queued
        except asyncio.CancelledError:
            pass
        await gate.release("private-running-id")

    asyncio.run(scenario())


def test_global_governor_evidence_is_aggregate_and_privacy_safe():
    async def scenario():
        manager = _manager()
        settings = RequestSchedulerSettings(
            global_max_running=1,
            global_queue_capacity=2,
        )
        app = create_app(manager, settings=ServerSettings(enable_admin_api=True))
        install_request_scheduler(app, settings=settings)
        governor = app.state.global_execution_governor
        governor.acquire("demo", "private-global-running", runtime_max_running=1)

        acquired = threading.Event()

        def queued() -> None:
            governor.acquire("demo", "private-global-queued", runtime_max_running=1)
            acquired.set()

        waiter = threading.Thread(target=queued)
        waiter.start()
        deadline = time.monotonic() + 1
        while governor.snapshot().queued != 1 and time.monotonic() < deadline:
            await asyncio.sleep(0.002)

        payload = await scheduler_evidence_payload(app)
        assert payload["runtimes"] == []
        assert payload["global"]["enabled"] is True
        assert payload["global"]["inflight"] == 1
        assert payload["global"]["queued"] == 1
        assert payload["global"]["fairness"] == "priority_aging_runtime_round_robin"
        assert payload["global"]["runtimes"] == [
            {"runtime_key": "demo", "queued": 1, "running": 1}
        ]
        assert payload["global"]["workloads"] == [
            {"workload_class": "standard", "queued": 1, "running": 1}
        ]
        rendered = str(payload)
        assert "private-global-running" not in rendered
        assert "private-global-queued" not in rendered

        governor.release("private-global-running")
        assert acquired.wait(timeout=1)
        governor.release("private-global-queued")
        waiter.join(timeout=1)
        assert not waiter.is_alive()

    asyncio.run(scenario())


def test_admin_control_plane_exposes_scheduler_and_global_governor_source(tmp_path):
    manager = _manager()
    app = create_app(manager, settings=ServerSettings(enable_admin_api=True))
    install_request_scheduler(
        app,
        settings=RequestSchedulerSettings(
            queue_capacity=3,
            default_queue_timeout_ms=250,
            global_max_running=2,
            global_queue_capacity=5,
        ),
    )
    install_product_api(app, evaluation_root=tmp_path / "evaluations")

    response = TestClient(app).get("/api/v1/scheduler")

    assert response.status_code == 200
    payload = response.json()
    assert payload["policy"]["enabled"] is True
    assert payload["policy"]["runtime_queue_enabled"] is True
    assert payload["policy"]["queue_capacity"] == 3
    assert payload["policy"]["global_governor_enabled"] is True
    assert payload["policy"]["global_max_running"] == 2
    assert payload["policy"]["global_queue_capacity"] == 5
    assert payload["policy"]["default_queue_timeout_ms"] == 250
    assert payload["policy"]["timeout_scope"] == "pre_execution_admission_wait_only"
    assert payload["policy"]["global_fairness"] == "priority_aging_runtime_round_robin"
    assert payload["policy"]["workload_default"] == "standard"
    assert payload["global"]["enabled"] is True
    assert payload["global"]["max_running"] == 2
    assert payload["runtimes"][0]["runtime_key"] == "demo"
