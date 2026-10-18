from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from scripts import build_artifacts as ba


def _successful_build(root: Path, name: str) -> Path:
    build = root / name
    build.mkdir(parents=True)
    (build / ba.MANIFEST).write_text(json.dumps({"build_id": name}), encoding="utf-8")
    return build


def test_build_id_is_unique_even_for_same_timestamp():
    now = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
    first = ba.new_build_id(now)
    second = ba.new_build_id(now)
    assert first.startswith("20260817T120000Z-")
    assert second.startswith("20260817T120000Z-")
    assert first != second


def test_promote_refuses_to_replace_successful_build(tmp_path: Path):
    final = tmp_path / "final"
    final.mkdir()
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "candidate").write_text("new", encoding="utf-8")

    with pytest.raises(FileExistsError):
        ba.promote(stage, final)

    assert stage.exists()
    assert (final / "candidate").exists() is False


def test_promote_moves_complete_stage_atomically_within_filesystem(tmp_path: Path):
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / ba.MANIFEST).write_text("{}", encoding="utf-8")
    final = tmp_path / "lineage" / "build-1"

    ba.promote(stage, final)

    assert not stage.exists()
    assert (final / ba.MANIFEST).is_file()


def test_retention_keeps_two_latest_successful_builds(tmp_path: Path):
    lineage = tmp_path / "lineage"
    _successful_build(lineage, "20260817T100000Z-a")
    _successful_build(lineage, "20260817T110000Z-b")
    _successful_build(lineage, "20260817T120000Z-c")

    ba.enforce_retention(lineage, keep=2)

    assert [path.name for path in ba.comparable_builds(lineage)] == [
        "20260817T110000Z-b",
        "20260817T120000Z-c",
    ]


def test_previous_manifest_ignores_incomplete_staging_directory(tmp_path: Path):
    lineage = tmp_path / "lineage"
    _successful_build(lineage, "20260817T100000Z-a")
    incomplete = lineage / "20260817T110000Z-b"
    incomplete.mkdir()

    previous = ba.previous_manifest(lineage)

    assert previous == {"build_id": "20260817T100000Z-a"}


def test_build_delta_records_required_dimensions():
    current = {
        "build_id": "new",
        "source": {"revision": "abc", "dirty": False},
        "dependencies": {"uv_lock_sha256": "1"},
        "toolchain": {"python": "3.11"},
        "lineage": {"channel": "local"},
        "artifacts": [{"name": "x.whl"}],
        "validation": ["wheel-content-check"],
    }
    previous = {
        "build_id": "old",
        "source": {"revision": "aaa", "dirty": False},
        "dependencies": {"uv_lock_sha256": "0"},
        "toolchain": {"python": "3.11"},
        "lineage": {"channel": "local"},
    }

    text = ba.build_delta(current, previous)

    for heading in (
        "## Source",
        "## Dependencies",
        "## Toolchain",
        "## Configuration",
        "## Compatibility / migrations",
        "## Artifact metrics",
        "## Validation",
    ):
        assert heading in text


def test_native_helper_bundle_requires_macos_and_signer(monkeypatch, tmp_path):
    from scripts import build_artifacts as ba

    monkeypatch.setattr(ba.sys, "platform", "linux")
    with pytest.raises(RuntimeError, match="macOS"):
        ba.compile_native_pressure_helper(tmp_path, tmp_path / "package", signing_identity="-")

    monkeypatch.setattr(ba.sys, "platform", "darwin")
    with pytest.raises(ValueError, match="identity"):
        ba.compile_native_pressure_helper(tmp_path, tmp_path / "package", signing_identity=" ")


def test_native_helper_builder_attests_source_architecture_and_signature_kind(monkeypatch, tmp_path):
    import os
    from scripts import build_artifacts as ba

    src = tmp_path / "scripts" / "macos" / "memory_pressure.swift"
    src.parent.mkdir(parents=True)
    src.write_text("// test fixture\n", encoding="utf-8")
    monkeypatch.setattr(ba.sys, "platform", "darwin")
    calls = []

    def fake_run(command, **kwargs):
        calls.append(tuple(command))
        if command[0] == "swiftc":
            target = Path(command[-1])
            target.write_bytes(b"test-native-macho")
            target.chmod(0o700)
        return None

    monkeypatch.setattr(ba.subprocess, "run", fake_run)
    pkg = tmp_path / "bundle"
    pkg.mkdir()
    result = ba.compile_native_pressure_helper(tmp_path, pkg, signing_identity="-")
    assert result == {
        "artifact": "native/korgis-memory-pressure",
        "architecture": ba.platform.machine(),
        "signature_kind": "adhoc",
        "source_sha256": ba.sha256_file(src),
    }
    assert [cmd[0] for cmd in calls] == ["swiftc", "codesign", "codesign"]
    assert calls[1][1:4] == ("--force", "--sign", "-")
    assert calls[2][1:3] == ("--verify", "--strict")
    assert os.access(pkg / "native" / "korgis-memory-pressure", os.X_OK)


def test_native_helper_signature_failure_prevents_unsigned_bundle(monkeypatch, tmp_path):
    import subprocess
    from scripts import build_artifacts as ba

    source = tmp_path / "scripts" / "macos" / "memory_pressure.swift"
    source.parent.mkdir(parents=True)
    source.write_text("// fixture", encoding="utf-8")
    monkeypatch.setattr(ba.sys, "platform", "darwin")

    def fail_sign(cmd, **kwargs):
        if cmd[0] == "codesign":
            raise subprocess.CalledProcessError(1, cmd)
        if cmd[0] == "swiftc":
            target = Path(cmd[-1])
            target.write_bytes(b"fixture")
            target.chmod(0o700)

    monkeypatch.setattr(ba.subprocess, "run", fail_sign)
    pkg = tmp_path / "bundle"
    pkg.mkdir()
    with pytest.raises(subprocess.CalledProcessError):
        ba.compile_native_pressure_helper(tmp_path, pkg, signing_identity="Developer-ID")
