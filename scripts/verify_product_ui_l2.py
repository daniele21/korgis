#!/usr/bin/env python3
"""Reference-grade product-ui fitness checks for Local LLM Server."""

from __future__ import annotations

import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / ".engineering" / "product-ui-l2.json"
BASELINE_PATH = ROOT / ".engineering" / "baseline.json"
UX_PATH = ROOT / "design" / "ux-contract.json"
PR_TEMPLATE = ROOT / ".github" / "pull_request_template.md"
EXPECTED_STANDARD_VERSION = "0.10.0"
EXPECTED_STANDARD_REVISION = "f392e4ded5cd3ec5efbf806934789c6cb9f10ded"
EXPECTED_PRODUCT_UI_CONTRACT_VERSION = "0.5.0"
ALLOWED_MANUAL_STATES = {"pending", "complete", "not-justified"}
SENSITIVE_FIELD_FRAGMENTS = {
    "prompt",
    "output",
    "content",
    "path",
    "hostname",
    "machine",
    "username",
    "email",
    "token",
    "secret",
}


def load_object(path: Path, errors: list[str]) -> dict:
    if not path.is_file():
        errors.append(f"missing required file: {path.relative_to(ROOT)}")
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"invalid JSON in {path.relative_to(ROOT)}: {exc}")
        return {}
    if not isinstance(value, dict):
        errors.append(f"{path.relative_to(ROOT)} must contain a JSON object")
        return {}
    return value


def require_file(relative: str, errors: list[str], *, label: str) -> Path:
    path = ROOT / relative
    if not path.is_file():
        errors.append(f"missing {label}: {relative}")
    return path


def declarations(css: str) -> set[str]:
    return set(re.findall(r"(?m)^\s*(--ds-[a-z0-9-]+)\s*:", css))


def defines_component(css: str, selector: str) -> bool:
    escaped = re.escape(selector)
    return bool(re.search(rf"(?m)^\s*{escaped}(?=[\s,:.#\[>+~{{])", css))


def main() -> int:
    errors: list[str] = []
    warnings: list[str] = []

    baseline = load_object(BASELINE_PATH, errors)
    profiles = baseline.get("profiles", []) if baseline else []
    if not isinstance(profiles, list):
        profiles = []

    print("Product-ui L2 fitness contract")
    print(f"root: {ROOT}")

    if "product-ui" not in profiles:
        print("SKIP: product-ui profile not adopted")
        print("RESULT: PASS (not applicable)")
        return 0

    standard = baseline.get("standard", {}) if isinstance(baseline, dict) else {}
    if standard.get("version") != EXPECTED_STANDARD_VERSION:
        errors.append(
            f"product-ui L2 requires repo-template-sw {EXPECTED_STANDARD_VERSION}; "
            f"found {standard.get('version')!r}"
        )
    if standard.get("revision") != EXPECTED_STANDARD_REVISION:
        errors.append("product-ui L2 standard revision does not match the reviewed 0.10.0 baseline")
    if baseline.get("target_level") != "L2":
        errors.append("product-ui L2 validator requires target_level L2 when product-ui is adopted")

    policy = load_object(POLICY_PATH, errors)
    ux = load_object(UX_PATH, errors)
    if policy.get("schema_version") != 1:
        errors.append("product-ui-l2.schema_version must be 1")
    if policy.get("contract_version") != EXPECTED_PRODUCT_UI_CONTRACT_VERSION:
        errors.append(
            f"product-ui-l2.contract_version must be {EXPECTED_PRODUCT_UI_CONTRACT_VERSION}"
        )

    system = policy.get("design_system")
    if not isinstance(system, dict):
        errors.append("product-ui-l2.design_system must be an object")
        system = {}

    canonical_relative = system.get("canonical_css")
    if not isinstance(canonical_relative, str) or not canonical_relative:
        errors.append("design_system.canonical_css must be a non-empty string")
        canonical_relative = "src/local_llm_server/static/design-system.css"
    canonical = require_file(canonical_relative, errors, label="canonical design-system CSS")
    canonical_css = canonical.read_text(encoding="utf-8") if canonical.is_file() else ""

    required_tokens = system.get("required_tokens")
    if not isinstance(required_tokens, list) or not required_tokens:
        errors.append("design_system.required_tokens must be a non-empty list")
        required_tokens = []
    defined_tokens = declarations(canonical_css)
    for token in required_tokens:
        if not isinstance(token, str) or not token.startswith("--ds-"):
            errors.append(f"invalid required design token: {token!r}")
        elif token not in defined_tokens:
            errors.append(f"required design token is not declared in canonical CSS: {token}")

    required_components = system.get("required_components")
    if not isinstance(required_components, list) or not required_components:
        errors.append("design_system.required_components must be a non-empty list")
        required_components = []
    for selector in required_components:
        if not isinstance(selector, str) or not selector.startswith(".ds-"):
            errors.append(f"invalid required design component selector: {selector!r}")
        elif not defines_component(canonical_css, selector):
            errors.append(f"required design component is not defined in canonical CSS: {selector}")

    static_dir = ROOT / "src" / "local_llm_server" / "static"
    if system.get("forbid_namespaced_token_definitions_outside_canonical_css") is True:
        for css_path in sorted(static_dir.glob("*.css")):
            if css_path == canonical:
                continue
            css = css_path.read_text(encoding="utf-8")
            duplicates = sorted(declarations(css))
            if duplicates:
                errors.append(
                    f"{css_path.relative_to(ROOT)} declares reserved --ds-* tokens outside canonical CSS: "
                    + ", ".join(duplicates)
                )

    if system.get("forbid_namespaced_component_definitions_outside_canonical_css") is True:
        for css_path in sorted(static_dir.glob("*.css")):
            if css_path == canonical:
                continue
            css = css_path.read_text(encoding="utf-8")
            duplicated = [
                selector
                for selector in required_components
                if isinstance(selector, str) and defines_component(css, selector)
            ]
            if duplicated:
                errors.append(
                    f"{css_path.relative_to(ROOT)} redefines canonical semantic component roots: "
                    + ", ".join(sorted(duplicated))
                )

    adaptive = policy.get("adaptive_layout")
    if not isinstance(adaptive, dict):
        errors.append("product-ui-l2.adaptive_layout must be an object")
        adaptive = {}
    adaptive_source = adaptive.get("source")
    if not isinstance(adaptive_source, str) or not adaptive_source:
        errors.append("adaptive_layout.source must be a non-empty string")
    else:
        adaptive_path = require_file(adaptive_source, errors, label="adaptive layout source")
        adaptive_css = adaptive_path.read_text(encoding="utf-8") if adaptive_path.is_file() else ""
        breakpoints = adaptive.get("required_breakpoints_px")
        if not isinstance(breakpoints, list) or not breakpoints:
            errors.append("adaptive_layout.required_breakpoints_px must be a non-empty list")
        else:
            for breakpoint in breakpoints:
                if not isinstance(breakpoint, int) or breakpoint <= 0:
                    errors.append(f"invalid adaptive breakpoint: {breakpoint!r}")
                    continue
                marker = f"@media (max-width: {breakpoint}px)"
                if marker not in adaptive_css:
                    errors.append(f"adaptive layout source is missing contractual breakpoint: {marker}")

    ux_journeys = ux.get("critical_journeys") if isinstance(ux, dict) else None
    journey_ids = {
        journey.get("id")
        for journey in ux_journeys or []
        if isinstance(journey, dict) and isinstance(journey.get("id"), str)
    }
    evidence = policy.get("critical_journey_evidence")
    if not isinstance(evidence, list) or not evidence:
        errors.append("product-ui-l2.critical_journey_evidence must be a non-empty list")
        evidence = []
    evidence_ids: set[str] = set()
    for index, item in enumerate(evidence):
        if not isinstance(item, dict):
            errors.append(f"critical_journey_evidence[{index}] must be an object")
            continue
        journey_id = item.get("journey_id")
        if not isinstance(journey_id, str) or not journey_id:
            errors.append(f"critical_journey_evidence[{index}].journey_id must be a non-empty string")
            continue
        if journey_id in evidence_ids:
            errors.append(f"duplicate critical journey evidence id: {journey_id}")
        evidence_ids.add(journey_id)
        if journey_id not in journey_ids:
            errors.append(f"critical journey evidence does not map to ux-contract: {journey_id}")
        files = item.get("files")
        if not isinstance(files, list) or not files:
            errors.append(f"critical journey {journey_id} must name at least one evidence file")
            continue
        for relative in files:
            if not isinstance(relative, str) or not relative:
                errors.append(f"critical journey {journey_id} contains an invalid evidence path")
            else:
                require_file(relative, errors, label=f"evidence for {journey_id}")
    missing_journeys = sorted(journey_ids - evidence_ids)
    if missing_journeys:
        errors.append("critical journeys missing evidence ownership: " + ", ".join(missing_journeys))

    privacy = policy.get("privacy_research")
    if not isinstance(privacy, dict):
        errors.append("product-ui-l2.privacy_research must be an object")
        privacy = {}
    if privacy.get("product_telemetry_default") != "off":
        errors.append("privacy_research.product_telemetry_default must remain 'off'")
    for key in (
        "raw_prompt_collection",
        "raw_output_collection",
        "local_path_collection",
        "hostname_or_machine_identity_collection",
    ):
        if privacy.get(key) is not False:
            errors.append(f"privacy_research.{key} must be false")
    fields = privacy.get("allowed_usability_fields")
    if not isinstance(fields, list) or not fields:
        errors.append("privacy_research.allowed_usability_fields must be a non-empty list")
        fields = []
    for field in fields:
        if not isinstance(field, str) or not field:
            errors.append(f"invalid usability evidence field: {field!r}")
            continue
        lowered = field.lower().replace("-", "_")
        if any(fragment in lowered for fragment in SENSITIVE_FIELD_FRAGMENTS):
            errors.append(f"usability evidence field is too sensitive/ambiguous: {field}")
    protocol = privacy.get("protocol")
    if isinstance(protocol, str) and protocol:
        require_file(protocol, errors, label="product experience validation protocol")
    else:
        errors.append("privacy_research.protocol must point to a durable repository document")

    review = policy.get("significant_ux_change_review")
    if not isinstance(review, dict):
        errors.append("product-ui-l2.significant_ux_change_review must be an object")
        review = {}
    markers = review.get("required_pr_markers")
    if not isinstance(markers, list) or not markers:
        errors.append("significant_ux_change_review.required_pr_markers must be a non-empty list")
        markers = []
    template_text = PR_TEMPLATE.read_text(encoding="utf-8") if PR_TEMPLATE.is_file() else ""
    for marker in markers:
        if not isinstance(marker, str) or not marker:
            errors.append(f"invalid product experience PR marker: {marker!r}")
        elif f"<!-- {marker} -->" not in template_text:
            errors.append(f"PR template missing product experience review marker: {marker}")

    manual = policy.get("manual_evidence")
    if not isinstance(manual, dict):
        errors.append("product-ui-l2.manual_evidence must be an object")
        manual = {}
    for key in ("manual_accessibility_status", "representative_user_usability_status"):
        status = manual.get(key)
        if status not in ALLOWED_MANUAL_STATES:
            errors.append(f"manual_evidence.{key} must be one of {sorted(ALLOWED_MANUAL_STATES)}")
        elif status == "pending":
            warnings.append(f"{key} remains pending; full product-ui L2 must not be claimed")
    if not isinstance(manual.get("completion_rule"), str) or not manual.get(
        "completion_rule", ""
    ).strip():
        errors.append("manual_evidence.completion_rule must be a non-empty string")

    for warning in warnings:
        print(f"WARN: {warning}")
    for error in errors:
        print(f"FAIL: {error}")
    if errors:
        print(f"RESULT: FAIL ({len(errors)} error(s), {len(warnings)} warning(s))")
        return 1
    print(f"RESULT: PASS ({len(warnings)} warning(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
