from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

from local_llm_server.control_plane_api import install_product_api
from local_llm_server.product_runtime_manager import ProductRuntimeManager
from local_llm_server.resource_manager import ResourceManager
from local_llm_server.resource_policy import ResourcePolicySettings
from local_llm_server.runtime import ResourceAdmissionError
from local_llm_server.server import ServerSettings, create_app


class _Engine:
    backend = "fake"

    def __init__(self):
        self.calls = 0
        self.closed = False

    def complete(self, payload):
        self.calls += 1
        return {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 2, "completion_tokens": 1},
        }

    def close(self):
        self.closed = True


def _cfg(key: str, estimate: int) -> dict:
    return {
        "model": key,
        "model_id": f"org/{key}",
        "model_path": f"/models/{key}.gguf",
        "backend": "fake",
        "resource_estimate_bytes": estimate,
        "modalities": ["text"],
        "max_concurrent_requests": 1,
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


def _admin_app(tmp_path, manager, settings):
    application = create_app(
        manager,
        settings=ServerSettings(enable_admin_api=True),
    )
    application.state.resource_policy_settings = settings
    install_product_api(application, evaluation_root=tmp_path / "eval")
    return TestClient(application)


def test_product_resource_lifecycle_matches_api_and_returns_to_healthy_cold_state(
    monkeypatch,
    tmp_path,
):
    settings = ResourcePolicySettings(memory_limit_bytes=1_000, headroom_bytes=100)
    resources = ResourceManager(settings.budget)
    manager = ProductRuntimeManager(default_model="model", resource_manager=resources)
    engine = _Engine()
    monkeypatch.setattr(
        "local_llm_server.config.build_config",
        lambda **_kwargs: _cfg("model", 400),
    )
    monkeypatch.setattr("local_llm_server.engine.load_llm", lambda _cfg: engine)

    # Admission starts from a genuinely empty ledger. The legacy app factory
    # still requires a resident default during construction, so the HTTP stack
    # is attached after the product load and remains live through the unload.
    assert resources.snapshot() == ()

    runtime, loaded = manager.load("model")
    assert loaded is True
    assert runtime.resource_reservation_id is not None

    client = _admin_app(tmp_path, manager, settings)
    committed = client.get("/api/v1/resources").json()
    expected_policy = {
        "enabled": True,
        "memory_limit_bytes": 1_000,
        "headroom_bytes": 100,
        "usable_budget_bytes": 900,
        "committed_bytes": 400,
        "reserved_bytes": 0,
        "remaining_bytes": 500,
        "reservation_count": 1,
        "policy_state": "configured",
    }
    assert {key: committed[key] for key in expected_policy} == expected_policy
    assert committed["observation"]["schema_version"] == "1"

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "model",
            "messages": [{"role": "user", "content": "hello"}],
            "temperature": 0.0,
        },
    )
    assert response.status_code == 200
    assert engine.calls == 1
    assert client.get("/api/v1/resources").json()["committed_bytes"] == 400

    unload_response = client.delete("/api/v1/models/model")
    assert unload_response.status_code == 200, unload_response.text
    assert engine.closed is True
    assert resources.snapshot() == ()

    released = client.get("/api/v1/resources").json()
    assert released["committed_bytes"] == 0
    assert released["reserved_bytes"] == 0
    assert released["remaining_bytes"] == 900
    assert released["reservation_count"] == 0

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["ok"] is True
    assert health.json()["state"] == "cold"
    assert health.json()["resident"] is False


def test_product_rejects_insufficient_budget_before_backend_load(monkeypatch):
    settings = ResourcePolicySettings(memory_limit_bytes=500, headroom_bytes=100)
    resources = ResourceManager(settings.budget)
    manager = ProductRuntimeManager(default_model="too-big", resource_manager=resources)
    backend_calls = []
    monkeypatch.setattr(
        "local_llm_server.config.build_config",
        lambda **_kwargs: _cfg("too-big", 401),
    )
    monkeypatch.setattr(
        "local_llm_server.engine.load_llm",
        lambda cfg: backend_calls.append(cfg) or _Engine(),
    )

    with pytest.raises(ResourceAdmissionError) as exc_info:
        manager.load("too-big")

    assert exc_info.value.result.decision.value == "reject"
    assert backend_calls == []
    assert resources.snapshot() == ()
    assert manager.cold is True


def test_headroom_is_subtracted_exactly_once_from_product_budget():
    settings = ResourcePolicySettings(memory_limit_bytes=1_000, headroom_bytes=100)
    resources = ResourceManager(settings.budget)

    admitted = resources.reserve("runtime:full-usable-budget", 900)
    assert admitted.decision.value == "admit"
    resources.commit("runtime:full-usable-budget")

    snapshot = resources.reserve("runtime:one-byte-too-many", 1)
    assert snapshot.decision.value == "reject"
    assert snapshot.usable_budget_bytes == 900
    assert snapshot.committed_bytes == 900


def test_disabled_product_policy_is_non_enforcing_and_reports_no_fake_budget(tmp_path):
    settings = ResourcePolicySettings()
    manager = ProductRuntimeManager(default_model="model", resource_manager=None)
    manager.add(_cfg("model", 400), _Engine())
    client = _admin_app(tmp_path, manager, settings)

    payload = client.get("/api/v1/resources").json()

    assert payload["enabled"] is False
    assert payload["policy_state"] == "disabled"
    assert payload["memory_limit_bytes"] is None
    assert payload["usable_budget_bytes"] is None
    assert payload["remaining_bytes"] is None
    assert payload["committed_bytes"] == 0
    assert payload["reserved_bytes"] == 0
    assert payload["reservation_count"] == 0
