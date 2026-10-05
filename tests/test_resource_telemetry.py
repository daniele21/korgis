from __future__ import annotations

from local_llm_server.resource_telemetry import (
    REQUEST_EVIDENCE_VERSION,
    ResourceSample,
    RequestResourceSampler,
    aggregate_resource_samples,
    request_evidence_payload,
    _parse_cpu_time_seconds,
)


class _SequenceSource:
    source_name = "test_sequence"

    def __init__(self, values):
        self.values = list(values)
        self.index = 0

    def sample(self):
        if self.index >= len(self.values):
            return self.values[-1]
        value = self.values[self.index]
        self.index += 1
        if isinstance(value, Exception):
            raise value
        return value


def test_aggregate_resource_samples_preserves_peak_and_cpu_semantics():
    samples = [
        ResourceSample(1.0, 4_000, 100.0, 0.1),
        ResourceSample(1.1, 7_000, 250.0, 0.2),
        ResourceSample(1.2, 5_000, 150.0, 0.1),
    ]

    evidence = aggregate_resource_samples(
        samples,
        interval_ms=100,
        sample_errors=0,
        source="test",
        snapshot_id="resource-test",
    )

    assert evidence.baseline_memory_bytes == 4_000
    assert evidence.peak_memory_bytes == 7_000
    assert evidence.end_memory_bytes == 5_000
    assert evidence.peak_delta_bytes == 3_000
    assert evidence.average_cpu_percent == 187.5
    assert evidence.peak_cpu_percent == 250.0
    assert evidence.cpu_observation_ms == 400.0
    assert evidence.attribution_quality == "process_global"



def test_parse_cpu_time_seconds_supports_posix_shapes():
    assert _parse_cpu_time_seconds("00:00:01") == 1.0
    assert _parse_cpu_time_seconds("01:02:03.5") == 3723.5
    assert _parse_cpu_time_seconds("2-01:00:00") == 176400.0

def test_sampler_degrades_to_unavailable_when_sampling_fails():
    source = _SequenceSource([RuntimeError("no ps"), RuntimeError("still no ps")])
    sampler = RequestResourceSampler(interval_ms=100, sample_source=source)
    sampler.start()
    evidence = sampler.stop()

    assert evidence.sample_count == 0
    assert evidence.sample_errors >= 2
    assert evidence.peak_memory_bytes is None
    assert evidence.average_cpu_percent is None


def test_request_payload_marks_cache_without_reusing_resource_measurements():
    payload = request_evidence_payload(execution_source="cache")

    assert payload["evidence_version"] == REQUEST_EVIDENCE_VERSION
    assert payload["execution_source"] == "cache"
    assert payload["resources"] is None
    assert str(payload["request_id"]).startswith("req-")
