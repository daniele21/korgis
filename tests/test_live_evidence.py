from __future__ import annotations

from local_llm_server.live_evidence import (
    manager_evidence_payload,
    record_runtime_metrics,
    runtime_evidence_payload,
)
from local_llm_server.metrics import (
    CountMetrics,
    DurationMetrics,
    InferenceMetrics,
    ThroughputMetrics,
)
from local_llm_server.runtime import ModelRuntimeManager
from local_llm_server.resource_telemetry import RequestResourceEvidence
from local_llm_server.runtime_evidence import RuntimeIdentitySnapshot, attach_runtime_identity


class _Engine:
    backend = "fake"

    def close(self):
        pass


def _runtime():
    cfg = {
        "model": "demo",
        "model_id": "org/demo",
        "model_path": "/private/models/demo.gguf",
        "backend": "fake",
        "max_concurrent_requests": 2,
        "resource_admission": {
            "decision": "admit",
            "estimate_bytes": 100,
            "usable_budget_bytes": 1000,
            "reason": "test",
        },
    }
    manager = ModelRuntimeManager(default_model="demo")
    runtime = manager.add(cfg, _Engine())
    return manager, runtime


def test_evidence_falls_back_to_truthful_runtime_chunk_metrics():
    _, runtime = _runtime()
    runtime.mark_started(10)
    runtime.mark_generating()
    runtime.record_output("hello")

    payload = runtime_evidence_payload(runtime)

    assert payload["metrics"]["counts"]["output_chunks"] == 1
    assert payload["metrics"]["counts"]["output_tokens"] is None
    assert payload["metrics"]["throughput"]["decode_tokens_per_second"] is None
    assert payload["resource_admission"]["decision"] == "admit"


def test_explicit_canonical_metrics_replace_runtime_fallback_only_when_recorded():
    _, runtime = _runtime()
    metrics = InferenceMetrics(
        durations=DurationMetrics(prompt_prefill_ms=10.0, decode_ms=20.0, total_ms=30.0),
        counts=CountMetrics(input_tokens=5, output_tokens=3, output_chunks=2),
        throughput=ThroughputMetrics(decode_tokens_per_second=150.0),
        sources={"output_tokens": "response.usage.completion_tokens"},
    )
    record_runtime_metrics(runtime, metrics)

    payload = runtime_evidence_payload(runtime)

    assert payload["metrics"]["counts"]["output_tokens"] == 3
    assert payload["metrics"]["durations_ms"]["ttft"] is None
    assert payload["metrics"]["throughput"]["decode_tokens_per_second"] == 150.0


def test_identity_is_exposed_only_from_attached_public_snapshot():
    _, runtime = _runtime()
    attach_runtime_identity(
        runtime,
        RuntimeIdentitySnapshot(
            "a" * 64,
            {
                "artifact_key": "b" * 64,
                "backend": {"implementation": "fake", "version": "1"},
            },
            123.0,
        ),
    )

    payload = runtime_evidence_payload(runtime)

    assert payload["identity"]["fingerprint"] == "a" * 64
    rendered = str(payload)
    assert "/private/models" not in rendered
    assert "last_content" not in rendered


def test_manager_projection_contains_no_request_or_output_content():
    manager, runtime = _runtime()
    runtime.mark_started(10)
    runtime.record_output("sensitive generated output")

    payload = manager_evidence_payload(manager)

    assert payload["default_model"] == "demo"
    assert payload["runtime_count"] == 1
    rendered = str(payload)
    assert "sensitive generated output" not in rendered
    assert "/private/models" not in rendered


def test_runtime_evidence_exposes_latest_privacy_safe_request_resources():
    _, runtime = _runtime()
    runtime.latest_request_resource_evidence = RequestResourceEvidence(
        snapshot_id="resource-test",
        baseline_memory_bytes=100,
        peak_memory_bytes=150,
        end_memory_bytes=120,
        peak_delta_bytes=50,
        average_cpu_percent=125.0,
        peak_cpu_percent=200.0,
        interval_ms=100,
        sample_count=3,
        sample_errors=0,
        cpu_observation_ms=200.0,
        memory_source="test_memory",
        cpu_source="test_cpu",
    )

    payload = runtime_evidence_payload(runtime)

    assert payload["request_resources"]["snapshot_id"] == "resource-test"
    assert payload["request_resources"]["memory"]["peak_bytes"] == 150
    assert payload["request_resources"]["cpu"]["peak_percent"] == 200.0
    assert payload["request_resources"]["sampling"]["cpu_observation_ms"] == 200.0
    assert payload["request_resources"]["sources"]["memory"] == "test_memory"
    assert payload["request_resources"]["sources"]["cpu"] == "test_cpu"
    rendered = str(payload["request_resources"])
    assert "pid" not in rendered.lower()
    assert "/private/" not in rendered
