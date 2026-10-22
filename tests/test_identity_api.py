from __future__ import annotations

import json

from fastapi.testclient import TestClient

from local_llm_server.product_composition import install_product_http_stack
from local_llm_server.runtime import ModelRuntimeManager
from local_llm_server.runtime_identity_capture import capture_verified_runtime_identity
from local_llm_server.server import ServerSettings, create_app


class _Engine:
    backend = "fake"

    def close(self):
        pass


def _runtime_config(key: str, *, verified: bool) -> dict[str, object]:
    cfg: dict[str, object] = {
        "model": key,
        "model_id": f"org/{key}",
        "model_path": f"/private/models/{key}.gguf",
        "download_url": "https://secret.example/model.gguf",
        "backend": "fake",
        "backend_version": "1.2.3",
        "quantization": "Q4_K_M",
        "artifact_revision": "rev-7",
        "ctx_size": 4096,
        "n_threads": 8,
        "max_concurrent_requests": 2,
        "modalities": ["text"],
    }
    if verified:
        cfg["artifact_sha256"] = "a" * 64
    return cfg


def _app(*, verified: bool):
    manager = ModelRuntimeManager(default_model="demo")
    runtime = manager.add(_runtime_config("demo", verified=verified), _Engine())
    if verified:
        assert capture_verified_runtime_identity(runtime) is not None
    application = create_app(manager, settings=ServerSettings(enable_admin_api=False))
    install_product_http_stack(application)
    return application


def test_identity_endpoint_is_public_versioned_and_path_free():
    app = _app(verified=True)

    with TestClient(app) as client:
        response = client.get("/v1/runtime/identity")

    assert response.status_code == 200
    payload = response.json()
    assert payload["protocol_version"] == "local-llm-identity-v1"
    assert payload["server"]["name"] == "local-llm-server"
    identity = payload["models"]["demo"]
    assert identity["model"] == {
        "id": "org/demo",
        "revision": "rev-7",
        "artifact_digest": f"sha256:{'a' * 64}",
        "artifact_key": identity["model"]["artifact_key"],
        "quantization": "Q4_K_M",
        "verification": "verified",
    }
    assert identity["runtime"]["name"] == "fake"
    assert identity["runtime"]["version"] == "1.2.3"
    assert identity["runtime"]["config"]["ctx_size"] == 4096
    assert identity["runtime"]["config_digest"]
    assert identity["runtime"]["fingerprint"]
    assert identity["runtime"]["evidence_grade"] == "verified"

    serialized = json.dumps(payload)
    assert "/private/models" not in serialized
    assert "secret.example" not in serialized
    assert "model_path" not in serialized
    assert "download_url" not in serialized


def test_identity_endpoint_preserves_unknown_artifact_evidence():
    app = _app(verified=False)

    with TestClient(app) as client:
        identity = client.get("/v1/runtime/identity").json()["models"]["demo"]

    assert identity["model"]["artifact_digest"] is None
    assert identity["model"]["artifact_key"] is None
    assert identity["model"]["verification"] == "available_unverified"
    assert identity["runtime"]["version"] == "1.2.3"
    assert identity["runtime"]["fingerprint"] is None
    assert identity["runtime"]["evidence_grade"] == "partial"


def test_identity_endpoint_reports_each_resident_runtime_and_default():
    manager = ModelRuntimeManager(default_model="first")
    manager.add(_runtime_config("first", verified=False), _Engine())
    manager.add(_runtime_config("second", verified=False), _Engine())
    app = create_app(manager, settings=ServerSettings(enable_admin_api=False))
    install_product_http_stack(app)

    with TestClient(app) as client:
        payload = client.get("/v1/runtime/identity").json()

    assert payload["default_model"] == "first"
    assert set(payload["models"]) == {"first", "second"}
    assert payload["models"]["second"]["model"]["id"] == "org/second"


def test_public_catalog_includes_downloaded_qwen_without_exposing_paths(monkeypatch):
    from local_llm_server import list_models as original
    import local_llm_server

    def catalog():
        return [
            {"key": "qwen3.5-4b-q4km", "model_id": "Qwen/Qwen3.5-4B",
             "downloaded": True, "backend": "llama_server",
             "path": "/private/qwen.gguf", "url": "https://private.example"},
            {"key": "qwen3.5-9b-q4km", "model_id": "Qwen/Qwen3.5-9B",
             "downloaded": False, "backend": "llama_server"},
            {"key": "qwen-image-2.1", "model_id": "Qwen/Qwen-Image-2.1",
             "downloaded": True, "backend": "diffusers_image"},
        ]

    monkeypatch.setattr(local_llm_server, "list_models", catalog)
    app = _app(verified=False)
    with TestClient(app) as client:
        response = client.get("/v1/runtime/catalog")
        assert client.post("/api/v1/models/load", json={"model": "qwen3.5-4b-q4km"}).status_code == 404
    assert response.status_code == 200
    payload = response.json()
    assert payload["protocol_version"] == "local-llm-catalog-v1"
    assert payload["models"] == [
        {"startup_key": "qwen3.5-4b-q4km", "model_id": "Qwen/Qwen3.5-4B",
         "downloaded": True, "resident": False},
        {"startup_key": "qwen3.5-9b-q4km", "model_id": "Qwen/Qwen3.5-9B",
         "downloaded": False, "resident": False},
    ]
    assert "/private" not in json.dumps(payload)
    assert "private.example" not in json.dumps(payload)
    monkeypatch.setattr(local_llm_server, "list_models", original)
