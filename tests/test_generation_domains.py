from __future__ import annotations

import pytest

from local_llm_server.core.generation_domains import (
    GenerationParameterKind,
    generation_parameter_domains_from_registry_entry,
)


def test_missing_generation_domains_remain_unavailable():
    assert generation_parameter_domains_from_registry_entry({"modalities": ["text"]}) == ()


def test_declared_numeric_domains_are_typed_bounded_and_sorted():
    domains = generation_parameter_domains_from_registry_entry(
        {
            "modalities": ["text"],
            "generation_parameter_domains": {
                "top_k": {
                    "kind": "integer",
                    "minimum": 1,
                    "maximum": 40,
                    "step": 1,
                },
                "temperature": {
                    "kind": "float",
                    "minimum": 0.0,
                    "maximum": 0.8,
                    "step": 0.1,
                },
            },
        }
    )

    assert [domain.name for domain in domains] == ["temperature", "top_k"]
    assert domains[0].kind is GenerationParameterKind.FLOAT
    assert domains[0].minimum == 0.0
    assert domains[0].maximum == 0.8
    assert domains[0].step == 0.1
    assert domains[0].provenance == "registry_declared"
    assert domains[1].kind is GenerationParameterKind.INTEGER


def test_declared_domain_can_be_narrower_than_backend_field_shape():
    [domain] = generation_parameter_domains_from_registry_entry(
        {
            "modalities": ["text"],
            "generation_parameter_domains": {
                "top_p": {
                    "kind": "float",
                    "minimum": 0.7,
                    "maximum": 0.95,
                }
            },
        }
    )

    assert domain.to_dict() == {
        "name": "top_p",
        "kind": "float",
        "provenance": "registry_declared",
        "minimum": 0.7,
        "maximum": 0.95,
    }


def test_unknown_request_parameter_domain_fails_closed():
    with pytest.raises(ValueError, match="unsupported request-generation parameter domain"):
        generation_parameter_domains_from_registry_entry(
            {
                "modalities": ["text"],
                "generation_parameter_domains": {
                    "n_batch": {
                        "kind": "integer",
                        "minimum": 1,
                        "maximum": 64,
                    }
                },
            }
        )


def test_numeric_domain_requires_ordered_bounds_and_valid_step():
    with pytest.raises(ValueError, match="requires minimum < maximum"):
        generation_parameter_domains_from_registry_entry(
            {
                "modalities": ["text"],
                "generation_parameter_domains": {
                    "temperature": {
                        "kind": "float",
                        "minimum": 1.0,
                        "maximum": 1.0,
                    }
                },
            }
        )

    with pytest.raises(ValueError, match="step must be > 0"):
        generation_parameter_domains_from_registry_entry(
            {
                "modalities": ["text"],
                "generation_parameter_domains": {
                    "top_k": {
                        "kind": "integer",
                        "minimum": 1,
                        "maximum": 20,
                        "step": 0,
                    }
                },
            }
        )


def test_thinking_domain_requires_effective_switchable_capability():
    with pytest.raises(ValueError, match="effective thinking_mode=switchable"):
        generation_parameter_domains_from_registry_entry(
            {
                "backend": "llama_server",
                "modalities": ["text"],
                "thinking_mode": "none",
                "generation_parameter_domains": {
                    "enable_thinking": {
                        "kind": "boolean",
                        "values": [False, True],
                    }
                },
            }
        )

    [domain] = generation_parameter_domains_from_registry_entry(
        {
            "backend": "llama_server",
            "modalities": ["text"],
            "thinking_mode": "switchable",
            "generation_parameter_domains": {
                "enable_thinking": {
                    "kind": "boolean",
                    "values": [True, False],
                }
            },
        }
    )
    assert domain.to_dict() == {
        "name": "enable_thinking",
        "kind": "boolean",
        "provenance": "registry_declared",
        "values": [False, True],
    }
