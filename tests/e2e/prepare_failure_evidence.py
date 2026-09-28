#!/usr/bin/env python3
"""Create a minimal identity-bearing failure bundle, then remove raw Playwright evidence."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = ROOT / "test-results"
RESULTS = RESULTS_DIR / "playwright-results.json"
OUTPUT_DIR = ROOT / "e2e-failure-evidence"
OUTPUT = OUTPUT_DIR / "manifest.json"


def _safe_relative_test_file(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    normalized = value.replace("\\", "/")
    marker = "tests/e2e/"
    if marker in normalized:
        return marker + normalized.split(marker, 1)[1]
    path = Path(normalized)
    if not path.is_absolute() and ".." not in path.parts:
        return normalized
    return None


def _collect_specs(suites: object) -> list[dict[str, object]]:
    collected: list[dict[str, object]] = []
    if not isinstance(suites, list):
        return collected
    for suite in suites:
        if not isinstance(suite, dict):
            continue
        suite_file = _safe_relative_test_file(suite.get("file"))
        for spec in suite.get("specs") or []:
            if not isinstance(spec, dict):
                continue
            outcomes: list[dict[str, object]] = []
            for test in spec.get("tests") or []:
                if not isinstance(test, dict):
                    continue
                for result in test.get("results") or []:
                    if not isinstance(result, dict):
                        continue
                    duration = result.get("duration")
                    outcomes.append({
                        "status": result.get("status") if isinstance(result.get("status"), str) else "unknown",
                        "duration_ms": duration if isinstance(duration, (int, float)) and not isinstance(duration, bool) else None,
                    })
            collected.append({"file": suite_file, "title": str(spec.get("title") or ""), "ok": bool(spec.get("ok")), "outcomes": outcomes})
        collected.extend(_collect_specs(suite.get("suites")))
    return collected


def build_manifest(report: dict[str, Any] | None, env: dict[str, str]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "evidence_type": "playwright_failure_summary",
        "identity": {
            "source_head_sha": env.get("SOURCE_HEAD_SHA") or env.get("GITHUB_SHA") or "unknown",
            "workflow_sha": env.get("GITHUB_SHA") or "unknown",
            "run_id": env.get("GITHUB_RUN_ID") or "unknown",
            "run_attempt": env.get("GITHUB_RUN_ATTEMPT") or "unknown",
            "workflow": env.get("GITHUB_WORKFLOW") or "unknown",
            "job": env.get("GITHUB_JOB") or "unknown",
            "event": env.get("GITHUB_EVENT_NAME") or "unknown",
        },
        "privacy": {
            "raw_page_content_retained": False,
            "screenshots_retained": False,
            "videos_retained": False,
            "traces_retained": False,
            "stdout_stderr_retained": False,
            "absolute_paths_retained": False,
        },
        "tests": _collect_specs(report.get("suites") if isinstance(report, dict) else None),
    }


def main() -> int:
    report: dict[str, Any] | None = None
    if RESULTS.is_file():
        try:
            loaded = json.loads(RESULTS.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                report = loaded
        except (OSError, json.JSONDecodeError):
            pass

    manifest = build_manifest(report, dict(os.environ))
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    # Raw reports/traces/media can contain rendered content, stack details or
    # absolute paths. Once the allow-listed manifest exists, remove them before
    # the workflow's broad artifact path is evaluated.
    if RESULTS_DIR.exists():
        shutil.rmtree(RESULTS_DIR)

    print(f"Wrote sanitized E2E failure evidence: {OUTPUT.relative_to(ROOT)}")
    print("Removed raw Playwright failure artifacts before upload")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
