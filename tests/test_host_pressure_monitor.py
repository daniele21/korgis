"""Deterministic lifecycle and safety checks for pressure observation only."""
from __future__ import annotations

import time
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from local_llm_server.host_pressure_monitor import HostPressureMonitor
from local_llm_server.policy_evidence import install_policy_evidence_api
from local_llm_server.product_runtime_manager import ProductRuntimeManager
from local_llm_server.resource_manager import ResourceManager
from local_llm_server.resources import (
    ResourceBudget,
    ResourceValue,
    ResourceValueSource,
    SystemResourceSnapshot,
)
from local_llm_server.server import ServerSettings, create_app

GIB = 1024 ** 3


@dataclass
class Host:
    available: int | None = 14 * GIB
    total: int = 32 * GIB
    swap: int | None = None
    failed: bool = False

    def snapshot(self):
        if self.failed:
            raise OSError("host observer unavailable")
        return SystemResourceSnapshot(
            captured_at_monotonic=0.0,
            platform="darwin",
            total_memory_bytes=ResourceValue(
                self.total, ResourceValueSource.MEASURED, "bytes",
            ),
            available_memory_bytes=(
                ResourceValue(self.available, ResourceValueSource.MEASURED, "bytes")
                if self.available is not None else ResourceValue.unavailable("bytes")
            ),
            swap_used_bytes=(
                ResourceValue(self.swap, ResourceValueSource.MEASURED, "bytes")
                if self.swap is not None else ResourceValue.unavailable("bytes")
            ),
        )


def _resources(host: Host) -> ResourceManager:
    return ResourceManager(ResourceBudget(limit_bytes=None), host_observer=host)


def _residency():
    return {
        "runtimes": [
            {"key": "busy", "model": "org/busy", "evictable": False,
             "is_resident_default": False, "last_used_age_seconds": 80.0},
            {"key": "default", "model": "org/default", "evictable": True,
             "is_resident_default": True, "last_used_age_seconds": 40.0},
            {"key": "idle", "model": "org/idle", "evictable": True,
             "is_resident_default": False, "last_used_age_seconds": 20.0},
        ]
    }


def test_monitor_observes_and_previews_without_unloading_or_repeating_actions():
    host = Host()
    monitor = HostPressureMonitor(_resources(host), residency_snapshot=_residency)
    first = monitor.sample_once()
    assert first["host_memory"]["pressure"] == "normal"
    assert first["pressure_policy_state"] == "normal"

    host.available = 3 * GIB
    first_critical = monitor.sample_once()
    assert first_critical["host_memory"]["pressure"] == "critical"
    assert first_critical["pressure_policy_state"] == "watching"

    triggered = monitor.sample_once()
    assert triggered["pressure_policy_state"] == "triggered"
    assert triggered["eviction_candidate_count"] == 1
    assert triggered["automatic_pressure_eviction_enabled"] is False
    assert all(event["automatic_action"] is False for event in triggered["events"])
    assert triggered["sample_count"] == 3
    assert monitor.sample_once()["eviction_candidate_count"] == 0

    host.available = 14 * GIB
    assert monitor.sample_once()["pressure_policy_state"] == "triggered"
    recovered = monitor.sample_once()
    assert recovered["pressure_policy_state"] == "normal"
    assert recovered["host_memory"]["alert"] == "none"


def test_unknown_measurements_do_not_clear_pressure_episode_or_invent_normal():
    host = Host(available=3 * GIB, swap=2 * GIB)
    monitor = HostPressureMonitor(_resources(host))
    monitor.sample_once()
    monitor.sample_once()
    host.failed = True
    missing = monitor.sample_once()
    assert missing["host_memory"]["pressure"] == "unknown"
    assert missing["host_memory"]["swap_used_bytes"] is None
    assert missing["pressure_policy_state"] == "triggered"
    assert missing["automatic_pressure_eviction_enabled"] is False


def test_history_is_bounded_and_does_not_include_runtime_identity():
    host = Host()
    monitor = HostPressureMonitor(_resources(host), residency_snapshot=_residency, history_limit=2)
    monitor.sample_once()
    host.available = 3 * GIB
    monitor.sample_once()
    host.available = 14 * GIB
    view = monitor.sample_once()
    assert len(view["events"]) == 2
    assert all(set(item) == {
        "at_monotonic", "alert", "policy_transition", "automatic_action", "action",
    } for item in view["events"])
    assert "org/idle" not in str(view)
    assert "model_path" not in str(view)


def test_monitor_lifecycle_start_stop_are_idempotent_and_bounded():
    host = Host()
    monitor = HostPressureMonitor(_resources(host), interval_seconds=0.1)
    monitor.start()
    first_thread = monitor._thread
    monitor.start()
    assert monitor._thread is first_thread
    for _ in range(100):
        if monitor.snapshot()["sample_count"] >= 1:
            break
        time.sleep(0.01)
    assert monitor.snapshot()["sample_count"] >= 1
    monitor.stop()
    monitor.stop()
    assert monitor.snapshot()["running"] is False
    assert first_thread is not None and not first_thread.is_alive()


def test_monitor_enforces_one_observer_and_bounded_settings():
    no_host = ResourceManager(ResourceBudget(limit_bytes=100))
    with pytest.raises(ValueError, match="host observer"):
        HostPressureMonitor(no_host)
    with pytest.raises(ValueError, match="interval_seconds"):
        HostPressureMonitor(_resources(Host()), interval_seconds=0)
    with pytest.raises(ValueError, match="history_limit"):
        HostPressureMonitor(_resources(Host()), history_limit=129)


def test_fastapi_lifespan_owns_sampler_and_admin_api_exposes_cached_alert():
    host = Host(available=3 * GIB, swap=GIB)
    resources = _resources(host)
    manager = ProductRuntimeManager(resource_manager=resources)
    app = create_app(settings=ServerSettings(enable_admin_api=True))
    app.state.runtime_manager = manager
    install_policy_evidence_api(app)

    with TestClient(app) as client:
        for _ in range(100):
            monitor = getattr(app.state, "host_pressure_monitor", None)
            if monitor is not None and monitor.snapshot()["sample_count"] >= 1:
                break
            time.sleep(0.01)
        assert monitor is not None
        response = client.get("/api/v1/policies")
        assert response.status_code == 200
        payload = response.json()
        assert payload["host_memory"]["alert"] == "critical"
        assert payload["host_memory"]["swap_used_bytes"] == GIB
        assert payload["host_pressure_monitor"]["sample_count"] >= 1
        assert payload["host_pressure_monitor"]["running"] is True
        assert payload["host_pressure_monitor"]["automatic_pressure_eviction_enabled"] is False
        assert "model_path" not in response.text

    assert monitor.snapshot()["running"] is False
    assert monitor._thread is not None and not monitor._thread.is_alive()


def test_critical_transition_trims_only_owned_response_cache_once_per_episode():
    host = Host(available=14 * GIB)
    clears = []
    monitor = HostPressureMonitor(
        _resources(host),
        clear_response_cache=lambda: clears.append("cache"),
    )
    monitor.sample_once()
    host.available = 3 * GIB
    monitor.sample_once()
    first = monitor.sample_once()
    assert clears == ["cache"]
    assert first["response_cache_trim_count"] == 1
    assert first["reclamation_claim"] is False
    assert first["events"][-1]["action"] == "response_cache_cleared"
    assert first["events"][-1]["automatic_action"] is True
    monitor.sample_once()
    assert clears == ["cache"]
    host.available = 14 * GIB
    monitor.sample_once()
    monitor.sample_once()
    host.available = 3 * GIB
    monitor.sample_once()
    monitor.sample_once()
    assert clears == ["cache", "cache"]
    assert monitor.snapshot()["automatic_pressure_eviction_enabled"] is False


def test_cache_trim_errors_do_not_crash_sampler_or_claim_success():
    host = Host(available=3 * GIB)

    def broken_cache():
        raise RuntimeError("cache failure")

    monitor = HostPressureMonitor(_resources(host), clear_response_cache=broken_cache)
    monitor.sample_once()
    status = monitor.sample_once()
    assert status["pressure_policy_state"] == "triggered"
    assert status["response_cache_trim_count"] == 0
    assert status["events"][-1]["automatic_action"] is False


def test_lifespan_monitor_really_clears_own_completed_response_cache():
    host = Host(available=3 * GIB)
    app = create_app(settings=ServerSettings(enable_admin_api=False))
    app.state.runtime_manager = ProductRuntimeManager(resource_manager=_resources(host))
    cache = app.state.inference_response_cache
    cache.put("cache-key", {"choices": [{"message": {"content": "sensitive"}}]})
    assert cache.get("cache-key")[0] is not None

    with TestClient(app):
        monitor = app.state.host_pressure_monitor
        # Covers both possible orders with the initial background sample.
        monitor.sample_once()
        monitor.sample_once()
        assert monitor.snapshot()["response_cache_trim_count"] == 1
        assert cache.get("cache-key")[0] is None
        assert monitor.snapshot()["automatic_pressure_eviction_enabled"] is False
    assert monitor.snapshot()["running"] is False
