from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

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
        utc_now=lambda: datetime(
            2026,
            10,
            5,
            8,
            0,
            tzinfo=timezone.utc,
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


def test_configured_model_storage_reports_only_real_mounted_directory_bytes(monkeypatch, tmp_path):
    from collections import namedtuple

    source = tmp_path / "models"
    source.mkdir()
    usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr(resource_observation.shutil, "disk_usage", lambda path: usage(9000, 3200, 5800))
    assert resource_observation.model_storage_volume_available_bytes(source) == {
        "value": 5800, "source": "measured",
    }
    assert resource_observation.model_storage_volume_available_bytes(source / "missing") == {
        "value": None, "source": "unavailable",
    }
    assert resource_observation.model_storage_volume_available_bytes(None) == {
        "value": None, "source": "unavailable",
    }


def test_storage_observation_failure_is_unavailable_not_fake_zero(monkeypatch, tmp_path):
    source = tmp_path / "models"
    source.mkdir()

    def denied(_path):
        raise OSError("private model directory inaccessible")

    monkeypatch.setattr(resource_observation.shutil, "disk_usage", denied)
    observed = resource_observation.model_storage_volume_available_bytes(source)
    assert observed == {"value": None, "source": "unavailable"}
    assert str(source) not in repr(observed)
