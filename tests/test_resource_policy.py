from __future__ import annotations

import pytest

from local_llm_server.resource_manager import ResourceManager
from local_llm_server.resource_policy import (
    ResourcePolicySettings,
    build_resource_manager,
    resource_policy_snapshot,
)


def test_disabled_policy_does_not_construct_enforcing_manager():
    settings = ResourcePolicySettings(host_memory_guard_enabled=False)
    assert settings.enabled is False
    assert build_resource_manager(settings) is None

    snapshot = resource_policy_snapshot(settings, None)
    assert snapshot["policy_state"] == "disabled"
    assert snapshot["usable_budget_bytes"] is None
    assert snapshot["remaining_bytes"] is None
    assert snapshot["committed_bytes"] == 0


def test_environment_accepts_byte_limit_and_headroom():
    settings = ResourcePolicySettings.from_environment(
        {
            "LOCAL_LLM_MEMORY_LIMIT_BYTES": "1000",
            "LOCAL_LLM_MEMORY_HEADROOM_BYTES": "100",
        }
    )
    assert settings.memory_limit_bytes == 1000
    assert settings.headroom_bytes == 100
    assert settings.budget.usable_bytes == 900


def test_environment_accepts_gib_values():
    settings = ResourcePolicySettings.from_environment(
        {
            "LOCAL_LLM_MEMORY_LIMIT_GIB": "2",
            "LOCAL_LLM_MEMORY_HEADROOM_GIB": "0.5",
        }
    )
    assert settings.memory_limit_bytes == 2 * 1024 ** 3
    assert settings.headroom_bytes == int(0.5 * 1024 ** 3)


def test_bytes_and_gib_for_same_field_are_rejected():
    with pytest.raises(ValueError, match="bytes or GiB"):
        ResourcePolicySettings.from_environment(
            {
                "LOCAL_LLM_MEMORY_LIMIT_BYTES": "100",
                "LOCAL_LLM_MEMORY_LIMIT_GIB": "1",
            }
        )


def test_headroom_cannot_exceed_limit():
    with pytest.raises(ValueError, match="cannot exceed"):
        ResourcePolicySettings(memory_limit_bytes=100, headroom_bytes=101)


def test_snapshot_reports_committed_reserved_and_remaining_bytes():
    settings = ResourcePolicySettings(memory_limit_bytes=1000, headroom_bytes=100)
    manager = build_resource_manager(settings)
    assert isinstance(manager, ResourceManager)

    manager.reserve("ready", 300)
    manager.commit("ready")
    manager.reserve("loading", 200)

    snapshot = resource_policy_snapshot(settings, manager)
    assert snapshot == {
        "enabled": True,
        "memory_limit_bytes": 1000,
        "headroom_bytes": 100,
        "usable_budget_bytes": 900,
        "committed_bytes": 300,
        "reserved_bytes": 200,
        "remaining_bytes": 400,
        "reservation_count": 2,
        "policy_state": "configured",
    }
