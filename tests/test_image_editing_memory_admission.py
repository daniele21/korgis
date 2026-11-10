"""Editing must reserve more host memory than the proven text-to-image path."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from local_llm_server.config import build_config
from local_llm_server.resource_manager import ResourceManager
from local_llm_server.resources import (
    ResourceBudget, ResourceValue, ResourceValueSource, SystemResourceSnapshot,
)
from local_llm_server.runtime import ModelRuntimeManager, ResourceAdmissionError

GIB = 1024**3
TURBO = "qwen-image-2.1-turbo-gguf-q4km"


@dataclass
class Mac:
    available: int
    swap: int = 14 * GIB

    def snapshot(self) -> SystemResourceSnapshot:
        measured = ResourceValueSource.MEASURED
        return SystemResourceSnapshot(
            captured_at_monotonic=1,
            platform="darwin",
            total_memory_bytes=ResourceValue(32 * GIB, measured, "bytes"),
            available_memory_bytes=ResourceValue(self.available, measured, "bytes"),
            swap_used_bytes=ResourceValue(self.swap, measured, "bytes"),
        )


def runtime(host: Mac) -> ModelRuntimeManager:
    return ModelRuntimeManager(resource_manager=ResourceManager(
        ResourceBudget(limit_bytes=None), host_observer=host,
    ))


def test_editing_rejects_14g_headroom_while_text_to_image_still_admits():
    mac = Mac(available=14 * GIB)
    manager = runtime(mac)
    txt = build_config(TURBO)
    editing = build_config(TURBO, image_editing_enabled=True)
    assert txt["resource_estimate_bytes"] == 8 * GIB
    assert editing["resource_estimate_bytes"] == 10 * GIB

    resident = manager._reserve_runtime_load(TURBO, txt)
    assert resident is not None
    manager.resource_manager.release(resident)

    with pytest.raises(ResourceAdmissionError, match="host available memory"):
        manager._reserve_runtime_load(TURBO, editing)
    assert manager.resource_manager.snapshot() == ()


def test_opt_in_editing_can_admit_safely_with_16g_reclaimable_and_releases():
    mac = Mac(available=16 * GIB, swap=100 * GIB)
    manager = runtime(mac)
    cfg = build_config(TURBO, image_editing_enabled=True)
    resident = manager._reserve_runtime_load(TURBO, cfg)
    assert resident is not None
    assert cfg["resource_admission"]["estimate_bytes"] == 10 * GIB
    assert cfg["resource_admission"]["profile"] == "disk_backed_image"
    assert manager.resource_manager.snapshot()[0].accounted_bytes == 10 * GIB
    manager.resource_manager.release(resident)
    assert manager.resource_manager.snapshot() == ()


def test_swap_cannot_rescue_editing_when_actual_available_memory_is_low():
    manager = runtime(Mac(available=4 * GIB, swap=200 * GIB))
    with pytest.raises(ResourceAdmissionError):
        manager._reserve_runtime_load(TURBO, build_config(TURBO, image_editing_enabled=True))
    assert manager.resource_manager.snapshot() == ()
