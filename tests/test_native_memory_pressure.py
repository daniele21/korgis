"""Native Darwin memory-pressure bridge: deterministic process and admission tests."""
from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from local_llm_server.native_memory_pressure import MacOSNativePressureEvents
from local_llm_server.host_pressure_monitor import HostPressureMonitor
from local_llm_server.resource_manager import AdmissionDecision, ResourceManager
from local_llm_server.resources import (
    ResourceBudget, ResourcePressure, ResourceValue, ResourceValueSource,
    SystemResourceSnapshot,
)

GIB = 1024 ** 3


@dataclass
class Host:
    available: int | None = 16 * GIB
    fail: bool = False

    def snapshot(self):
        if self.fail:
            raise OSError("vm_stat failed")
        return SystemResourceSnapshot(
            captured_at_monotonic=0,
            platform="darwin",
            total_memory_bytes=ResourceValue(32 * GIB, ResourceValueSource.MEASURED, "bytes"),
            available_memory_bytes=(
                ResourceValue(self.available, ResourceValueSource.MEASURED, "bytes")
                if self.available is not None else ResourceValue.unavailable("bytes")
            ),
        )


def test_native_critical_overrides_derived_normal_and_denies_new_allocations():
    manager = ResourceManager(ResourceBudget(limit_bytes=None), host_observer=Host())
    assert manager.reserve("first", GIB).decision is AdmissionDecision.ADMIT
    manager.release("first")

    manager.update_native_pressure(ResourcePressure.CRITICAL)
    status = manager.host_memory_status()
    assert status["pressure"] == "critical"
    assert status["source"] == "native_dispatch_event"
    assert status["derived_pressure"] == "normal"
    assert status["native_last_event"] == "critical"
    denied = manager.reserve("second", GIB)
    assert denied.decision is AdmissionDecision.REJECT
    assert "critical" in denied.reason
    assert manager.snapshot() == ()
    manager.update_native_pressure(None)
    assert manager.reserve("third", GIB).decision is AdmissionDecision.ADMIT


def test_native_normal_cannot_hide_derived_pressure_or_bypass_missing_observation():
    host = Host(available=3 * GIB)
    manager = ResourceManager(ResourceBudget(limit_bytes=None), host_observer=host)
    manager.update_native_pressure(ResourcePressure.NORMAL)
    assert manager.host_memory_status()["pressure"] == "critical"
    assert manager.reserve("a", GIB).decision is AdmissionDecision.REJECT
    host.available = None
    assert manager.reserve("b", GIB).decision is AdmissionDecision.REJECT
    assert manager.host_memory_status()["pressure"] == "normal"
    host.fail = True
    assert manager.reserve("c", GIB).decision is AdmissionDecision.REJECT
    assert manager.host_memory_status()["source"] == "native_dispatch_event"
    assert manager.host_memory_status()["available_bytes"] is None


def test_bad_native_event_type_and_unknown_are_rejected():
    manager = ResourceManager(ResourceBudget(limit_bytes=None), host_observer=Host())
    with pytest.raises(ValueError, match="unknown"):
        manager.update_native_pressure(ResourcePressure.UNKNOWN)
    with pytest.raises(TypeError, match="ResourcePressure"):
        manager.update_native_pressure("critical")


def test_helper_must_be_opt_in_absolute_executable(monkeypatch, tmp_path):
    observed = []
    no_config = MacOSNativePressureEvents(observed.append, executable="", system_name="Darwin")
    assert no_config.start() is False
    assert no_config.public_status()["state"] == "not_configured"
    assert no_config.public_status()["opted_in"] is False
    assert MacOSNativePressureEvents(
        observed.append, executable=str(tmp_path / "nope"), system_name="Darwin"
    ).start() is False
    assert MacOSNativePressureEvents(
        observed.append, executable="python", system_name="Darwin"
    ).start() is False
    unsupported = MacOSNativePressureEvents(
        observed.append, executable="/bin/sh", system_name="Linux"
    )
    assert unsupported.start() is False
    assert unsupported.public_status()["state"] == "unsupported"
    assert observed == []


def _fake_pressure_helper(tmp_path: Path) -> Path:
    path = tmp_path / "fake-pressure-helper"
    path.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        "print('ready', flush=True)\n"
        "print('critical', flush=True)\n"
        "for line in sys.stdin: pass\n",
        encoding="utf-8",
    )
    path.chmod(0o700)
    return path


def test_supervised_child_forwards_only_known_events_and_is_reaped(tmp_path):
    helper = _fake_pressure_helper(tmp_path)
    captured = []
    source = MacOSNativePressureEvents(captured.append, executable=str(helper), system_name="Darwin")
    assert source.start() is True
    assert source.start() is True
    child = source._proc
    assert child is not None
    try:
        for _ in range(100):
            if ResourcePressure.CRITICAL in captured:
                break
            time.sleep(0.01)
        assert ResourcePressure.CRITICAL in captured
        assert source.public_status()["state"] == "active"
        assert source.public_status()["kind"] == "dispatch_memorypressure"
    finally:
        source.stop()
    assert child.poll() is not None
    assert source.public_status()["state"] == "stopped"
    assert captured[-1] is None
    source.stop()  # idempotent


def test_monitor_uses_native_dispatch_event_as_canonical_guard(tmp_path, monkeypatch):
    helper = _fake_pressure_helper(tmp_path)
    monkeypatch.setenv("LOCAL_LLM_MACOS_PRESSURE_HELPER", str(helper))
    host = Host()
    manager = ResourceManager(ResourceBudget(limit_bytes=None), host_observer=host)
    monitor = HostPressureMonitor(manager, interval_seconds=0.1)
    # Deterministic Linux CI is not macOS: explicitly simulate a supported host.
    monitor._native._system = "Darwin"
    monitor.start()
    try:
        for _ in range(100):
            if manager.host_memory_status()["native_last_event"] == "critical":
                break
            time.sleep(0.01)
        native = manager.host_memory_status()
        assert native["native_last_event"] == "critical"
        assert native["pressure"] == "critical"
        assert manager.reserve("new", GIB).decision is AdmissionDecision.REJECT
    finally:
        child = monitor._native._proc
        monitor.stop()
    assert child is not None and child.poll() is not None
    assert manager.host_memory_status()["pressure"] == "normal"
    assert monitor.snapshot()["native_source"]["state"] == "stopped"


def test_native_bridge_crash_fails_back_to_derived_without_stale_warning(tmp_path):
    path = tmp_path / "one-shot"
    path.write_text(
        f"#!{sys.executable}\n"
        "print('ready', flush=True)\n"
        "print('critical', flush=True)\n",
        encoding="utf-8",
    )
    path.chmod(0o700)
    manager = ResourceManager(ResourceBudget(limit_bytes=None), host_observer=Host())
    source = MacOSNativePressureEvents(manager.update_native_pressure, executable=str(path), system_name="Darwin")
    assert source.start()
    try:
        for _ in range(100):
            if source.public_status()["state"] == "disconnected":
                break
            time.sleep(0.01)
        assert source.public_status()["state"] == "disconnected"
        assert manager.host_memory_status()["pressure"] == "normal"
        assert manager.host_memory_status().get("native_last_event") is None
    finally:
        source.stop()
