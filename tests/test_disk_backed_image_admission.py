"""Regression tests for bounded disk-backed image admission.

The stored swap amount is observation only. No host RAM allowance is
derived from swap usage, and the ordinary model policy is unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from local_llm_server.resource_manager import (
    AdmissionDecision, HostAdmissionProfile, ResourceManager, ReservationKind,
)
from local_llm_server.resources import (
    ResourceBudget, ResourceValue, ResourceValueSource, SystemResourceSnapshot,
)
from local_llm_server.runtime import ModelRuntimeManager

GIB = 1024**3


@dataclass
class Host:
    available: int = 14 * GIB
    total: int = 32 * GIB
    swap: int = 14 * GIB

    def snapshot(self) -> SystemResourceSnapshot:
        measured = ResourceValueSource.MEASURED
        return SystemResourceSnapshot(
            captured_at_monotonic=1.0,
            platform="darwin",
            total_memory_bytes=ResourceValue(self.total, measured, "bytes"),
            available_memory_bytes=ResourceValue(self.available, measured, "bytes"),
            swap_used_bytes=ResourceValue(self.swap, measured, "bytes"),
        )


def manager(host: Host) -> ResourceManager:
    return ResourceManager(ResourceBudget(limit_bytes=None), host_observer=host)


def test_opt_in_disk_backed_admits_estimated_8g_when_normal_memory() -> None:
    host = Host()
    standard = manager(host).reserve("std", 8 * GIB)
    assert standard.decision is AdmissionDecision.REJECT
    guarded = manager(host)
    admitted = guarded.reserve(
        "turbo", 8 * GIB,
        admission_profile=HostAdmissionProfile.DISK_BACKED_IMAGE,
    )
    assert admitted.decision is AdmissionDecision.ADMIT
    assert guarded.snapshot()[0].admission_profile is HostAdmissionProfile.DISK_BACKED_IMAGE
    host.available = 7 * GIB
    assert guarded.commit("turbo").decision is AdmissionDecision.ADMIT
    assert guarded.release("turbo")
    assert guarded.snapshot() == ()


def test_swap_amount_never_adds_to_physical_available() -> None:
    host = Host(available=4 * GIB, swap=100 * GIB)
    result = manager(host).reserve(
        "turbo", 8 * GIB,
        admission_profile=HostAdmissionProfile.DISK_BACKED_IMAGE,
    )
    assert result.decision is AdmissionDecision.REJECT
    assert host.swap > host.available


@pytest.mark.parametrize("available", [0, 2 * GIB, 6 * GIB])
def test_disk_backed_rejects_pressure_or_insufficient_headroom(available: int) -> None:
    host = Host(available=available)
    result = manager(host).reserve(
        "turbo", 8 * GIB,
        admission_profile=HostAdmissionProfile.DISK_BACKED_IMAGE,
    )
    assert result.decision is AdmissionDecision.REJECT


def test_disk_backed_commit_still_rejects_post_load_pressure() -> None:
    host = Host()
    resources = manager(host)
    assert resources.reserve(
        "turbo", 8 * GIB,
        admission_profile=HostAdmissionProfile.DISK_BACKED_IMAGE,
    ).decision is AdmissionDecision.ADMIT
    host.available = 5 * GIB
    failed = resources.commit("turbo")
    assert failed.decision is AdmissionDecision.REJECT
    resources.rollback("turbo")
    assert resources.snapshot() == ()


def test_nonresident_reservations_cannot_request_disk_backed() -> None:
    with pytest.raises(ValueError, match="resident models"):
        manager(Host()).reserve(
            "request", 100,
            kind=ReservationKind.TRANSIENT,
            admission_profile=HostAdmissionProfile.DISK_BACKED_IMAGE,
        )


@pytest.mark.parametrize(
    "override",
    [
        {"backend": "llama_server"},
        {"sd_server_params_backend": "diffusion=ram,te=cpu,vae=cpu"},
        {"image_offload_to_cpu": False},
        {"resource_estimate_bytes": None},
        {"resource_admission_profile": "not_a_profile"},
    ],
)
def test_runtime_rejects_invalid_profile_before_engine(monkeypatch, override: dict) -> None:
    cfg = {
        "model": "turbo",
        "model_id": "turbo",
        "backend": "stable_diffusion_cpp_image",
        "sd_server_params_backend": "diffusion=disk,te=cpu,vae=cpu",
        "image_offload_to_cpu": True,
        "resource_estimate_bytes": 8 * GIB,
        "resource_admission_profile": "disk_backed_image",
    }
    cfg.update(override)
    loaded = []
    monkeypatch.setattr(
        "local_llm_server.config.build_config",
        lambda **_kwargs: dict(cfg),
    )
    monkeypatch.setattr(
        "local_llm_server.engine.load_llm",
        lambda _cfg: loaded.append(_cfg),
    )
    runtime = ModelRuntimeManager(resource_manager=manager(Host()))
    with pytest.raises(ValueError, match="admission|disk_backed"):
        runtime.load("turbo")
    assert loaded == []

def test_turbo_registry_uses_bounded_profile_not_a_global_guard_bypass() -> None:
    from local_llm_server.config import build_config

    turbo = build_config("qwen-image-2.1-turbo-gguf-q4km")
    assert turbo["resource_admission_profile"] == "disk_backed_image"
    assert turbo["resource_estimate_bytes"] == 8 * GIB
    assert turbo["sd_server_params_backend"].startswith("diffusion=disk")
    assert turbo["image_offload_to_cpu"] is True

    standard = build_config("qwen-image-2.1-gguf-q4km")
    assert standard["resource_admission_profile"] == "standard"
    assert standard["resource_estimate_bytes"] is None
