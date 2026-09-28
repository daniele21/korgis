from __future__ import annotations

import json
from pathlib import Path
import subprocess

from scripts.verify_repeatability_contracts import PATH, validate
from tests.e2e.lifecycle import OwnedRunState, stale_owned_roots

COMMANDS_PATH = Path(".engineering/commands.json")


def test_canonical_clean_removes_owned_output(tmp_path: Path) -> None:
    commands = json.loads(COMMANDS_PATH.read_text(encoding="utf-8"))["commands"]
    clean = commands["clean"]["run"]
    owned = [
        "build",
        "dist",
        ".pytest_cache",
        ".ruff_cache",
        "playwright-report",
        "test-results",
        "src/local_llm_server.egg-info",
    ]
    for relative in owned:
        path = tmp_path / relative
        path.mkdir(parents=True, exist_ok=True)
        (path / "sentinel").write_text("owned", encoding="utf-8")

    subprocess.run(clean, cwd=tmp_path, shell=True, check=True)

    assert all(not (tmp_path / relative).exists() for relative in owned)


def test_multiple_owned_e2e_runs_leave_no_owned_temp_roots() -> None:
    before = set(stale_owned_roots())
    created = []
    for _ in range(5):
        state = OwnedRunState.create()
        created.append(state.root)
        assert state.owns_root() is True
        state.cleanup()
        assert state.root.exists() is False
    assert set(stale_owned_roots()) == before
    assert len(set(created)) == 5


def test_repository_repeatability_contract_is_valid() -> None:
    payload = json.loads(PATH.read_text(encoding="utf-8"))
    assert validate(payload) == []


def test_repeatability_contract_rejects_missing_lifecycles() -> None:
    payload = {
        "schema_version": 1,
        "owner": "test",
        "required_lifecycles": [],
        "contracts": [],
        "non_claims": ["one", "two", "three"],
    }
    errors = validate(payload)
    assert any("required_lifecycles" in error for error in errors)
    assert any("contracts must be a non-empty array" in error for error in errors)
