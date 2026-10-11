"""Product API accepts M8 planning requests without resident backends or mutation."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from local_llm_server.control_plane_api import install_product_api
from local_llm_server.resource_manager import ResourceManager
from local_llm_server.resource_policy import ResourcePolicySettings
from local_llm_server.resources import ResourceBudget
from local_llm_server.runtime import ModelRuntimeManager
from local_llm_server.server import ServerSettings, create_app


def _profile(task="chat", model="text", residency="warm"):
    return {
        "schema_version": "korgis-application-profile-v1",
        "application_id": "closedroom",
        "profile_id": "meeting",
        "roles": {
            "analysis": {"model": model, "task": task, "residency": residency},
        },
    }


def _client(tmp_path: Path, *, admin: bool, monkeypatch, memory_budget=1000):
    from local_llm_server import config, registry

    model_entry = {
        "tasks": ["chat"],
        "input_modalities": ["text"],
        "output_modalities": ["text"],
        "features": [],
        "model_id": "org/text",
        "size_gb": 0.00001,
    }
    monkeypatch.setattr(registry, "load_registry", lambda: {"models": {"text": model_entry}})
    monkeypatch.setattr(config, "build_config", lambda model, no_download: {
        "model": model,
        "model_path": "/home/private/document",
        "resource_model_weights_bytes": 200,
        "resource_backend_overhead_bytes": 20,
        "resource_context_cache_bytes": 10,
        "resource_safety_margin_bytes": 5,
        "resource_request_estimate_bytes": 90,
    })
    resource = ResourceManager(ResourceBudget(limit_bytes=memory_budget, headroom_bytes=100))
    manager = ModelRuntimeManager(default_model=None, resource_manager=resource)
    app = create_app(manager, settings=ServerSettings(enable_admin_api=admin))
    app.state.resource_policy_settings = ResourcePolicySettings(
        memory_limit_bytes=memory_budget, headroom_bytes=100,
    )
    install_product_api(app, evaluation_root=tmp_path / "evaluations")
    return TestClient(app), manager, resource


def test_profiles_are_admin_only(tmp_path, monkeypatch):
    client, _, _ = _client(tmp_path, admin=False, monkeypatch=monkeypatch)
    assert client.post("/api/v1/applications/profiles/validate", json=_profile()).status_code == 404
    assert client.post("/api/v1/applications/profiles/plan", json=_profile()).status_code == 404


def test_validate_plan_exact_model_zero_resident_no_mutation(tmp_path, monkeypatch):
    client, runtime, resource = _client(tmp_path, admin=True, monkeypatch=monkeypatch)
    before = resource.snapshot()
    validation = client.post("/api/v1/applications/profiles/validate", json=_profile())
    assert validation.status_code == 200
    assert validation.json()["valid"] is True
    assert validation.json()["roles"][0]["model"] == "text"
    plan = client.post("/api/v1/applications/profiles/plan", json=_profile())
    assert plan.status_code == 200
    result = plan.json()
    assert result["decision"] == "plausible"
    assert result["models"][0]["would_load"] is True
    assert result["planned"]["additional_estimated_bytes"] == 235
    assert result["mutated_runtime"] is False
    assert resource.snapshot() == before
    assert runtime.list() == ()
    assert "/home/private/" not in str(result)


def test_invalid_profile_and_unknown_capability_fail_before_planning(tmp_path, monkeypatch):
    client, _, _ = _client(tmp_path, admin=True, monkeypatch=monkeypatch)
    assert client.post(
        "/api/v1/applications/profiles/plan",
        json=_profile(task="transcription"),
    ).status_code == 422
    assert client.post(
        "/api/v1/applications/profiles/plan",
        json=_profile(model="unknown"),
    ).status_code == 422
    invalid = _profile()
    invalid["exfiltrate"] = True
    assert client.post(
        "/api/v1/applications/profiles/validate",
        json=invalid,
    ).status_code == 422
