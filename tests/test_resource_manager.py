from __future__ import annotations

import pytest

from local_llm_server.resource_manager import (
    AdmissionDecision,
    ReservationState,
    ResourceManager,
)
from local_llm_server.resources import ResourceBudget


def test_reservation_fits_usable_budget():
    manager = ResourceManager(ResourceBudget(limit_bytes=1000, headroom_bytes=100))

    result = manager.reserve("load-a", 600)

    assert result.decision is AdmissionDecision.ADMIT
    assert manager.snapshot()[0].state is ReservationState.RESERVED


def test_reservation_rejects_over_budget_without_mutating_ledger():
    manager = ResourceManager(ResourceBudget(limit_bytes=1000, headroom_bytes=100))
    manager.reserve("load-a", 600)

    result = manager.reserve("load-b", 400)

    assert result.decision is AdmissionDecision.REJECT
    assert [item.reservation_id for item in manager.snapshot()] == ["load-a"]


def test_unbounded_budget_returns_unknown_and_does_not_pretend_admission():
    manager = ResourceManager(ResourceBudget(limit_bytes=None))

    result = manager.reserve("load-a", 600)

    assert result.decision is AdmissionDecision.UNKNOWN
    assert manager.snapshot() == ()


def test_commit_can_reconcile_observed_footprint():
    manager = ResourceManager(ResourceBudget(limit_bytes=2000, headroom_bytes=0))
    manager.reserve("load-a", 500)

    result = manager.commit("load-a", observed_bytes=750)

    assert result.decision is AdmissionDecision.ADMIT
    reservation = manager.snapshot()[0]
    assert reservation.state is ReservationState.COMMITTED
    assert reservation.accounted_bytes == 750


def test_observed_overcommit_is_rejected_without_corrupting_reservation():
    manager = ResourceManager(ResourceBudget(limit_bytes=1000))
    manager.reserve("a", 400)
    manager.commit("a")
    manager.reserve("b", 400)

    result = manager.commit("b", observed_bytes=700)

    assert result.decision is AdmissionDecision.REJECT
    b = next(item for item in manager.snapshot() if item.reservation_id == "b")
    assert b.state is ReservationState.RESERVED
    assert b.accounted_bytes == 400


def test_release_and_rollback_are_idempotent():
    manager = ResourceManager(ResourceBudget(limit_bytes=1000))
    manager.reserve("load-a", 100)

    assert manager.release("load-a") is True
    assert manager.release("load-a") is False
    assert manager.rollback("missing") is False


def test_duplicate_reservation_is_rejected():
    manager = ResourceManager(ResourceBudget(limit_bytes=1000))
    manager.reserve("load-a", 100)

    with pytest.raises(ValueError, match="reservation already exists"):
        manager.reserve("load-a", 100)


def test_preview_uses_exact_budget_policy_without_reserving():
    manager = ResourceManager(ResourceBudget(limit_bytes=1000, headroom_bytes=100))
    manager.reserve("existing", 600)
    before = manager.snapshot()
    preview = manager.preview_reservation(400)
    assert preview.decision is AdmissionDecision.REJECT
    assert preview.reason == manager.reserve("new", 400).reason
    assert manager.snapshot() == before
    assert manager.preview_reservation(300).decision is AdmissionDecision.ADMIT
    assert manager.snapshot() == before


def test_preview_without_configured_budget_does_not_invent_admission():
    manager = ResourceManager(ResourceBudget(limit_bytes=None))
    preview = manager.preview_reservation(100)
    assert preview.decision is AdmissionDecision.UNKNOWN
    assert manager.snapshot() == ()
    with pytest.raises(ValueError, match="requested_bytes"):
        manager.preview_reservation(-1)

