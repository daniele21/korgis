"""Declared request-generation parameter domains for external evaluation clients.

Domains are capability metadata, not runtime defaults. Missing metadata deliberately
means unavailable: callers must never infer an optimization range from backend fields
or configured default values.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from math import isfinite
from typing import Any

from .capabilities import ThinkingMode, effective_thinking_mode


class GenerationParameterKind(str, Enum):
    FLOAT = "float"
    INTEGER = "integer"
    BOOLEAN = "boolean"


_PARAMETER_KINDS: dict[str, GenerationParameterKind] = {
    "temperature": GenerationParameterKind.FLOAT,
    "top_p": GenerationParameterKind.FLOAT,
    "top_k": GenerationParameterKind.INTEGER,
    "min_p": GenerationParameterKind.FLOAT,
    "repeat_penalty": GenerationParameterKind.FLOAT,
    "presence_penalty": GenerationParameterKind.FLOAT,
    "frequency_penalty": GenerationParameterKind.FLOAT,
    "max_tokens": GenerationParameterKind.INTEGER,
    "enable_thinking": GenerationParameterKind.BOOLEAN,
}


@dataclass(frozen=True, slots=True)
class GenerationParameterDomain:
    name: str
    kind: GenerationParameterKind
    minimum: int | float | None = None
    maximum: int | float | None = None
    step: int | float | None = None
    values: tuple[bool, ...] = ()
    provenance: str = "registry_declared"

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "name": self.name,
            "kind": self.kind.value,
            "provenance": self.provenance,
        }
        if self.kind is GenerationParameterKind.BOOLEAN:
            payload["values"] = list(self.values)
        else:
            payload["minimum"] = self.minimum
            payload["maximum"] = self.maximum
            if self.step is not None:
                payload["step"] = self.step
        return payload


def generation_parameter_domains_from_registry_entry(
    entry: Mapping[str, Any],
) -> tuple[GenerationParameterDomain, ...]:
    """Return only explicitly declared, validated request-level domains."""
    raw_domains = entry.get("generation_parameter_domains")
    if raw_domains is None:
        return ()
    if not isinstance(raw_domains, Mapping):
        raise ValueError("generation_parameter_domains must be a mapping")

    domains = tuple(
        _parse_domain(str(name), raw, entry)
        for name, raw in sorted(raw_domains.items(), key=lambda item: str(item[0]))
    )
    return domains


def _parse_domain(
    name: str,
    raw: object,
    entry: Mapping[str, Any],
) -> GenerationParameterDomain:
    expected_kind = _PARAMETER_KINDS.get(name)
    if expected_kind is None:
        raise ValueError(f"unsupported request-generation parameter domain: {name}")
    if not isinstance(raw, Mapping):
        raise ValueError(f"generation_parameter_domains.{name} must be a mapping")

    raw_kind = raw.get("kind")
    try:
        kind = GenerationParameterKind(str(raw_kind))
    except ValueError as exc:
        raise ValueError(
            f"generation_parameter_domains.{name}.kind must be float, integer, or boolean"
        ) from exc
    if kind is not expected_kind:
        raise ValueError(
            f"generation_parameter_domains.{name}.kind must be {expected_kind.value}"
        )

    allowed_fields = (
        {"kind", "values"}
        if kind is GenerationParameterKind.BOOLEAN
        else {"kind", "minimum", "maximum", "step"}
    )
    unknown_fields = set(raw) - allowed_fields
    if unknown_fields:
        unknown = ", ".join(sorted(str(field) for field in unknown_fields))
        raise ValueError(f"generation_parameter_domains.{name} has unsupported fields: {unknown}")

    if kind is GenerationParameterKind.BOOLEAN:
        return _parse_boolean_domain(name, raw, entry)
    return _parse_numeric_domain(name, kind, raw)


def _parse_boolean_domain(
    name: str,
    raw: Mapping[str, Any],
    entry: Mapping[str, Any],
) -> GenerationParameterDomain:
    values = raw.get("values")
    if not isinstance(values, (list, tuple)) or len(values) != 2:
        raise ValueError(f"generation_parameter_domains.{name}.values must contain false and true")
    if any(not isinstance(value, bool) for value in values) or set(values) != {False, True}:
        raise ValueError(f"generation_parameter_domains.{name}.values must contain false and true")
    if name == "enable_thinking" and effective_thinking_mode(entry) is not ThinkingMode.SWITCHABLE:
        raise ValueError(
            "generation_parameter_domains.enable_thinking requires effective thinking_mode=switchable"
        )
    return GenerationParameterDomain(
        name=name,
        kind=GenerationParameterKind.BOOLEAN,
        values=(False, True),
    )


def _parse_numeric_domain(
    name: str,
    kind: GenerationParameterKind,
    raw: Mapping[str, Any],
) -> GenerationParameterDomain:
    minimum = _numeric_value(name, "minimum", raw.get("minimum"), kind)
    maximum = _numeric_value(name, "maximum", raw.get("maximum"), kind)
    if minimum >= maximum:
        raise ValueError(f"generation_parameter_domains.{name} requires minimum < maximum")

    raw_step = raw.get("step")
    step = None if raw_step is None else _numeric_value(name, "step", raw_step, kind)
    if step is not None and (step <= 0 or step > maximum - minimum):
        raise ValueError(
            f"generation_parameter_domains.{name}.step must be > 0 and <= the declared span"
        )
    return GenerationParameterDomain(
        name=name,
        kind=kind,
        minimum=minimum,
        maximum=maximum,
        step=step,
    )


def _numeric_value(
    name: str,
    field: str,
    value: object,
    kind: GenerationParameterKind,
) -> int | float:
    label = f"generation_parameter_domains.{name}.{field}"
    if kind is GenerationParameterKind.INTEGER:
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"{label} must be an integer")
        return value
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{label} must be numeric")
    numeric = float(value)
    if not isfinite(numeric):
        raise ValueError(f"{label} must be finite")
    return numeric
