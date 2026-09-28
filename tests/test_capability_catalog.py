from __future__ import annotations

import pytest

from local_llm_server.capability_catalog import (
    capability_catalog_item,
    project_capability_catalog,
    validate_registry_capability_entry,
)


def test_legacy_catalog_projection_is_marked_conservative():
    item = capability_catalog_item(
        "vlm",
        {
            "model_id": "org/vlm",
            "modalities": ["text", "image"],
            "multimodal": True,
        },
    )

    assert item["model_id"] == "org/vlm"
    assert item["capability_source"] == "legacy_conservative"
    assert item["capabilities"]["tasks"] == [
        "chat",
        "structured_generation",
        "vision_language",
    ]
    assert item["generation_parameter_domains"] == []


def test_explicit_catalog_projection_is_marked_explicit():
    item = capability_catalog_item(
        "asr",
        {
            "tasks": ["transcription"],
            "input_modalities": ["audio"],
            "output_modalities": ["text"],
            "features": ["streaming"],
        },
    )

    assert item["capability_source"] == "explicit"
    assert item["capabilities"]["tasks"] == ["transcription"]


def test_catalog_projects_only_explicit_generation_domains_with_provenance():
    item = capability_catalog_item(
        "tunable",
        {
            "modalities": ["text"],
            "generation_parameter_domains": {
                "temperature": {
                    "kind": "float",
                    "minimum": 0.0,
                    "maximum": 0.6,
                    "step": 0.1,
                }
            },
        },
    )

    assert item["generation_parameter_domains"] == [
        {
            "name": "temperature",
            "kind": "float",
            "provenance": "registry_declared",
            "minimum": 0.0,
            "maximum": 0.6,
            "step": 0.1,
        }
    ]


def test_invalid_explicit_transcription_declaration_fails_before_backend():
    with pytest.raises(ValueError, match="requires audio input"):
        validate_registry_capability_entry(
            {
                "tasks": ["transcription"],
                "input_modalities": ["text"],
                "output_modalities": ["text"],
            }
        )


def test_invalid_generation_domain_declaration_fails_before_backend():
    with pytest.raises(ValueError, match="n_batch"):
        validate_registry_capability_entry(
            {
                "modalities": ["text"],
                "generation_parameter_domains": {
                    "n_batch": {
                        "kind": "integer",
                        "minimum": 1,
                        "maximum": 32,
                    }
                },
            }
        )


def test_catalog_projection_is_stably_sorted():
    projected = project_capability_catalog(
        {
            "z": {"modalities": ["text"]},
            "a": {"modalities": ["text"]},
        }
    )

    assert [item["key"] for item in projected] == ["a", "z"]
