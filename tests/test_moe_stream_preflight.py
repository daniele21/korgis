"""Preflight never starts an inference engine or leaks local paths."""
from __future__ import annotations

import json

import pytest

from local_llm_server.moe_stream_preflight import preflight
from local_llm_server.resources import (
    ResourceValue,
    ResourceValueSource,
    SystemResourceSnapshot,
)


def _measured(value):
    return ResourceValue(value, ResourceValueSource.MEASURED, "bytes")


def _snapshot(available=14 * 1024**3):
    return SystemResourceSnapshot(
        captured_at_monotonic=1.0,
        platform="darwin",
        total_memory_bytes=_measured(32 * 1024**3),
        available_memory_bytes=_measured(available),
        swap_used_bytes=_measured(512 * 1024**2),
    )


def _binary(tmp_path):
    path = tmp_path / "private-backend-executable"
    path.write_bytes(b"fake-executable")
    path.chmod(0o755)
    return path


def _runner(_binary, option):
    if option == "--version":
        return "version: 10621 (c1d0e7a)"
    if option == "--help":
        return "--moe-stream-cache --moe-stream-io-threads"
    raise AssertionError("unexpected execution")


def test_ready_for_pilot_only_with_verified_binary_and_headroom(tmp_path):
    binary = _binary(tmp_path)
    report = preflight(
        binary, run_option=_runner, snapshot=_snapshot(),
        system="Darwin", architecture="arm64",
    )
    assert report["status"] == "ready_for_bounded_pilot"
    assert report["inference_exercised"] is False
    assert report["device_memory_safety_claim"] is False
    assert report["model_fit_claim"] is False
    assert report["host"]["os_reserve_bytes"] == 6 * 1024**3
    assert str(tmp_path) not in json.dumps(report)
    assert report["binary_sha256"] is not None


def test_default_unsupported_platform_fails_before_binary_probe(tmp_path):
    binary = _binary(tmp_path)
    def fail(*_args):
        raise AssertionError("must not execute backend")
    report = preflight(binary, run_option=fail, system="Linux", architecture="x86_64")
    assert report["reason"] == "unsupported_platform"


def test_selected_binary_missing_fails_closed(tmp_path):
    report = preflight(
        tmp_path / "missing", system="Darwin", architecture="arm64",
    )
    assert report["reason"] == "selected_binary_unavailable"
    assert str(tmp_path) not in json.dumps(report)


def test_unadvertised_flags_fail_closed(tmp_path):
    report = preflight(
        _binary(tmp_path), run_option=lambda b, o: (
            "version: 10621 (c1d0e7a)" if o == "--version" else "--moe-stream-cache"
        ), system="Darwin", architecture="arm64",
    )
    assert report["reason"] == "moe_stream_flags_unavailable"


@pytest.mark.parametrize("available", [None, 0, 3 * 1024**3, 5 * 1024**3])
def test_missing_or_low_host_headroom_cannot_pass(tmp_path, available):
    resource = (
        ResourceValue.unavailable("bytes") if available is None
        else _measured(available)
    )
    snapshot = SystemResourceSnapshot(
        captured_at_monotonic=1, platform="darwin",
        total_memory_bytes=_measured(32 * 1024**3),
        available_memory_bytes=resource,
    )
    report = preflight(
        _binary(tmp_path), run_option=_runner, snapshot=snapshot,
        system="Darwin", architecture="arm64",
    )
    assert report["status"] == "blocked"
    assert report["reason"] in {"host_headroom_insufficient", "host_memory_unavailable"}


def test_binary_probe_timeout_is_not_success(tmp_path):
    def fail(_binary, _option):
        import subprocess
        raise subprocess.TimeoutExpired("llama-server", 10)
    report = preflight(
        _binary(tmp_path), run_option=fail,
        system="Darwin", architecture="arm64",
    )
    assert report["reason"] == "binary_probe_failed"


def test_model_artifact_is_verified_without_loading(tmp_path):
    binary = _binary(tmp_path)
    model = tmp_path / "private-model.gguf"
    model.write_bytes(b"GGUF" + b"\0" * 32)
    report = preflight(
        binary, model_path=model, run_option=_runner,
        snapshot=_snapshot(), system="Darwin", architecture="arm64",
    )
    assert report["model_artifact"]["disk_bytes"] == 36
    assert len(report["model_artifact"]["sha256"]) == 64
    assert str(model) not in json.dumps(report)
    assert report["inference_exercised"] is False
