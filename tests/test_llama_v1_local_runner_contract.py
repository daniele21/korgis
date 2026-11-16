"""Guards for independent generic Mac validation and native v1 Metal smoke."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
GENERIC_SCRIPT = ROOT / ".github/ci/local-test.sh"
NATIVE_SCRIPT = ROOT / ".github/ci/llama-v1-native-smoke.sh"
MAC_TASKS = ROOT / ".github/ci/mac-tasks.json"


def test_shell_syntax():
    for script in (GENERIC_SCRIPT, NATIVE_SCRIPT):
        assert subprocess.run(["bash", "-n", str(script)], check=False).returncode == 0


def test_central_source_and_non_destructive_proof():
    # The pinned, heavyweight native smoke remains fully present, source
    # attributable and scratch-only, but is not part of generic Mac dispatch.
    body = NATIVE_SCRIPT.read_text(encoding="utf-8")
    assert "SOURCE_REPO" in body and "SOURCE_SHA" in body
    assert '"$(git rev-parse HEAD)" == "$SOURCE_SHA"' in body
    assert "RUNNER_TEMP" in body
    assert '--root "$work/engines"' in body
    assert "--no-binary-package llama-cpp-python" in body
    assert '"not_run"' in body
    assert 'rm -rf "$work"' in body
    assert (
        "cmake-3.31.6-macos-universal.tar.gz" in body
        or "cmake-$cmake_version-macos-universal.tar.gz" in body
    )
    assert 'shasum -a 256 "$work/$cmake_dist"' in body
    for forbidden in ("sudo ", "brew install", "huggingface-cli download"):
        assert forbidden not in body


def test_generic_mac_dispatch_remains_lightweight_and_independent():
    body = GENERIC_SCRIPT.read_text(encoding="utf-8")
    for required in (
        "scripts/verify_repository.py",
        "scripts/verify_operations.py",
        "scripts/verify_e2e.py",
        "set -euo pipefail",
    ):
        assert required in body
    for heavyweight in (
        "install_llama_cpp_v1_macos.sh",
        "llama-cpp-python",
        "cmake",
        "curl --fail",
    ):
        assert heavyweight not in body

    tasks = json.loads(MAC_TASKS.read_text(encoding="utf-8"))["tasks"]
    assert tasks["template-smoke"]["script"] == ".github/ci/local-test.sh"
    assert tasks["template-smoke"]["manual_only"] is True
