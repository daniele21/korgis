"""Local command helpers for representative worker reclamation evidence.

The runner writes a privacy-safe JSON report from real OS resource observations
and isolated worker lifecycle cycles. It intentionally omits prompt/output text
and local model paths from the serialized report.
"""
from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Any, Callable, Mapping

from .artifact_verification import ArtifactVerificationStore, verified_receipt_for_config
from .config import build_config
from .request_pipeline import prepare_chat_request
from .resources import (
    ResourceObserver,
    ResourceValue,
    ResourceValueSource,
    StandardLibraryResourceObserver,
    SystemResourceSnapshot,
)
from .resources_macos import MacOSResourceObserver
from .worker_reclamation import WorkerReclamationReport, run_worker_reclamation_experiment


_BACKEND_PACKAGES = {
    "llama_cpp": "llama-cpp-python",
    "mlx": "mlx-lm",
    "mlx_vlm_server": "mlx-vlm",
    "diffusers_image": "diffusers",
    "mflux_image": "mflux",
}
_LLAMA_SERVER_VERSION = re.compile(
    r"version:\s*(?P<build>\d+)\s*\(`?(?P<commit>[0-9a-fA-F]{7,40})`?\)"
)


@dataclass(frozen=True, slots=True)
class HardwareEvidenceOptions:
    model: str
    cycles: int = 3
    prompt: str = "Reply with the single word OK."
    max_tokens: int = 32
    model_path: str | None = None
    backend: str | None = None
    backend_version: str | None = None
    accelerator: str | None = None
    settle_seconds: float = 2.0
    no_download: bool = False

    def __post_init__(self) -> None:
        if not self.model.strip():
            raise ValueError("model must be non-empty")
        if self.cycles < 1:
            raise ValueError("cycles must be >= 1")
        if self.max_tokens < 1:
            raise ValueError("max_tokens must be >= 1")
        if self.settle_seconds < 0:
            raise ValueError("settle_seconds must be >= 0")


class WorkerSystemResourceObserver:
    """Observe host resources plus the bound child RSS while it exists.

    Before worker start and after worker stop, process RSS is explicitly
    unavailable. A terminated child is never relabelled as a measured zero.
    """

    def __init__(self, delegate: ResourceObserver) -> None:
        self.delegate = delegate
        self._worker: Any | None = None

    def bind_worker(self, worker: Any) -> None:
        self._worker = worker

    def unbind_worker(self, worker: Any) -> None:
        if self._worker is worker:
            self._worker = None

    def snapshot(self) -> SystemResourceSnapshot:
        snapshot = self.delegate.snapshot()
        worker = self._worker
        pid = getattr(worker, "pid", None) if worker is not None else None
        rss = (
            _read_process_rss_for_pid(pid)
            if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0
            else ResourceValue.unavailable("bytes")
        )
        return replace(snapshot, process_rss_bytes=rss)


def default_worker_resource_observer() -> ResourceObserver:
    base: ResourceObserver
    if platform.system().lower() == "darwin":
        base = MacOSResourceObserver()
    else:
        base = StandardLibraryResourceObserver()
    return WorkerSystemResourceObserver(base)


def local_environment_metadata() -> dict[str, str]:
    """Return bounded host-environment metadata without hostname or user identity."""
    return {
        "system": platform.system().lower() or "unknown",
        "release": platform.release() or "unknown",
        "machine": platform.machine().lower() or "unknown",
        "python_version": platform.python_version(),
    }


def execute_hardware_reclamation_evidence(
    options: HardwareEvidenceOptions,
    *,
    observer: ResourceObserver | None = None,
    config_builder: Callable[..., dict[str, Any]] = build_config,
    experiment_runner: Callable[..., WorkerReclamationReport] = run_worker_reclamation_experiment,
    clock_sleep: Callable[[float], None] = time.sleep,
    verification_store: ArtifactVerificationStore | None = None,
) -> dict[str, object]:
    """Execute representative local worker cycles and return a shareable report."""
    explicit: dict[str, Any] = {}
    if options.backend is not None:
        explicit["backend"] = options.backend
    if options.no_download:
        explicit["no_download"] = True

    cfg = config_builder(
        model=options.model,
        model_path=options.model_path,
        **explicit,
    )
    resolved_backend_version = options.backend_version or resolve_backend_version(cfg)
    if resolved_backend_version is not None:
        cfg["backend_version"] = resolved_backend_version
    if options.accelerator is not None:
        cfg["hardware_accelerator"] = options.accelerator

    # HE-1 consumes the exact ID-2 receipt contract. It does not hash again and
    # does not create a second artifact-identity format. A stale/missing receipt
    # simply leaves the worker descriptor exploratory.
    receipt = verified_receipt_for_config(cfg, store=verification_store)
    if receipt is not None:
        cfg["artifact_sha256"] = receipt.sha256
        cfg["artifact_size_bytes"] = receipt.size_bytes

    resource_observer = observer or default_worker_resource_observer()
    preflight = resource_observer.snapshot()
    total_memory = preflight.total_memory_bytes.value
    if isinstance(total_memory, int) and not isinstance(total_memory, bool) and total_memory >= 0:
        cfg["hardware_total_memory_bytes"] = total_memory

    prepared = prepare_chat_request(
        {
            "model": str(cfg.get("model") or options.model),
            "messages": [{"role": "user", "content": options.prompt}],
            "temperature": 0.0,
            "max_tokens": options.max_tokens,
            "stream": False,
        },
        runtime_config=cfg,
        runtime_model_id=str(cfg.get("model_id") or cfg.get("model") or options.model),
    )
    backend_payload = dict(prepared.backend.kwargs)

    report = experiment_runner(
        resource_observer,
        config=cfg,
        request_payload=backend_payload,
        cycles=options.cycles,
        settle_after_stop=(
            (lambda: clock_sleep(options.settle_seconds))
            if options.settle_seconds > 0
            else None
        ),
    )

    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "environment": local_environment_metadata(),
        "procedure": {
            "name": "worker_reclamation_v1",
            "cycles": options.cycles,
            "settle_after_stop_seconds": options.settle_seconds,
            "max_tokens": options.max_tokens,
            "prompt_recorded": False,
            "output_recorded": False,
        },
        "report": report.to_public_dict(),
    }


def write_evidence_report(path: Path, payload: Mapping[str, object]) -> None:
    """Atomically persist one evidence report without retaining a partial JSON file."""
    target = path.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)


def resolve_backend_version(config: Mapping[str, Any]) -> str | None:
    explicit = config.get("backend_version")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()

    backend = str(config.get("backend") or "")
    package_name = _BACKEND_PACKAGES.get(backend)
    if package_name is not None:
        try:
            return metadata.version(package_name)
        except metadata.PackageNotFoundError:
            return None

    if backend == "llama_server":
        binary = config.get("llama_server_bin")
        if binary:
            return _probe_llama_server_version(str(binary))
    return None


def _read_process_rss_for_pid(pid: int) -> ResourceValue:
    system = platform.system().lower()
    if system == "linux":
        try:
            resident_pages = int(Path(f"/proc/{pid}/statm").read_text(encoding="utf-8").split()[1])
            page_size = int(os.sysconf("SC_PAGE_SIZE"))
            value = resident_pages * page_size
        except (OSError, ValueError, IndexError):
            return ResourceValue.unavailable("bytes")
        return ResourceValue(value, ResourceValueSource.MEASURED, "bytes")

    if system == "darwin":
        try:
            completed = subprocess.run(
                ["ps", "-o", "rss=", "-p", str(pid)],
                capture_output=True,
                text=True,
                timeout=2.0,
                check=True,
            )
            value = int(completed.stdout.strip()) * 1024
        except (OSError, ValueError, subprocess.SubprocessError):
            return ResourceValue.unavailable("bytes")
        return ResourceValue(value, ResourceValueSource.MEASURED, "bytes")

    return ResourceValue.unavailable("bytes")


def _probe_llama_server_version(binary: str) -> str | None:
    try:
        completed = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    text = "\n".join(value for value in (completed.stdout, completed.stderr) if value)
    match = _LLAMA_SERVER_VERSION.search(text)
    if match is None:
        return None
    return f"build-{match.group('build')}@{match.group('commit').lower()}"
