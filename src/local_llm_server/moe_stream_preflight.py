"""Read-only macOS preflight for experimental llama.cpp MoE SSD streaming.

This only verifies the selected executable's advertised options and observed
host headroom. It never loads a model, makes network requests or edits runtime
configuration. A PASS is not model-fit, throughput or safety evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
from pathlib import Path
from typing import Callable

from .llama_server_compat import LLAMA_CPP_VALIDATED_MIN_BUILD, parse_llama_server_version
from .resources import ResourcePressure, SystemResourceSnapshot, classify_memory_pressure
from .resources_macos import MacOSResourceObserver

_GIB = 1024**3
_FLAGS = ("--moe-stream-cache", "--moe-stream-io-threads")
CommandRunner = Callable[[Path, str], str]


def _run_option(binary: Path, option: str) -> str:
    result = subprocess.run(
        [str(binary), option],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("selected backend option probe failed")
    return result.stdout + "\n" + result.stderr


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def preflight(
    binary: Path,
    *,
    model_path: Path | None = None,
    run_option: CommandRunner = _run_option,
    snapshot: SystemResourceSnapshot | None = None,
    system: str | None = None,
    architecture: str | None = None,
) -> dict[str, object]:
    """Return a bounded, path-free preflight report; never start inference."""
    host_system = (system or platform.system()).lower()
    host_arch = (architecture or platform.machine()).lower()
    report: dict[str, object] = {
        "schema": "korgis-moe-stream-mac-preflight-v1",
        "status": "blocked",
        "reason": "not_checked",
        "platform": host_system,
        "architecture": host_arch,
        "experimental_backend": True,
        "inference_exercised": False,
        "device_memory_safety_claim": False,
        "model_fit_claim": False,
        "binary_sha256": None,
        "binary_build": None,
        "binary_commit": None,
        "flags": {name: False for name in _FLAGS},
        "model_artifact": None,
        "host": {
            "total_memory_bytes": None,
            "available_memory_bytes": None,
            "swap_used_bytes": None,
            "derived_pressure": "unknown",
            "os_reserve_bytes": None,
        },
    }

    def blocked(reason: str) -> dict[str, object]:
        report["reason"] = reason
        return report

    if host_system != "darwin" or host_arch != "arm64":
        return blocked("unsupported_platform")
    if not binary.is_file():
        return blocked("selected_binary_unavailable")
    if not os.access(binary, os.X_OK):
        return blocked("selected_binary_not_executable")

    try:
        version = parse_llama_server_version(run_option(binary, "--version"))
        options = run_option(binary, "--help")
        report["binary_sha256"] = _sha256(binary)
    except (OSError, subprocess.SubprocessError, RuntimeError, ValueError):
        return blocked("binary_probe_failed")

    if version is None or version.build < LLAMA_CPP_VALIDATED_MIN_BUILD:
        return blocked("binary_identity_unattributable_or_unsupported")
    report["binary_build"] = version.build
    report["binary_commit"] = version.commit
    report["flags"] = {name: name in options for name in _FLAGS}
    if not all(report["flags"].values()):
        return blocked("moe_stream_flags_unavailable")

    if model_path is not None:
        if not model_path.is_file():
            return blocked("model_artifact_unavailable")
        try:
            # Verify an optional model artifact by streaming reads, never mmap
            # or loading backend weights. Size does not imply resident RAM.
            report["model_artifact"] = {
                "sha256": _sha256(model_path),
                "disk_bytes": model_path.stat().st_size,
            }
        except OSError:
            return blocked("model_artifact_verification_failed")

    try:
        measurement = snapshot or MacOSResourceObserver().snapshot()
        total = measurement.total_memory_bytes.value
        available = measurement.available_memory_bytes.value
        swap = measurement.swap_used_bytes.value
        pressure = classify_memory_pressure(measurement)
    except (OSError, ValueError, subprocess.SubprocessError):
        return blocked("host_observation_failed")

    host = report["host"]
    host["total_memory_bytes"] = total
    host["available_memory_bytes"] = available
    host["swap_used_bytes"] = swap
    host["derived_pressure"] = pressure.value
    if not isinstance(total, int) or not isinstance(available, int) or total <= 0:
        return blocked("host_memory_unavailable")
    # Mirrors the conservative 18.75% / 2-6 GiB operational OS reserve
    # for *preflight only*. Runtime admission remains the authority.
    reserve = max(2 * _GIB, min(6 * _GIB, int(total * 0.1875)))
    host["os_reserve_bytes"] = reserve
    if pressure is not ResourcePressure.NORMAL or available <= reserve:
        return blocked("host_headroom_insufficient")

    report["status"] = "ready_for_bounded_pilot"
    report["reason"] = "binary_flags_and_host_headroom_observed"
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--model-path", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)

    result = preflight(args.binary, model_path=args.model_path)
    serialized = json.dumps(result, sort_keys=True, separators=(",", ":"))
    if args.output is None:
        print(serialized)
    else:
        # Content is path-free; only the explicitly chosen output receives it.
        args.output.write_text(serialized + "\n", encoding="utf-8")
    return 0 if result["status"] == "ready_for_bounded_pilot" else 2


if __name__ == "__main__":
    raise SystemExit(main())
