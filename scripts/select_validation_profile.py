#!/usr/bin/env python3
"""Select Local LLM Server validation risks, gates and profile from changed paths."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Iterable

PROFILES = ("lean", "scoped", "strong", "full")
STAGES = ("iteration", "integration", "release")
PROMOTION_BASES = {"main", "master"}
EXECUTABLE_SUFFIXES = {".py", ".js", ".ts", ".sh", ".yml", ".yaml", ".toml", ".json"}

FULL_PREFIXES = (".github/workflows/",)
FULL_PATHS = {
    ".engineering/baseline.json",
    ".engineering/commands.json",
    "scripts/select_validation_profile.py",
    "scripts/verify_operations.py",
    "scripts/verify_repository.py",
    "scripts/verify_e2e.py",
    "scripts/verify_artifact_lifecycle.py",
    "pyproject.toml",
    "uv.lock",
    "package.json",
    "package-lock.json",
    "deploy.sh",
    "release.sh",
}
STRONG_PREFIXES = (
    "src/local_llm_server/core/",
    "src/local_llm_server/static/",
    "tests/e2e/",
    "tests/real_runtime/",
    "design/",
)
STRONG_PATHS = {
    ".engineering/e2e.json",
    "SECURITY.md",
    "scripts/verify_security_exceptions.py",
    "scripts/verify_built_surface_e2e.py",
    "scripts/verify_resource_regression.py",
    "scripts/verify_fault_injection.py",
    "scripts/verify_repeatability_contracts.py",
}
LEAN_PREFIXES = ("docs/", "skills/")
LEAN_PATHS = {"README.md", "CHANGELOG.md", "AGENTS.md", "CONTRIBUTING.md", ".github/pull_request_template.md"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-ref", default=os.getenv("VALIDATION_BASE_REF", "dev"))
    parser.add_argument("--head-ref", default=os.getenv("VALIDATION_HEAD_REF", "HEAD"))
    parser.add_argument("--profile", choices=("auto",) + PROFILES, default="auto")
    parser.add_argument("--stage", choices=("auto",) + STAGES, default="auto")
    parser.add_argument("--path", action="append", default=[])
    parser.add_argument("--github-output")
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args()


def git(*args: str) -> str:
    proc = subprocess.run(["git", *args], text=True, capture_output=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "git command failed")
    return proc.stdout.strip()


def changed_paths(base_ref: str, head_ref: str) -> list[str]:
    merge_base = git("merge-base", base_ref, head_ref)
    output = git("diff", "--name-only", f"{merge_base}...{head_ref}")
    return sorted({line.strip() for line in output.splitlines() if line.strip()})


def _has_prefix(path: str, prefixes: tuple[str, ...]) -> bool:
    return any(path.startswith(prefix) for prefix in prefixes)


def classify(paths: Iterable[str]) -> tuple[str, list[str], str]:
    changed = sorted(set(paths))
    if not changed:
        return "lean", ["docs_governance"], "no changed paths detected"

    full = [p for p in changed if p in FULL_PATHS or _has_prefix(p, FULL_PREFIXES)]
    if full:
        risks = risks_for(changed)
        if "validation_infra" not in risks:
            risks.append("validation_infra")
        return "full", sorted(set(risks)), "global validation/build/dependency surface changed: " + ", ".join(full[:6])

    unknown_exec = [
        p for p in changed
        if Path(p).suffix.lower() in EXECUTABLE_SUFFIXES
        and not p.startswith(("src/", "tests/", "scripts/", ".github/", ".engineering/", "design/"))
    ]
    if unknown_exec:
        return "full", sorted(set(risks_for(changed) + ["unknown_executable"])), "unknown executable path fails safe: " + ", ".join(unknown_exec[:6])

    strong = [p for p in changed if p in STRONG_PATHS or _has_prefix(p, STRONG_PREFIXES)]
    if strong:
        return "strong", risks_for(changed), "cross-boundary/runtime/E2E/product surface changed: " + ", ".join(strong[:6])

    if all(p in LEAN_PATHS or _has_prefix(p, LEAN_PREFIXES) or p.endswith(".md") for p in changed):
        return "lean", ["docs_governance"], "documentation/governance-only blast radius"

    if all(p.startswith(("src/", "tests/", "scripts/")) for p in changed):
        return "scoped", risks_for(changed), "contained implementation/test blast radius"

    return "strong", risks_for(changed), "mixed repository surface; fail safe stronger"


def risks_for(paths: Iterable[str]) -> list[str]:
    risks: set[str] = set()
    for path in paths:
        if path.startswith(("docs/", "skills/")) or path in LEAN_PATHS or path.endswith(".md"):
            risks.add("docs_governance")
        if path.startswith(("src/", "tests/", "scripts/")) and Path(path).suffix == ".py":
            risks.add("python")
        if path.startswith(("src/local_llm_server/static/", "design/", "tests/e2e/")) or Path(path).suffix in {".js", ".ts"}:
            risks.add("ui")
        if path.startswith(("src/local_llm_server/core/", "tests/real_runtime/")):
            risks.add("runtime_contract")
        if path.startswith("tests/e2e/") or path == ".engineering/e2e.json":
            risks.add("e2e")
        if path in {"deploy.sh", "release.sh", "pyproject.toml", "uv.lock", "package.json", "package-lock.json", "scripts/verify_artifact_lifecycle.py"}:
            risks.add("packaging_or_dependencies")
        if path in {"SECURITY.md", "scripts/verify_security_exceptions.py"} or path.endswith("security.yml"):
            risks.add("security")
        if path in FULL_PATHS or path.startswith(FULL_PREFIXES):
            risks.add("validation_infra")
    return sorted(risks or {"repository_metadata"})


def gates_for(profile: str, stage: str, risks: Iterable[str]) -> list[str]:
    risk_set = set(risks)
    gates = {"repository-guards"}
    executable = bool(risk_set & {"python", "runtime_contract", "e2e", "ui", "packaging_or_dependencies", "validation_infra", "unknown_executable"})
    if stage == "iteration":
        if executable and profile != "lean":
            gates.update({"lint", "python-fast"})
        return sorted(gates)
    if executable and profile != "lean":
        gates.update({"lint", "python-matrix"})
    if profile in {"strong", "full"} and risk_set & {"ui", "e2e", "runtime_contract", "validation_infra"}:
        gates.add("browser-e2e")
    if profile == "full" or risk_set & {"packaging_or_dependencies"}:
        gates.add("package-smoke")
    if profile == "full" or risk_set & {"security", "packaging_or_dependencies"}:
        gates.add("security-audit")
    if profile == "full":
        gates.add("l2-specialists")
    if stage == "release":
        gates.update({"browser-e2e", "package-smoke", "security-audit", "l2-specialists"})
    return sorted(gates)


def resolve_stage(requested: str) -> str:
    if requested != "auto":
        return requested
    if os.getenv("GITHUB_EVENT_NAME") == "pull_request":
        if os.getenv("GITHUB_BASE_REF") in PROMOTION_BASES:
            return "release"
        if os.getenv("PR_DRAFT", "false").lower() == "true":
            return "iteration"
        return "integration"
    ref = os.getenv("GITHUB_REF_NAME", "")
    if ref in PROMOTION_BASES:
        return "release"
    if ref == "dev":
        return "integration"
    return "iteration"


def gate_key(gates: Iterable[str]) -> str:
    return hashlib.sha256(",".join(sorted(gates)).encode()).hexdigest()[:16]


def select(paths: list[str], *, stage: str, requested_profile: str = "auto") -> dict[str, object]:
    automatic, risks, reason = classify(paths)
    if stage == "release":
        automatic = "full"
        reason = "release/promotion stage requires FULL validation"
    selected = automatic
    if requested_profile != "auto":
        if PROFILES.index(requested_profile) < PROFILES.index(automatic):
            raise ValueError(f"requested profile {requested_profile} is weaker than auto-selected {automatic}")
        selected = requested_profile
        if selected != automatic:
            reason = f"explicit stronger override from auto={automatic}: {reason}"
    gates = gates_for(selected, stage, risks)
    return {"stage": stage, "profile": selected, "auto_profile": automatic, "reason": reason, "risk_dimensions": risks, "required_gates": gates, "gate_key": gate_key(gates), "changed_paths": sorted(set(paths))}


def write_github_output(path: str, result: dict[str, object]) -> None:
    values = {
        "stage": result["stage"],
        "profile": result["profile"],
        "reason": str(result["reason"]).replace("\n", " "),
        "risk_dimensions": ",".join(result["risk_dimensions"]),
        "required_gates": ",".join(result["required_gates"]),
        "gate_key": result["gate_key"],
    }
    with open(path, "a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


def self_test() -> None:
    assert select(["docs/README.md"], stage="iteration")["required_gates"] == ["repository-guards"]
    scoped = select(["src/local_llm_server/foo.py"], stage="iteration")
    assert scoped["profile"] == "scoped" and "python-fast" in scoped["required_gates"] and "browser-e2e" not in scoped["required_gates"]
    integration = select(["src/local_llm_server/core/runtime.py"], stage="integration")
    assert integration["profile"] == "strong" and "python-matrix" in integration["required_gates"] and "browser-e2e" in integration["required_gates"]
    full = select(["scripts/select_validation_profile.py"], stage="integration")
    assert full["profile"] == "full" and {"package-smoke", "security-audit", "l2-specialists"} <= set(full["required_gates"])
    artifact = select(["scripts/verify_artifact_lifecycle.py"], stage="integration")
    assert artifact["profile"] == "full" and "l2-specialists" in artifact["required_gates"]
    release = select(["README.md"], stage="release")
    assert release["profile"] == "full" and "l2-specialists" in release["required_gates"]
    print("validation selector self-test: PASS")


def main() -> int:
    args = parse_args()
    if args.self_test:
        self_test()
        return 0
    try:
        paths = args.path or changed_paths(args.base_ref, args.head_ref)
        result = select(paths, stage=resolve_stage(args.stage), requested_profile=args.profile)
    except (RuntimeError, ValueError) as exc:
        print(f"validation selection failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    if args.github_output:
        write_github_output(args.github_output, result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
