"""Representative macOS evidence runner for local image-generation runtimes.

The runner records observations. It never turns one device run into a memory-fit,
performance, reclamation-safety or production-safety claim.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import signal
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .artifact_verification import (
    ArtifactVerificationStore,
    public_verification_summary,
    verified_receipt_for_config,
    verify_model_artifact,
)
from .config import build_config
from .evidence_profiles import ImageEvidenceProfile, load_image_evidence_profile
from .hardware_evidence import WorkerSystemResourceObserver, local_environment_metadata
from .memory_envelope import resident_memory_envelope
from .resources import ResourceObserver, ResourceValue, SystemResourceSnapshot
from .resources_macos import MacOSResourceObserver

_GIB = 1024**3
_PASS = "PASS"
_FAIL = "FAIL"
_INCONCLUSIVE = "INCONCLUSIVE"


class ImageHardwareEvidenceError(RuntimeError):
    """Bounded evidence-run failure without private path leakage."""


@dataclass(frozen=True, slots=True)
class ImageHardwareEvidenceOptions:
    profile: str | Path
    output_dir: Path
    model_path: str | None = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _safe_error(exc: BaseException) -> str:
    text = str(exc).strip().replace("\n", " ")
    lowered = text.lower()
    if (
        any(token in lowered for token in ("/users/", "/home/", "file://"))
        or "/" in text
        or "\\" in text
    ):
        text = exc.__class__.__name__
    return (text[:217] + "...") if len(text) > 220 else (text or exc.__class__.__name__)


def _resource(value: ResourceValue) -> dict[str, Any]:
    return {
        "value": value.value,
        "source": value.source.value,
        "unit": value.unit,
    }


def _snapshot(snapshot: SystemResourceSnapshot) -> dict[str, Any]:
    return {
        "platform": snapshot.platform,
        "total_memory_bytes": _resource(snapshot.total_memory_bytes),
        "available_memory_bytes": _resource(snapshot.available_memory_bytes),
        "process_rss_bytes": _resource(snapshot.process_rss_bytes),
        "accelerator_memory_bytes": _resource(snapshot.accelerator_memory_bytes),
        "thermal_pressure": _resource(snapshot.thermal_pressure),
    }


def _summarize_samples(
    samples: list[SystemResourceSnapshot],
) -> dict[str, Any]:
    rss_values = [
        int(sample.process_rss_bytes.value)
        for sample in samples
        if isinstance(sample.process_rss_bytes.value, int)
        and not isinstance(sample.process_rss_bytes.value, bool)
    ]
    available_values = [
        int(sample.available_memory_bytes.value)
        for sample in samples
        if isinstance(sample.available_memory_bytes.value, int)
        and not isinstance(sample.available_memory_bytes.value, bool)
    ]
    return {
        "sample_count": len(samples),
        "peak_process_rss_bytes": max(rss_values) if rss_values else None,
        "minimum_available_memory_bytes": (
            min(available_values) if available_values else None
        ),
        "rss_source": "measured" if rss_values else "unavailable",
        "available_memory_source": (
            "measured" if available_values else "unavailable"
        ),
    }


def _git_state() -> dict[str, Any]:
    def command(*args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode:
            raise ImageHardwareEvidenceError("git source identity is unavailable")
        return result.stdout.strip()

    return {
        "revision": command("rev-parse", "HEAD"),
        "branch": command("branch", "--show-current"),
        "tracked_clean": not bool(
            command("status", "--porcelain", "--untracked-files=no")
        ),
        "root": Path(command("rev-parse", "--show-toplevel")).resolve(),
    }


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _port_is_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def _request_json(
    url: str,
    *,
    method: str = "GET",
    payload: Mapping[str, Any] | None = None,
    timeout: float,
) -> tuple[int, Mapping[str, Any]]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310
            status = int(getattr(response, "status", 200))
            raw = response.read()
    except HTTPError as exc:
        status, raw = int(exc.code), exc.read()
    except URLError as exc:
        raise ImageHardwareEvidenceError("loopback request failed") from exc

    try:
        body = json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ImageHardwareEvidenceError(
            f"loopback response was not JSON (status {status})"
        ) from exc
    if not isinstance(body, Mapping):
        raise ImageHardwareEvidenceError(
            f"loopback response was not an object (status {status})"
        )
    return status, body


class OwnedImageEvidenceServer:
    """Own one loopback Korgis process and sample it while it is alive."""

    def __init__(
        self,
        *,
        profile: ImageEvidenceProfile,
        output_dir: Path,
        observer: WorkerSystemResourceObserver,
        model_path: str | None,
        requester: Callable[..., tuple[int, Mapping[str, Any]]] = _request_json,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.profile = profile
        self.output_dir = output_dir
        self.observer = observer
        self.model_path = model_path
        self.requester = requester
        self.sleep = sleep
        self.monotonic = monotonic
        self.process: subprocess.Popen[str] | None = None
        self._log: Any | None = None

    @property
    def base_url(self) -> str:
        execution = self.profile.execution
        return f"http://{execution.host}:{execution.port}"

    def start(self) -> tuple[dict[str, Any], list[SystemResourceSnapshot]]:
        execution = self.profile.execution
        if not _port_is_free(execution.host, execution.port):
            raise ImageHardwareEvidenceError("configured evidence port is already in use")

        command = [
            sys.executable,
            "-m",
            "local_llm_server",
            "serve",
            "--model",
            self.profile.model,
            "--host",
            execution.host,
            "--port",
            str(execution.port),
            "--no-download",
        ]
        if self.model_path:
            command.extend(["--model-path", self.model_path])

        self._log = (self.output_dir / "image-evidence-server.log").open(
            "w",
            encoding="utf-8",
        )
        started = self.monotonic()
        self.process = subprocess.Popen(
            command,
            stdout=self._log,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        self.observer.bind_worker(self.process)
        samples: list[SystemResourceSnapshot] = []
        deadline = started + self.profile.startup_timeout_seconds

        while self.monotonic() < deadline:
            samples.append(self.observer.snapshot())
            if self.process.poll() is not None:
                raise ImageHardwareEvidenceError(
                    "representative image server exited before readiness"
                )
            try:
                status, body = self.requester(
                    f"{self.base_url}/health",
                    timeout=min(2.0, self.profile.startup_timeout_seconds),
                )
            except ImageHardwareEvidenceError:
                self.sleep(self.profile.sample_interval_seconds)
                continue
            if status == 200 and body.get("ok") is True:
                samples.append(self.observer.snapshot())
                return (
                    {
                        "wall_seconds": round(self.monotonic() - started, 3),
                        "listener": "loopback",
                        "log_retained_locally": True,
                    },
                    samples,
                )
            self.sleep(self.profile.sample_interval_seconds)

        raise ImageHardwareEvidenceError("representative image server readiness timed out")

    def runtime_identity(self) -> Mapping[str, Any]:
        status, body = self.requester(
            f"{self.base_url}/v1/runtime/identity",
            timeout=10,
        )
        if status != 200:
            raise ImageHardwareEvidenceError(
                f"runtime identity returned HTTP {status}"
            )
        return body

    def generate(
        self,
        payload: Mapping[str, Any],
    ) -> tuple[Mapping[str, Any], float, list[SystemResourceSnapshot]]:
        samples: list[SystemResourceSnapshot] = [self.observer.snapshot()]
        started = self.monotonic()
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                self.requester,
                f"{self.base_url}/v1/images/generations",
                method="POST",
                payload=payload,
                timeout=self.profile.request_timeout_seconds,
            )
            while not future.done():
                samples.append(self.observer.snapshot())
                self.sleep(self.profile.sample_interval_seconds)
            status, body = future.result()
        samples.append(self.observer.snapshot())
        if status != 200:
            raise ImageHardwareEvidenceError(
                f"image generation returned HTTP {status}"
            )
        return body, (self.monotonic() - started) * 1000.0, samples

    def stop(self) -> tuple[dict[str, Any], list[SystemResourceSnapshot]]:
        process = self.process
        samples: list[SystemResourceSnapshot] = []
        if process is None:
            return (
                {
                    "owned_process_started": False,
                    "listener_closed": True,
                    "hard_kill_required": False,
                },
                samples,
            )

        graceful = True
        hard_kill = False
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGINT)
            except ProcessLookupError:
                pass
            deadline = self.monotonic() + 20
            while process.poll() is None and self.monotonic() < deadline:
                samples.append(self.observer.snapshot())
                self.sleep(min(self.profile.sample_interval_seconds, 0.5))
            if process.poll() is None:
                graceful = False
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                deadline = self.monotonic() + 5
                while process.poll() is None and self.monotonic() < deadline:
                    samples.append(self.observer.snapshot())
                    self.sleep(min(self.profile.sample_interval_seconds, 0.5))
            if process.poll() is None:
                hard_kill = True
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=5)

        self.observer.unbind_worker(process)
        if self.profile.settle_seconds:
            self.sleep(self.profile.settle_seconds)
        samples.append(self.observer.snapshot())
        if self._log is not None:
            self._log.close()
            self._log = None

        return (
            {
                "owned_process_started": True,
                "exit_code": process.returncode,
                "graceful": graceful,
                "hard_kill_required": hard_kill,
                "listener_closed": _port_is_free(
                    self.profile.execution.host,
                    self.profile.execution.port,
                ),
            },
            samples,
        )


class ImageHardwareEvidenceCampaign:
    """Execute one versioned image workload and retain bounded evidence."""

    def __init__(
        self,
        options: ImageHardwareEvidenceOptions,
        *,
        verification_store: ArtifactVerificationStore | None = None,
        observer: WorkerSystemResourceObserver | None = None,
        server_factory: Callable[..., Any] = OwnedImageEvidenceServer,
        config_builder: Callable[..., dict[str, Any]] = build_config,
        git_state: Callable[[], dict[str, Any]] = _git_state,
        system: Callable[[], str] = platform.system,
        machine: Callable[[], str] = platform.machine,
    ) -> None:
        self.options = options
        self.profile = load_image_evidence_profile(options.profile)
        self.output_dir = options.output_dir.expanduser().resolve()
        self.report_path = self.output_dir / "image-evidence.json"
        self.verification_store = verification_store or ArtifactVerificationStore()
        self.observer = observer or WorkerSystemResourceObserver(
            MacOSResourceObserver()
        )
        self.server_factory = server_factory
        self.config_builder = config_builder
        self.git_state = git_state
        self.system = system
        self.machine = machine
        self.cfg: dict[str, Any] | None = None
        self.server: Any | None = None
        self._all_samples: list[SystemResourceSnapshot] = []
        self.report: dict[str, Any] = {
            "schema_version": 1,
            "procedure": "image_generation_hardware_evidence_v1",
            "started_at": _utc_now(),
            "completed_at": None,
            "profile": {
                "id": self.profile.profile_id,
                "source_kind": self.profile.source_kind,
                "configuration_sha256": self.profile.configuration_digest(),
                "model": self.profile.model,
                "prompt_sha256": hashlib.sha256(
                    self.profile.workload.prompt.encode("utf-8")
                ).hexdigest(),
                "prompt_recorded": False,
                "workload": {
                    "width": self.profile.workload.width,
                    "height": self.profile.workload.height,
                    "num_inference_steps": (
                        self.profile.workload.num_inference_steps
                    ),
                    "guidance_scale": self.profile.workload.guidance_scale,
                    "seed": self.profile.workload.seed,
                    "output_format": self.profile.workload.output_format,
                    "repetitions": self.profile.repetitions,
                },
                "safety": {
                    "host_safety_margin_gib": (
                        self.profile.safety.host_safety_margin_gib
                    ),
                    "require_macos": self.profile.safety.require_macos,
                    "require_apple_silicon": (
                        self.profile.safety.require_apple_silicon
                    ),
                    "require_clean_dev_checkout": (
                        self.profile.safety.require_clean_dev_checkout
                    ),
                    "require_output_outside_repo": (
                        self.profile.safety.require_output_outside_repo
                    ),
                },
            },
            "source": {},
            "environment": {},
            "artifact": None,
            "runtime_identity": None,
            "phases": {},
            "generations": [],
            "observations": {},
            "claims": dict(self.profile.claims),
            "privacy": {
                "model_path_recorded": False,
                "prompt_recorded": False,
                "image_bytes_in_json": False,
                "local_image_artifacts_retained": True,
                "process_id_recorded": False,
            },
            "complete": False,
        }

    def _persist(self) -> None:
        _atomic_write_json(self.report_path, self.report)

    def _phase(
        self,
        name: str,
        *,
        status: str,
        reason: str,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self.report["phases"][name] = {
            "status": status,
            "reason": reason,
            "details": dict(details or {}),
        }
        self._persist()

    def run(self) -> dict[str, Any]:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._persist()
        try:
            if not self._preflight():
                return self._finalize()
            if not self._verify_artifact():
                return self._finalize()

            self.server = self.server_factory(
                profile=self.profile,
                output_dir=self.output_dir,
                observer=self.observer,
                model_path=self.options.model_path,
            )
            try:
                startup, samples = self.server.start()
                self._all_samples.extend(samples)
                self._phase(
                    "server_start",
                    status=_PASS,
                    reason="owned loopback Korgis server reached readiness",
                    details={
                        **startup,
                        "resources": _summarize_samples(samples),
                    },
                )

                identity = self.server.runtime_identity()
                self.report["runtime_identity"] = identity
                self._phase(
                    "runtime_identity",
                    status=_PASS,
                    reason="path-free runtime identity captured",
                )

                for index in range(self.profile.repetitions):
                    if not self._generation(index):
                        break
            except Exception as exc:  # noqa: BLE001 - evidence retains failure
                self._phase(
                    "execution",
                    status=_FAIL,
                    reason=_safe_error(exc),
                )
            finally:
                self._stop_server()
        finally:
            self._finalize()
        return self.report

    def _preflight(self) -> bool:
        try:
            git = self.git_state()
            system = self.system().lower()
            machine = self.machine().lower()
            self.cfg = self.config_builder(
                model=self.profile.model,
                model_path=self.options.model_path,
                no_download=True,
            )
            initial = self.observer.snapshot()
        except Exception as exc:  # noqa: BLE001
            self._phase("preflight", status=_INCONCLUSIVE, reason=_safe_error(exc))
            return False

        checks = {
            "macos": (
                not self.profile.safety.require_macos
                or system == "darwin"
            ),
            "apple_silicon": (
                not self.profile.safety.require_apple_silicon
                or machine in {"arm64", "aarch64"}
            ),
            "clean_dev_checkout": (
                not self.profile.safety.require_clean_dev_checkout
                or (
                    git.get("branch") == "dev"
                    and git.get("tracked_clean") is True
                )
            ),
            "output_outside_repo": (
                not self.profile.safety.require_output_outside_repo
                or not _is_within(self.output_dir, Path(git["root"]))
            ),
            "loopback_only": self.profile.execution.host == "127.0.0.1",
            "port_free": _port_is_free(
                self.profile.execution.host,
                self.profile.execution.port,
            ),
        }

        envelope = resident_memory_envelope(self.cfg)
        lower_bound = envelope.accounted_bytes
        margin = int(self.profile.safety.host_safety_margin_gib * _GIB)
        available = initial.available_memory_bytes.value
        memory_guard = (
            isinstance(lower_bound, int)
            and lower_bound > 0
            and isinstance(available, int)
            and not isinstance(available, bool)
            and available >= lower_bound + margin
        )
        checks["resident_lower_bound_available"] = isinstance(lower_bound, int)
        checks["measured_available_memory"] = isinstance(available, int)
        checks["exploratory_host_safety_guard"] = memory_guard

        self.report["source"] = {
            "revision": git.get("revision"),
            "branch": git.get("branch"),
            "tracked_clean": git.get("tracked_clean"),
        }
        self.report["environment"] = {
            **local_environment_metadata(),
            "initial_resources": _snapshot(initial),
        }
        self._all_samples.append(initial)

        if not all(checks.values()):
            self._phase(
                "preflight",
                status=_INCONCLUSIVE,
                reason=(
                    "representative host/source/privacy/resource preconditions "
                    "were not satisfied"
                ),
                details={
                    "checks": checks,
                    "resident_lower_bound_bytes": lower_bound,
                    "resident_envelope_complete": envelope.complete,
                    "host_safety_margin_bytes": margin,
                },
            )
            return False

        self._phase(
            "preflight",
            status=_PASS,
            reason="representative image evidence preconditions satisfied",
            details={
                "checks": checks,
                "resident_lower_bound_bytes": lower_bound,
                "resident_envelope_complete": envelope.complete,
                "host_safety_margin_bytes": margin,
                "guard_is_memory_fit_claim": False,
            },
        )
        return True

    def _verify_artifact(self) -> bool:
        assert self.cfg is not None
        try:
            receipt = verified_receipt_for_config(
                self.cfg,
                store=self.verification_store,
            )
            reused = receipt is not None
            if receipt is None:
                receipt = verify_model_artifact(
                    self.profile.model,
                    model_path=self.options.model_path,
                    store=self.verification_store,
                )
            summary = public_verification_summary(receipt)
        except Exception as exc:  # noqa: BLE001
            self._phase(
                "artifact_verification",
                status=_INCONCLUSIVE,
                reason=_safe_error(exc),
            )
            return False

        self.report["artifact"] = summary
        self._phase(
            "artifact_verification",
            status=_PASS,
            reason=(
                "existing verified artifact receipt reused"
                if reused
                else "artifact manifest verified and receipt persisted"
            ),
            details={
                "receipt_reused": reused,
                "artifact_kind": summary.get("artifact_kind"),
                "file_count": summary.get("file_count"),
            },
        )
        return True

    def _generation(self, index: int) -> bool:
        assert self.server is not None
        workload = self.profile.workload
        payload = {
            "model": self.profile.model,
            "prompt": workload.prompt,
            "n": 1,
            "response_format": "b64_json",
            "size": f"{workload.width}x{workload.height}",
            "num_inference_steps": workload.num_inference_steps,
            "guidance_scale": workload.guidance_scale,
            "seed": workload.seed,
            "output_format": workload.output_format,
        }
        phase_name = f"generation_{index + 1:02d}"
        try:
            body, wall_latency_ms, samples = self.server.generate(payload)
            self._all_samples.extend(samples)
            artifact = self._persist_generated_image(index, body)
            korgis = body.get("korgis")
            korgis = dict(korgis) if isinstance(korgis, Mapping) else {}
            generation = {
                "index": index + 1,
                "status": _PASS,
                "wall_latency_ms": round(wall_latency_ms, 3),
                "korgis_latency_ms": korgis.get("latency_ms"),
                "runtime_key": korgis.get("runtime_key"),
                "backend": korgis.get("backend"),
                "seed": korgis.get("seed"),
                "width": korgis.get("width"),
                "height": korgis.get("height"),
                "generation_metadata": korgis.get("generation"),
                "artifact": artifact,
                "resources": _summarize_samples(samples),
            }
            self.report["generations"].append(generation)
            self._phase(
                phase_name,
                status=_PASS,
                reason="image generation completed and artifact was retained locally",
                details={
                    "artifact_sha256": artifact["sha256"],
                    "wall_latency_ms": generation["wall_latency_ms"],
                    "resources": generation["resources"],
                },
            )
            return True
        except Exception as exc:  # noqa: BLE001
            self.report["generations"].append(
                {
                    "index": index + 1,
                    "status": _FAIL,
                    "error": _safe_error(exc),
                }
            )
            self._phase(
                phase_name,
                status=_FAIL,
                reason=_safe_error(exc),
            )
            return False

    def _persist_generated_image(
        self,
        index: int,
        body: Mapping[str, Any],
    ) -> dict[str, Any]:
        data = body.get("data")
        if not isinstance(data, list) or not data or not isinstance(data[0], Mapping):
            raise ImageHardwareEvidenceError("image response contained no data")
        encoded = str(data[0].get("b64_json") or "")
        if not encoded:
            raise ImageHardwareEvidenceError("image response contained no base64 artifact")
        try:
            raw = base64.b64decode(encoded, validate=True)
        except ValueError as exc:
            raise ImageHardwareEvidenceError("image response base64 was invalid") from exc

        suffix = (
            "jpg"
            if self.profile.workload.output_format in {"jpg", "jpeg"}
            else self.profile.workload.output_format
        )
        relative = Path("images") / f"generation-{index + 1:02d}.{suffix}"
        target = self.output_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        return {
            "path": relative.as_posix(),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size_bytes": len(raw),
        }

    def _stop_server(self) -> None:
        if self.server is None:
            return
        try:
            details, samples = self.server.stop()
            self._all_samples.extend(samples)
            status = (
                _PASS
                if details.get("listener_closed") is True
                and details.get("hard_kill_required") is False
                else _FAIL
            )
            self._phase(
                "server_stop",
                status=status,
                reason=(
                    "owned image evidence server stopped cleanly"
                    if status == _PASS
                    else "owned image evidence server required unsafe cleanup"
                ),
                details={
                    **details,
                    "resources": _summarize_samples(samples),
                },
            )
        except Exception as exc:  # noqa: BLE001
            self._phase(
                "server_stop",
                status=_FAIL,
                reason=_safe_error(exc),
            )

    def _finalize(self) -> dict[str, Any]:
        self.report["completed_at"] = _utc_now()
        self.report["observations"] = _summarize_samples(self._all_samples)
        generations = self.report.get("generations") or []
        phases = self.report.get("phases") or {}
        self.report["complete"] = bool(
            len(generations) == self.profile.repetitions
            and all(item.get("status") == _PASS for item in generations)
            and phases.get("preflight", {}).get("status") == _PASS
            and phases.get("artifact_verification", {}).get("status") == _PASS
            and phases.get("server_start", {}).get("status") == _PASS
            and phases.get("runtime_identity", {}).get("status") == _PASS
            and phases.get("server_stop", {}).get("status") == _PASS
        )
        self._persist()
        return self.report


def execute_image_hardware_evidence(
    options: ImageHardwareEvidenceOptions,
    **kwargs: Any,
) -> dict[str, Any]:
    """Run one image evidence campaign; dependency hooks are for deterministic tests."""
    return ImageHardwareEvidenceCampaign(options, **kwargs).run()
