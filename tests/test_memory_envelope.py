from __future__ import annotations

from local_llm_server.core.contracts import GenerationOptions, InferenceRequest, TaskType
from local_llm_server.memory_envelope import (
    request_memory_envelope,
    resident_memory_envelope,
)


def test_resident_envelope_sums_attributable_components(tmp_path):
    model = tmp_path / "model.gguf"
    projector = tmp_path / "mmproj.gguf"
    model.write_bytes(b"1234567890")
    projector.write_bytes(b"proj")

    envelope = resident_memory_envelope(
        {
            "model_path": str(model),
            "mmproj_path": str(projector),
            "resource_backend_overhead_bytes": 20,
            "resource_context_cache_bytes": 30,
            "resource_prompt_cache_bytes": 40,
            "resource_safety_margin_bytes": 50,
        }
    )

    assert envelope.accounted_bytes == 154
    assert envelope.complete is True
    metadata = envelope.as_dict()
    assert metadata["components"]["model_weights"]["source"] == "artifact_file_size"
    assert metadata["components"]["projector"]["bytes"] == 4


def test_resident_envelope_keeps_unavailable_components_explicit(tmp_path):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"1234567890")

    envelope = resident_memory_envelope({"model_path": str(model)})

    assert envelope.accounted_bytes == 10
    assert envelope.complete is False
    assert set(envelope.unavailable_components) == {
        "backend_overhead",
        "context_cache",
        "safety_margin",
    }
    assert envelope.as_dict()["components"]["projector"]["source"] == "not_applicable"


def test_resident_total_override_is_authoritative_and_complete():
    envelope = resident_memory_envelope({"resource_estimate_bytes": 777})

    assert envelope.accounted_bytes == 777
    assert envelope.complete is True
    assert envelope.unavailable_components == ()


def test_llama_server_prompt_cache_uses_configured_ram_budget():
    envelope = resident_memory_envelope(
        {
            "resource_model_weights_bytes": 1,
            "resource_backend_overhead_bytes": 2,
            "resource_context_cache_bytes": 3,
            "resource_safety_margin_bytes": 4,
            "llama_server_cache_ram_mib": 2,
        }
    )

    prompt_cache = envelope.as_dict()["components"]["prompt_cache"]
    assert prompt_cache["bytes"] == 2 * 1024 * 1024
    assert prompt_cache["source"] == "llama_server_cache_ram"


def test_transient_envelope_scales_only_with_configured_request_inputs():
    request = InferenceRequest(
        task=TaskType.CHAT,
        model="demo",
        input_text="hello",
        generation=GenerationOptions(max_tokens=5),
    )

    envelope = request_memory_envelope(
        request,
        {
            "resource_request_base_bytes": 10,
            "resource_request_output_token_bytes": 3,
            "resource_request_safety_margin_bytes": 4,
        },
    )

    assert envelope.accounted_bytes == 29
    assert envelope.complete is True
    assert envelope.as_dict()["components"]["request_input"]["source"] == "not_configured"


def test_transient_envelope_does_not_turn_missing_max_tokens_into_zero():
    request = InferenceRequest(task=TaskType.CHAT, model="demo", input_text="hello")

    envelope = request_memory_envelope(
        request,
        {
            "resource_request_base_bytes": 10,
            "resource_request_output_token_bytes": 3,
        },
    )

    assert envelope.accounted_bytes == 10
    assert envelope.complete is False
    assert envelope.unavailable_components == ("request_output_tokens",)


def test_unconfigured_transient_envelope_is_unknown_not_complete_zero():
    request = InferenceRequest(task=TaskType.CHAT, model="demo", input_text="hello")

    envelope = request_memory_envelope(request, {})

    assert envelope.accounted_bytes is None
    assert envelope.complete is False


def test_transient_total_override_is_authoritative():
    request = InferenceRequest(task=TaskType.CHAT, model="demo", input_text="hello")

    envelope = request_memory_envelope(
        request,
        {"resource_request_estimate_bytes": 1234},
    )

    assert envelope.accounted_bytes == 1234
    assert envelope.complete is True


def test_resident_envelope_prefers_exact_bundle_size_over_catalog_size(tmp_path):
    artifacts = {}
    for name, content in (
        ("diffusion", b"12345"),
        ("encoder", b"1234567"),
        ("vae", b"123"),
    ):
        path = tmp_path / name
        path.write_bytes(content)
        artifacts[name] = str(path)

    envelope = resident_memory_envelope(
        {
            "model_artifacts": artifacts,
            "size_gb": 10.03,
            "resource_backend_overhead_bytes": 0,
            "resource_context_cache_bytes": 0,
            "resource_safety_margin_bytes": 0,
        }
    )

    model_weights = envelope.as_dict()["components"]["model_weights"]
    assert model_weights["bytes"] == 15
    assert model_weights["source"] == "artifact_bundle_file_size"
