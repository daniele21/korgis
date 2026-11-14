"""Guards for the central self-hosted native smoke script."""
from __future__ import annotations

from pathlib import Path
import subprocess

SCRIPT = Path(__file__).resolve().parents[1] / ".github/ci/local-test.sh"


def test_shell_syntax():
    assert subprocess.run(["bash", "-n", str(SCRIPT)], check=False).returncode == 0


def test_central_source_and_non_destructive_proof():
    body = SCRIPT.read_text(encoding="utf-8")
    assert 'SOURCE_REPO' in body and 'SOURCE_SHA' in body
    assert '"$(git rev-parse HEAD)" == "$SOURCE_SHA"' in body
    assert 'RUNNER_TEMP' in body
    assert '--root "$work/engines"' in body
    assert '--no-binary-package llama-cpp-python' in body
    assert '"not_run"' in body
    assert 'rm -rf "$work"' in body
    assert 'cmake-3.31.6-macos-universal.tar.gz' in body or 'cmake-$cmake_version-macos-universal.tar.gz' in body
    assert 'shasum -a 256 "$work/$cmake_dist"' in body
    for forbidden in ("sudo ", "brew install", "huggingface-cli download"):
        assert forbidden not in body
