"""M8.1/M8.2 zero-resident, fail-closed application intent validation."""

from __future__ import annotations

import pytest

from local_llm_server.core.application_profile import (
    ApplicationProfile,
    ApplicationProfileError,
    ResidencyIntent,
    validate_application_profile,
)


def _profile(task="chat", residency="on_demand"):
    return {
        "schema_version": "korgis-application-profile-v1",
        "application_id": "closedroom",
        "profile_id": "meeting",
        "roles": {
            "analysis": {"model": "text-4b", "task": task, "residency": residency}
        },
    }


def _model(tasks, *, inputs=("text",), features=()):
    return {
        "tasks": list(tasks),
        "input_modalities": list(inputs),
        "output_modalities": ["text"],
        "features": list(features),
        "thinking_mode": "none",
    }


def test_versioned_profile_parses_and_roundtrips_immutable_roles():
    profile = ApplicationProfile.parse(_profile())
    assert profile.roles["analysis"].residency is ResidencyIntent.ON_DEMAND
    assert profile.to_dict() == {
        **_profile(),
        "roles": {
            "analysis": {"model": "text-4b", "task": "chat",
                         "residency": "on_demand", "priority": "interactive"}
        },
    }
    with pytest.raises(TypeError):
        profile.roles["other"] = profile.roles["analysis"]


@pytest.mark.parametrize("mutation", [
    lambda x: x.update({"schema_version": "future"}),
    lambda x: x.update({"cloud_fallback": True}),
    lambda x: x["roles"]["analysis"].update({"model": "other", "unknown": 1}),
    lambda x: x["roles"]["analysis"].update({"task": "unrecognized"}),
    lambda x: x["roles"]["analysis"].update({"residency": "automatic"}),
    lambda x: x["roles"]["analysis"].update({"priority": "highest"}),
    lambda x: x["roles"]["analysis"].update({"idle_ttl_seconds": 60}),
    lambda x: x["roles"]["analysis"].update({"idle_ttl_seconds": True, "residency": "warm"}),
])
def test_unknown_version_policy_or_broken_role_rejected(mutation):
    value = _profile()
    mutation(value)
    with pytest.raises(ApplicationProfileError):
        ApplicationProfile.parse(value)


def test_warm_ttl_is_explicit_but_does_not_trigger_lifecycle():
    value = _profile(residency="warm")
    value["roles"]["analysis"]["idle_ttl_seconds"] = 120
    profile = ApplicationProfile.parse(value)
    assert profile.roles["analysis"].idle_ttl_seconds == 120
    out = validate_application_profile(profile, {"text-4b": _model(["chat"])})
    assert out["mutated_runtime"] is False
    assert out["resource_fit"] == "unavailable"
    assert out["roles"][0]["residency_intent"] == "warm"


def test_canonical_capability_facts_gate_tasks_without_backend_execution(monkeypatch):
    import local_llm_server.engine as engine

    def forbidden(*args, **kwargs):
        raise AssertionError("profile validation must never load a model")

    monkeypatch.setattr(engine, "load_llm", forbidden)
    transcript = ApplicationProfile.parse(_profile(task="transcription"))
    with pytest.raises(ApplicationProfileError, match="task unsupported"):
        validate_application_profile(transcript, {"text-4b": _model(["chat"])})
    valid = validate_application_profile(
        transcript, {"text-4b": _model(["transcription"], inputs=("audio",))}
    )
    assert valid["roles"][0]["task"] == "transcription"
    assert valid["roles"][0]["model"] == "text-4b"


def test_structured_generation_requires_feature_not_just_task_declaration():
    profile = ApplicationProfile.parse(_profile(task="structured_generation"))
    with pytest.raises(ApplicationProfileError, match="structured output"):
        validate_application_profile(
            profile, {"text-4b": _model(["structured_generation"])}
        )
    assert validate_application_profile(
        profile,
        {"text-4b": _model(["structured_generation"], features=("structured_output",))},
    )["valid"] is True


def test_unknown_model_never_falls_back_or_assumes_capacity():
    profile = ApplicationProfile.parse(_profile())
    with pytest.raises(ApplicationProfileError, match="configured model not found"):
        validate_application_profile(profile, {"other": _model(["chat"])})
    result = validate_application_profile(profile, {"text-4b": _model(["chat"])})
    assert result["resource_fit"] == "unavailable"
    assert result["mutated_runtime"] is False
