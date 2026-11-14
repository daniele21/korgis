from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from local_llm_server.control_plane_api import install_product_api
from local_llm_server.resource_manager import ResourceManager
from local_llm_server.resource_policy import ResourcePolicySettings
from local_llm_server.resources import ResourceBudget
from local_llm_server.runtime import ModelRuntimeManager
from local_llm_server.runtime_evidence import RuntimeIdentitySnapshot, attach_runtime_identity
from local_llm_server.server import ServerSettings, create_app


class _TextEngine:
    backend = "fake_text"

    def __init__(self):
        self.calls = 0

    def complete(self, payload):
        self.calls += 1
        return {
            "choices": [{"message": {"role": "assistant", "content": "wrong"}}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 1},
        }

    def close(self):
        pass


class _AsrEngine:
    backend = "fake_asr"

    def __init__(self):
        self.calls = 0

    def transcribe(self, payload):
        self.calls += 1
        return {"text": "transcribed", "language": "en", "duration": 2.5}

    def close(self):
        pass


def _text_cfg():
    return {
        "model": "text",
        "model_id": "org/text",
        "model_path": "/private/text.gguf",
        "backend": "fake_text",
        "modalities": ["text"],
        "max_concurrent_requests": 1,
    }


def _asr_cfg():
    return {
        "model": "asr",
        "model_id": "org/asr",
        "model_path": "/private/asr",
        "backend": "fake_asr",
        "tasks": ["transcription"],
        "input_modalities": ["audio"],
        "output_modalities": ["text"],
        "features": ["streaming"],
        "modalities": ["audio"],
        "max_concurrent_requests": 1,
    }


def _app(tmp_path: Path, *, admin: bool):
    resource_manager = ResourceManager(ResourceBudget(limit_bytes=1_000, headroom_bytes=100))
    manager = ModelRuntimeManager(default_model="text", resource_manager=resource_manager)
    text_engine = _TextEngine()
    asr_engine = _AsrEngine()
    text_runtime = manager.add(_text_cfg(), text_engine)
    manager.add(_asr_cfg(), asr_engine)
    attach_runtime_identity(
        text_runtime,
        RuntimeIdentitySnapshot("a" * 64, {"artifact_key": "b" * 64}, 1.0),
    )

    application = create_app(
        manager,
        settings=ServerSettings(enable_admin_api=admin),
    )
    application.state.resource_policy_settings = ResourcePolicySettings(
        memory_limit_bytes=1_000,
        headroom_bytes=100,
    )
    install_product_api(application, evaluation_root=tmp_path / "evaluations")
    return application, manager, text_engine, asr_engine


def test_transcription_route_is_public_and_uses_explicit_asr_runtime(tmp_path: Path):
    application, _, _, asr_engine = _app(tmp_path, admin=False)
    response = TestClient(application).post(
        "/v1/audio/transcriptions",
        data={"model": "asr", "language": "en"},
        files={"file": ("meeting.wav", b"RIFFaudio", "audio/wav")},
    )

    assert response.status_code == 200
    assert response.json()["text"] == "transcribed"
    assert response.json()["language"] == "en"
    assert asr_engine.calls == 1


def test_control_plane_routes_require_admin_api(tmp_path: Path):
    application, _, _, _ = _app(tmp_path, admin=False)
    client = TestClient(application)
    assert client.get("/api/v1/resources").status_code == 404
    assert client.get("/api/v1/evidence").status_code == 404
    assert client.get("/api/v1/evaluation/test-sets").status_code == 404
    assert client.get("/api/v1/evaluation/history").status_code == 404


def test_resource_and_evidence_routes_are_public_safe_when_admin_enabled(tmp_path: Path):
    application, manager, _, _ = _app(tmp_path, admin=True)
    reservation = manager.resource_manager.reserve("runtime:test", 200)
    assert reservation.decision.value == "admit"
    manager.resource_manager.commit("runtime:test")

    client = TestClient(application)
    resources = client.get("/api/v1/resources")
    assert resources.status_code == 200
    assert resources.json()["committed_bytes"] == 200
    assert resources.json()["usable_budget_bytes"] == 900
    observation = resources.json()["observation"]
    assert observation["schema_version"] == "1"
    assert observation["runtimes"][0]["runtime_key"] == "text"
    assert "pid" not in observation["runtimes"][0]

    evidence = client.get("/api/v1/evidence")
    assert evidence.status_code == 200
    payload = evidence.json()
    assert payload["runtime_count"] == 2
    rendered = str(payload)
    assert "/private/" not in rendered
    assert payload["runtimes"][0]["identity"]["fingerprint"] == "a" * 64


def test_evaluation_routes_list_dataset_run_ten_samples_and_persist(tmp_path: Path):
    application, _, text_engine, _ = _app(tmp_path, admin=True)
    client = TestClient(application)

    test_sets = client.get("/api/v1/evaluation/test-sets")
    assert test_sets.status_code == 200
    assert test_sets.json()["test_sets"][0]["id"] == "general-purpose"
    assert test_sets.json()["test_sets"][0]["sample_count"] == 20

    run = client.post(
        "/api/v1/evaluation/runs",
        json={"model": "text", "sample_count": 10, "seed": 7},
    )
    assert run.status_code == 200
    payload = run.json()
    assert payload["evidence_grade"] is True
    assert payload["report"]["complete"] is True
    assert len(payload["report"]["results"]) == 10
    assert payload["report"]["manifest"]["content_retained"] is True
    assert all("content" in result for result in payload["report"]["results"])
    assert text_engine.calls == 10
    assert "persisted_path" not in payload

    run_ids = client.get("/api/v1/evaluation/runs")
    assert run_ids.status_code == 200
    assert run_ids.json()["run_ids"] == [payload["report"]["manifest"]["run_id"]]

    history = client.get("/api/v1/evaluation/history")
    assert history.status_code == 200
    assert history.json()["runs"][0]["run_id"] == payload["report"]["manifest"]["run_id"]
    assert history.json()["runs"][0]["runtime_fingerprint"] == "a" * 64

    persisted = client.get(
        f"/api/v1/evaluation/history/{payload['report']['manifest']['run_id']}"
    )
    assert persisted.status_code == 200
    assert all("output" in result["content"] for result in persisted.json()["results"])


def test_evaluation_can_exclude_model_output_from_local_history(tmp_path: Path):
    application, _, _, _ = _app(tmp_path, admin=True)
    client = TestClient(application)

    run = client.post(
        "/api/v1/evaluation/runs",
        json={"model": "text", "sample_count": 10, "retain_content": False},
    )

    assert run.status_code == 200
    report = run.json()["report"]
    run_id = report["manifest"]["run_id"]
    persisted = client.get(f"/api/v1/evaluation/history/{run_id}").json()
    assert report["manifest"]["content_retained"] is False
    assert all(item["content"]["output"] is not None for item in report["results"])
    assert persisted["manifest"]["content_retained"] is False
    assert all(item["content"]["input"] for item in persisted["results"])
    assert all(item["content"]["expected"] for item in persisted["results"])
    assert all("output" not in item["content"] for item in persisted["results"])


def test_evaluation_history_comparison_is_attribution_safe_for_matched_runs(tmp_path: Path):
    application, _, text_engine, _ = _app(tmp_path, admin=True)
    client = TestClient(application)
    first = client.post(
        "/api/v1/evaluation/runs",
        json={"model": "text", "sample_count": 10, "seed": 11},
    )
    second = client.post(
        "/api/v1/evaluation/runs",
        json={"model": "text", "sample_count": 10, "seed": 11},
    )
    assert first.status_code == 200
    assert second.status_code == 200
    assert text_engine.calls == 20

    baseline = first.json()["report"]["manifest"]["run_id"]
    candidate = second.json()["report"]["manifest"]["run_id"]
    comparison = client.get(
        "/api/v1/evaluation/history/compare",
        params={"baseline": baseline, "candidate": candidate},
    )
    assert comparison.status_code == 200
    payload = comparison.json()
    assert payload["comparable"] is True
    assert payload["evidence_grade"] is True
    assert payload["attribution_safe"] is True
    assert payload["deltas"]["objective_quality_mean"] == 0

    persisted = client.get(f"/api/v1/evaluation/history/{baseline}")
    assert persisted.status_code == 200
    assert persisted.json()["manifest"]["run_id"] == baseline


def test_evaluation_rejects_non_multiple_sample_count(tmp_path: Path):
    application, _, _, _ = _app(tmp_path, admin=True)
    response = TestClient(application).post(
        "/api/v1/evaluation/runs",
        json={"model": "text", "sample_count": 15},
    )
    assert response.status_code == 400
    assert "multiple of 10" in str(response.json()["detail"])
