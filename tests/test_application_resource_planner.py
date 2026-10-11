"""M8.3 dry-run is deterministic, non-mutating and evidence-class honest."""
from __future__ import annotations

import pytest

from local_llm_server.application_resource_planner import plan_application_profile
from local_llm_server.core.application_profile import ApplicationProfile
from local_llm_server.resource_manager import ResourceManager
from local_llm_server.resource_policy import ResourcePolicySettings
from local_llm_server.resources import ResourceBudget


def _profile(*, intents=("warm",), models=None):
    models = models or ("text",) * len(intents)
    return ApplicationProfile.parse({
        "schema_version": "korgis-application-profile-v1",
        "application_id": "closedroom",
        "profile_id": "meeting",
        "roles": {
            f"role{i}": {"model": model, "task": "chat", "residency": intent}
            for i, (model, intent) in enumerate(zip(models, intents))
        },
    })


def _registry(*models):
    return {name: {"tasks": ["chat"], "input_modalities": ["text"],
                   "output_modalities": ["text"], "features": [], "size_gb": 1.0}
            for name in models}


def _config(key, *, size=250, complete=True):
    result = {
        "model": key,
        "resource_model_weights_bytes": size,
        "resource_request_estimate_bytes": 70,
    }
    if complete:
        result.update(resource_backend_overhead_bytes=25,
                      resource_context_cache_bytes=75, resource_safety_margin_bytes=50)
    return result


def _plan(profile, manager, *, limit=1000, resident=None, configs=None):
    configs = configs or {}
    return plan_application_profile(
        profile,
        registry_models=_registry("text", "other"),
        settings=ResourcePolicySettings(memory_limit_bytes=limit, headroom_bytes=100),
        resource_manager=manager,
        resident_runtime_reservations=resident or {},
        config_for_model=lambda model: configs.get(model) or _config(model),
    )


def test_known_complete_cold_residency_is_plausible_without_mutation():
    rm = ResourceManager(ResourceBudget(limit_bytes=1000, headroom_bytes=100))
    before = rm.snapshot()
    plan = _plan(_profile(), rm)
    assert plan["decision"] == "plausible"
    assert plan["planned"]["additional_estimated_bytes"] == 400
    assert plan["planned"]["combined_estimated_bytes"] == 400
    assert plan["budget"]["usable_bytes"] == 900
    assert plan["models"][0]["resident_envelope"]["complete"] is True
    assert plan["models"][0]["observed_peak_bytes"]["value"] is None
    assert plan["mutated_runtime"] is False
    assert rm.snapshot() == before


def test_existing_ledger_is_accounted_and_resident_model_not_double_counted():
    rm = ResourceManager(ResourceBudget(limit_bytes=1000, headroom_bytes=100))
    rm.reserve("runtime:text", 300)
    rm.commit("runtime:text")
    before = rm.snapshot()
    plan = _plan(_profile(intents=("pinned", "warm"), models=("text", "other")),
                 rm, resident={"text": "runtime:text"})
    assert plan["decision"] == "plausible"
    assert plan["current"]["accounted_bytes"] == 300
    assert plan["planned"]["additional_estimated_bytes"] == 400
    assert plan["models"][0]["would_load"] is False
    assert plan["models"][1]["would_load"] is True
    assert rm.snapshot() == before


def test_duplicate_roles_for_one_model_count_its_footprint_once():
    plan = _plan(_profile(intents=("warm", "pinned"), models=("text", "text")),
                 ResourceManager(ResourceBudget(limit_bytes=1000)))
    assert plan["planned"]["additional_estimated_bytes"] == 400
    assert plan["models"][0]["roles"] == ["role0", "role1"]


def test_on_demand_is_deferred_and_not_assumed_to_fit_future_requests():
    plan = _plan(_profile(intents=("on_demand", "warm"), models=("text", "other")),
                 ResourceManager(ResourceBudget(limit_bytes=1000)))
    assert plan["decision"] == "plausible"
    assert plan["planned"]["additional_estimated_bytes"] == 400
    assert plan["models"][0]["on_demand_deferred"] is True
    assert plan["models"][0]["transient_request_bytes"]["request_specific"] is False


def test_incomplete_envelope_never_promotes_lower_bound_to_fit():
    plan = _plan(_profile(), ResourceManager(ResourceBudget(limit_bytes=1000)),
                 configs={"text": _config("text", complete=False)})
    assert plan["decision"] == "uncertain"
    assert plan["planned"]["additional_estimated_bytes"] is None
    assert plan["planned"]["additional_known_lower_bound_bytes"] == 250
    assert "backend_overhead" in plan["models"][0]["resident_envelope"]["unavailable_components"]


def test_lower_bound_budget_rejection_even_when_estimate_incomplete():
    plan = _plan(_profile(), ResourceManager(ResourceBudget(limit_bytes=1000)),
                 configs={"text": _config("text", size=950, complete=False)})
    assert plan["decision"] == "rejected"
    assert plan["reason"] == "known_lower_bound_exceeds_configured_budget"


def test_missing_budget_or_resource_ledger_never_claims_fit():
    rm = ResourceManager(ResourceBudget(limit_bytes=None))
    plan = _plan(_profile(), rm, limit=None)
    assert plan["decision"] == "uncertain"
    assert plan["reason"] == "configured_budget_unavailable"
    assert _plan(_profile(), None)["reason"] == "resource_ledger_unavailable"


def test_unattributed_existing_runtime_is_not_treated_as_accounted():
    rm = ResourceManager(ResourceBudget(limit_bytes=1000))
    plan = _plan(_profile(), rm, resident={"text": None})
    assert plan["decision"] == "uncertain"
    assert plan["current"]["unattributed_resident_models"] == ["text"]
    assert plan["planned"]["additional_estimated_bytes"] == 0


def test_exact_config_binding_and_task_capability_validation_precede_planning():
    rm = ResourceManager(ResourceBudget(limit_bytes=1000))
    with pytest.raises(ValueError, match="exact configured model"):
        _plan(_profile(), rm, configs={"text": {"model": "other"}})
    wrong = ApplicationProfile.parse({
        "schema_version": "korgis-application-profile-v1",
        "application_id": "closedroom",
        "profile_id": "meeting",
        "roles": {"analysis": {"model": "missing", "task": "chat", "residency": "warm"}},
    })
    with pytest.raises(ValueError, match="configured model not found"):
        _plan(wrong, rm)


def test_no_paths_prompts_or_outputs_in_plan():
    rm = ResourceManager(ResourceBudget(limit_bytes=1000))
    cfg = {**_config("text"), "model_path": "/secret/person/work.g g u f",
           "prompt": "TOP SECRET", "api_key": "SECRET TOKEN"}
    report = _plan(_profile(), rm, configs={"text": cfg})
    rendered = repr(report)
    assert "/secret/person" not in rendered
    assert "TOP SECRET" not in rendered
    assert "SECRET TOKEN" not in rendered
