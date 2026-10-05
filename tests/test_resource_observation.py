from __future__ import annotations

from dataclasses import dataclass, field

from local_llm_server import resource_observation
from local_llm_server.resources import (
    ResourceValue,
    ResourceValueSource,
    SystemResourceSnapshot,
)


class _Observer:
    def snapshot(self) -> SystemResourceSnapshot:
        measured = ResourceValueSource.MEASURED
        return SystemResourceSnapshot(
            captured_at_monotonic=10.0,
            platform="test",
            total_memory_bytes=ResourceValue(16_000, measured, "bytes"),
            available_memory_bytes=ResourceValue(8_000, measured, "bytes"),
            process_rss_bytes=ResourceValue(1_000, measured, "bytes"),
        )


@dataclass
class _Engine:
    backend: str = "fake"
    execution_isolation: str = "in_process"


@dataclass
class _Runtime:
    key: str = "runtime-a"
    model_id: str = "model-a"
    engine: object = field(default_factory=_Engine)
    cfg: dict = field(default_factory=lambda: {"backend": "fake"})


class _Manager:
    def list(self):
        return [_Runtime()]


def test_resource_observation_is_source_backed_and_privacy_safe(monkeypatch):
    measured = ResourceValueSource.MEASURED
    monkeypatch.setattr(
        resource_observation,
        "read_process_rss",
        lambda _pid: ResourceValue(2_000, measured, "bytes"),
    )
    monkeypatch.setattr(
        resource_observation,
        "read_process_cpu_seconds",
        lambda _pid: ResourceValue(3.5, measured, "seconds"),
    )

    payload = resource_observation.resource_observation_payload(
        _Manager(),
        observer=_Observer(),
        clock=lambda: 11.0,
        utc_now=lambda: __import__("datetime").datetime(
            2026,
            10,
            5,
            8,
            0,
            tzinfo=__import__("datetime").UTC,
        ),
    )

    assert payload["schema_version"] == "1"
    assert payload["system"]["total_memory_bytes"]["value"] == 16_000
    runtime = payload["runtimes"][0]
    assert runtime["runtime_key"] == "runtime-a"
    assert runtime["scope"] == "server_process"
    assert runtime["process_rss_bytes"]["value"] == 2_000
    assert runtime["process_cpu_seconds"]["value"] == 3.5
    assert "pid" not in runtime


def test_parse_ps_cpu_time_supports_hours_and_days():
    assert resource_observation._parse_ps_cpu_time("01:02:03") == 3723
    assert resource_observation._parse_ps_cpu_time("2-01:00:00") == 176400
    assert resource_observation._parse_ps_cpu_time("bad") is None
