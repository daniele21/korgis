from __future__ import annotations

from local_llm_server.request_telemetry_device_evidence import (
    _sanitize_completion,
    compare_summaries,
    validate_candidate_summary,
)


def _request(*, source: str = "inference", quality: str = "process_global"):
    resources = None
    if source != "cache":
        resources = {
            "memory": {
                "baseline_bytes": 100,
                "peak_bytes": 150,
                "end_bytes": 120,
                "peak_delta_bytes": 50,
            },
            "cpu": {
                "average_percent": 125.0,
                "peak_percent": 200.0,
            },
            "sampling": {
                "interval_ms": 100,
                "sample_count": 3,
                "errors": 0,
                "cpu_observation_ms": 275.0,
            },
            "attribution": {
                "scope": "korgis_process_tree",
                "quality": quality,
            },
            "sources": {
                "memory": "ps_process_tree_rss_excluding_sampler",
                "cpu": "ps_process_tree_cpu_time_delta_excluding_sampler",
            },
        }
    return {
        "http_status": 200,
        "wall_clock_ms": 100.0,
        "assistant_content_retained": False,
        "korgis": {
            "evidence_version": "korgis-request-evidence-v1",
            "execution_source": source,
            "resources": resources,
        },
    }


def _stream_probe():
    return {
        "http_status": 200,
        "runtime_idle_after_close": True,
        "latest_resource_snapshot_present": True,
        "stream_content_retained": False,
    }


def test_candidate_validation_accepts_complete_privacy_safe_campaign():
    summary = {
        "source_commit": "a" * 40,
        "hardware": {"system": "Darwin"},
        "workloads": {
            "sequential_short": [_request(), _request()],
            "sequential_long": [_request(), _request()],
            "cache": {
                "first": _request(),
                "second": _request(source="cache"),
            },
            "concurrent": [_request(), _request()],
            "streaming": {
                "completed": _stream_probe(),
                "cancelled": _stream_probe(),
            },
        },
    }

    assert validate_candidate_summary(summary) == []


def test_candidate_validation_rejects_cache_fresh_resource_measurement():
    cached = _request(source="cache")
    cached["korgis"]["resources"] = _request()["korgis"]["resources"]
    summary = {
        "source_commit": "b" * 40,
        "hardware": {"system": "Darwin"},
        "workloads": {
            "sequential_short": [_request(), _request()],
            "sequential_long": [_request(), _request()],
            "cache": {"first": _request(), "second": cached},
            "concurrent": [_request(), _request()],
            "streaming": {
                "completed": _stream_probe(),
                "cancelled": _stream_probe(),
            },
        },
    }

    errors = validate_candidate_summary(summary)
    assert "cache_second:cache_has_fresh_resource_evidence" in errors


def test_sanitized_completion_never_retains_assistant_text():
    result = _sanitize_completion(
        200,
        {
            "model": "demo",
            "choices": [
                {
                    "message": {
                        "content": "private assistant output"
                    }
                }
            ],
            "usage": {
                "prompt_tokens": 2,
                "completion_tokens": 1,
            },
            "korgis": {
                "evidence_version": "korgis-request-evidence-v1",
                "execution_source": "inference",
                "resources": None,
            },
        },
        12.5,
    )

    assert result["has_assistant_content"] is True
    assert result["assistant_content_retained"] is False
    assert "private assistant output" not in str(result)


def test_sampler_overhead_comparison_is_observational_and_requires_matching_identity():
    baseline = {
        "model": "demo",
        "identity": {"runtime_fingerprint": "fp"},
        "source_commit": "b" * 40,
        "workloads": {
            "sequential_short": [
                {"wall_clock_ms": 90},
                {"wall_clock_ms": 110},
            ],
            "sequential_long": [
                {"wall_clock_ms": 190},
                {"wall_clock_ms": 210},
            ],
        },
    }
    candidate = {
        "model": "demo",
        "identity": {"runtime_fingerprint": "fp"},
        "source_commit": "a" * 40,
        "workloads": {
            "sequential_short": [
                {"wall_clock_ms": 100},
                {"wall_clock_ms": 120},
            ],
            "sequential_long": [
                {"wall_clock_ms": 200},
                {"wall_clock_ms": 220},
            ],
        },
    }

    comparison = compare_summaries(candidate, baseline)

    assert comparison["status"] == "OBSERVATIONAL"
    assert comparison["comparable"] is True
    assert (
        comparison["workloads"]["sequential_short"]["median_delta_ms"]
        == 10
    )
    assert (
        comparison["workloads"]["sequential_short"]["median_delta_percent"]
        == 10
    )
