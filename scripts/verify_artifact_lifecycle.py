#!/usr/bin/env python3
"""Exercise canonical build identity, promotion, retention and failure isolation."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "dist"


def successful_builds() -> set[Path]:
    root = OUTPUT / "builds"
    if not root.is_dir():
        return set()
    return {p for p in root.glob("*/*") if p.is_dir() and (p / "build-manifest.json").is_file()}


def invoke(*extra: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(ROOT / "deploy.sh"), *extra],
        cwd=ROOT,
        check=check,
        text=True,
        capture_output=True,
        env={**os.environ, "LOCAL_LLM_BUILD_CHANNEL": "ci", "LOCAL_LLM_BUILD_VARIANT": "wheel-sdist"},
    )


def build_once() -> Path:
    before = successful_builds()
    result = invoke("--skip-tests")
    created = successful_builds() - before
    if len(created) != 1:
        raise AssertionError(
            f"expected exactly one promoted build, got {sorted(map(str, created))}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return created.pop()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_build(build: Path) -> dict:
    manifest = json.loads((build / "build-manifest.json").read_text(encoding="utf-8"))
    required = {"build-manifest.json", "BUILD_CHANGELOG.md", "SHA256SUMS", "package"}
    assert required <= {p.name for p in build.iterdir()}
    bundles = list(build.glob("*.tar.gz"))
    assert len(bundles) == 1
    package_files = [p for p in (build / "package").iterdir() if p.is_file()]
    assert any(p.suffix == ".whl" for p in package_files)
    assert any(p.name.endswith(".tar.gz") for p in package_files)
    for line in (build / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        expected, relative = line.split("  ", 1)
        target = build / relative
        assert target.is_file()
        assert sha256(target) == expected
    return manifest


def main() -> int:
    first = build_once()
    second = build_once()
    first_manifest = verify_build(first)
    second_manifest = verify_build(second)
    assert first != second
    assert first_manifest["build_id"] != second_manifest["build_id"]
    assert first_manifest["source"]["revision"] == second_manifest["source"]["revision"]
    assert first_manifest["source"]["dirty"] is False
    assert second_manifest["source"]["dirty"] is False
    assert first_manifest["lineage"] == second_manifest["lineage"]
    assert first_manifest["build_id"] in (second / "BUILD_CHANGELOG.md").read_text(encoding="utf-8")

    third = build_once()
    third_manifest = verify_build(third)
    assert not first.exists(), "third comparable success must evict the oldest retained build"
    assert second.exists() and third.exists()
    assert successful_builds() == {second, third}
    assert second_manifest["build_id"] in (third / "BUILD_CHANGELOG.md").read_text(encoding="utf-8")
    assert third_manifest["source"]["dirty"] is False

    required_asset = ROOT / "src/local_llm_server/static/index.html"
    hidden = required_asset.with_suffix(".html.std09-hidden")
    before_failure = successful_builds()
    required_asset.rename(hidden)
    try:
        failed = invoke("--skip-tests", check=False)
        assert failed.returncode != 0, "controlled broken package unexpectedly succeeded"
    finally:
        hidden.rename(required_asset)
    assert successful_builds() == before_failure, "failed staging was promoted"
    staging = OUTPUT / ".staging"
    assert not staging.exists() or not any(staging.iterdir()), "staging residue remains"

    print("artifact lifecycle: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
