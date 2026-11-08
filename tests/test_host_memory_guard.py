"""Live host-memory gate: deterministic measurements and fail-conservative admission."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from local_llm_server.resource_manager import AdmissionDecision, ResourceManager
from local_llm_server.resource_policy import (
    ResourcePolicySettings, build_resource_manager, resource_policy_snapshot,
)
from local_llm_server.resources import (
    ResourceBudget, ResourceValue, ResourceValueSource, SystemResourceSnapshot,
)
from local_llm_server.runtime import ModelRuntimeManager, ResourceAdmissionError


GIB = 1024 ** 3


@dataclass
class Host:
    available: int | None = 14 * GIB
    total: int = 32 * GIB

    def snapshot(self):
        return SystemResourceSnapshot(
            captured_at_monotonic=0.0,
            platform="darwin",
            total_memory_bytes=ResourceValue(
                self.total, ResourceValueSource.MEASURED, "bytes"
            ),
            available_memory_bytes=(
                ResourceValue(self.available, ResourceValueSource.MEASURED, "bytes")
                if self.available is not None else ResourceValue.unavailable("bytes")
            ),
        )


def test_host_admission_blocks_new_model_when_mac_has_insufficient_headroom():
    host = Host(available=9 * GIB)
    manager = ResourceManager(ResourceBudget(limit_bytes=None), host_observer=host)
    denied = manager.reserve("model-1", 3 * GIB)
    assert denied.decision is AdmissionDecision.REJECT
    assert manager.snapshot() == ()
    assert "host available memory" in denied.reason


def test_host_admission_tracks_pending_models_without_double_counting_committed():
    host = Host(available=13 * GIB)
    manager = ResourceManager(ResourceBudget(limit_bytes=None), host_observer=host)
    one = manager.reserve("model-1", 3 * GIB)
    assert one.decision is AdmissionDecision.ADMIT
    two = manager.reserve("model-2", 2 * GIB)
    assert two.decision is AdmissionDecision.REJECT
    host.available = 10 * GIB
    committed = manager.commit("model-1")
    assert committed.decision is AdmissionDecision.ADMIT
    three = manager.reserve("model-3", 2 * GIB)
    assert three.decision is AdmissionDecision.ADMIT
    # Committed model is now physically reflected in host.available.
    assert manager.snapshot()[0].reservation_id == "model-1"


def test_host_admission_refuses_missing_measurement_and_post_load_pressure():
    host = Host(available=None)
    manager = ResourceManager(ResourceBudget(limit_bytes=None), host_observer=host)
    assert manager.reserve("model", GIB).decision is AdmissionDecision.REJECT
    host.available = 9 * GIB
    assert manager.reserve("model", GIB).decision is AdmissionDecision.ADMIT
    host.available = 5 * GIB
    assert manager.commit("model").decision is AdmissionDecision.REJECT
    assert len(manager.snapshot()) == 1
    manager.release("model")
    assert manager.snapshot() == ()


def test_runtime_unknown_estimate_fails_before_backend(monkeypatch):
    host = Host()
    manager = ModelRuntimeManager(resource_manager=ResourceManager(
        ResourceBudget(limit_bytes=None), host_observer=host
    ))
    monkeypatch.setattr(
        "local_llm_server.config.build_config",
        lambda **kwargs: {"model": "unknown", "model_id": "unknown",
                          "backend": "fake", "model_path": "/missing/not-a-model"},
    )
    invoked = []
    monkeypatch.setattr(
        "local_llm_server.engine.load_llm", lambda cfg: invoked.append(cfg)
    )
    with pytest.raises(ResourceAdmissionError, match="estimate unavailable"):
        manager.load("unknown")
    assert invoked == []


def test_mac_default_is_guarded_and_reports_policy(monkeypatch):
    import local_llm_server.resource_policy as policy
    monkeypatch.setattr(policy.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        "local_llm_server.resources_macos.MacOSResourceObserver",
        lambda: Host(),
    )
    settings = ResourcePolicySettings()
    manager = build_resource_manager(settings)
    assert manager is not None and manager.host_protection_enabled
    snapshot = resource_policy_snapshot(settings, manager)
    assert snapshot["enabled"] is True
    assert snapshot["host_memory_guard_enabled"] is True
    assert snapshot["policy_state"] == "host_guard"


def test_mac_guard_can_be_explicitly_disabled(monkeypatch):
    import local_llm_server.resource_policy as policy
    monkeypatch.setattr(policy.platform, "system", lambda: "Darwin")
    settings = ResourcePolicySettings.from_environment(
        {"LOCAL_LLM_HOST_MEMORY_GUARD": "false"}
    )
    assert build_resource_manager(settings) is None


def test_runtime_load_cleanup_after_host_pressure_rises(monkeypatch):
    host = Host(available=13 * GIB)
    resources = ResourceManager(ResourceBudget(limit_bytes=None), host_observer=host)
    manager = ModelRuntimeManager(resource_manager=resources)

    class Engine:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    engine = Engine()
    calls = []

    monkeypatch.setattr(
        "local_llm_server.config.build_config",
        lambda **kwargs: {
            "model": "model", "model_id": "model",
            "backend": "fake", "model_path": "/missing/model",
            "resource_estimate_bytes": 3 * GIB,
        },
    )

    def loading_backend(cfg):
        calls.append(cfg)
        # A competing process used RAM during startup; the commit must
        # reject without publishing a supposedly safe resident model.
        host.available = 5 * GIB
        return engine

    monkeypatch.setattr(
        "local_llm_server.engine.load_llm", loading_backend,
    )
    with pytest.raises(ResourceAdmissionError, match="host available memory"):
        manager.load("model")
    assert len(calls) == 1
    assert engine.closed is True
    assert manager.list() == []
    assert resources.snapshot() == ()


def test_host_guard_invalid_environment_rejected():
    with pytest.raises(ValueError, match="HOST_MEMORY_GUARD"):
        ResourcePolicySettings.from_environment({
            "LOCAL_LLM_HOST_MEMORY_GUARD": "sometimes",
        })
