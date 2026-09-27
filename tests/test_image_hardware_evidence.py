from __future__ import annotations

import base64
from pathlib import Path

from local_llm_server.artifact_identity import ArtifactVerificationReceipt
from local_llm_server.artifact_verification import ArtifactVerificationStore
from local_llm_server.image_hardware_evidence import (
    ImageHardwareEvidenceOptions,
    execute_image_hardware_evidence,
)
from local_llm_server.resources import (
    ResourceValue,
    ResourceValueSource,
    SystemResourceSnapshot,
)


def _measured(value: int) -> ResourceValue:
    return ResourceValue(value, ResourceValueSource.MEASURED, "bytes")


def _unavailable() -> ResourceValue:
    return ResourceValue.unavailable("bytes")


def _snapshot(*, rss: int | None, available: int) -> SystemResourceSnapshot:
    return SystemResourceSnapshot(
        captured_at_monotonic=1.0,
        platform="darwin",
        total_memory_bytes=_measured(64 * 1024**3),
        available_memory_bytes=_measured(available),
        process_rss_bytes=_measured(rss) if rss is not None else _unavailable(),
    )


class _Observer:
    def __init__(self) -> None:
        self.bound = None

    def bind_worker(self, worker) -> None:
        self.bound = worker

    def unbind_worker(self, worker) -> None:
        if self.bound is worker:
            self.bound = None

    def snapshot(self) -> SystemResourceSnapshot:
        return _snapshot(rss=None, available=60 * 1024**3)


class _FakeServer:
    def __init__(
        self,
        *,
        profile,
        output_dir,
        observer,
        model_path,
        fail_generation: int | None = None,
    ) -> None:
        del output_dir, model_path
        self.profile = profile
        self.observer = observer
        self.fail_generation = fail_generation
        self.calls = 0

    def start(self):
        return (
            {
                "wall_seconds": 3.5,
                "listener": "loopback",
                "log_retained_locally": True,
            },
            [
                _snapshot(rss=12 * 1024**3, available=52 * 1024**3),
                _snapshot(rss=20 * 1024**3, available=44 * 1024**3),
            ],
        )

    def runtime_identity(self):
        return {
            "protocol_version": "local-llm-identity-v1",
            "models": {
                self.profile.model: {
                    "model": {
                        "id": "mflux-community/qwen-image-2-1-mflux-q8",
                        "quantization": "Q8",
                    },
                    "runtime": {
                        "name": "mflux_image",
                        "version": "0.20.0",
                        "evidence_grade": "verified",
                    },
                }
            },
        }

    def generate(self, payload):
        self.calls += 1
        if self.fail_generation == self.calls:
            raise RuntimeError("synthetic generation failure")
        assert payload["prompt"]
        raw = f"png-{self.calls}".encode()
        body = {
            "data": [{"b64_json": base64.b64encode(raw).decode()}],
            "korgis": {
                "runtime_key": self.profile.model,
                "backend": "mflux_image",
                "seed": payload["seed"],
                "width": 1024,
                "height": 1024,
                "latency_ms": 1200.0 + self.calls,
                "generation": {
                    "runtime": "mflux",
                    "quantization_bits": 8,
                },
            },
        }
        samples = [
            _snapshot(
                rss=(24 + self.calls) * 1024**3,
                available=(40 - self.calls) * 1024**3,
            ),
            _snapshot(
                rss=(28 + self.calls) * 1024**3,
                available=(36 - self.calls) * 1024**3,
            ),
        ]
        return body, 1300.0 + self.calls, samples

    def stop(self):
        return (
            {
                "owned_process_started": True,
                "exit_code": 0,
                "graceful": True,
                "hard_kill_required": False,
                "listener_closed": True,
            },
            [_snapshot(rss=None, available=54 * 1024**3)],
        )


def _setup(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    output = tmp_path / "evidence"
    snapshot = tmp_path / "qwen-q8"
    snapshot.mkdir()
    (snapshot / "weights.bin").write_bytes(b"weights")
    (snapshot / "config.json").write_text("{}", encoding="utf-8")

    store = ArtifactVerificationStore(tmp_path / "receipts")
    store.save(
        ArtifactVerificationReceipt.for_directory(
            "mflux-community/qwen-image-2-1-mflux-q8",
            snapshot,
        )
    )
    return repo, output, snapshot, store


def _config(snapshot: Path):
    return {
        "model": "qwen-image-2.1-mflux-q8",
        "model_id": "mflux-community/qwen-image-2-1-mflux-q8",
        "model_path": str(snapshot),
        "model_source": "huggingface",
        "backend": "mflux_image",
        "quantization": "Q8",
        "resource_model_weights_bytes": 24 * 1024**3,
        "resource_backend_overhead_bytes": None,
        "resource_context_cache_bytes": None,
        "resource_prompt_cache_bytes": None,
        "resource_projector_bytes": None,
        "resource_safety_margin_bytes": None,
    }


def test_image_hardware_evidence_retains_artifacts_and_bounded_measurements(tmp_path):
    repo, output, snapshot, store = _setup(tmp_path)
    observer = _Observer()

    report = execute_image_hardware_evidence(
        ImageHardwareEvidenceOptions(
            profile="qwen-image-2.1-mflux-q8-smoke-v1",
            output_dir=output,
        ),
        verification_store=store,
        observer=observer,
        server_factory=_FakeServer,
        config_builder=lambda **_kwargs: _config(snapshot),
        git_state=lambda: {
            "revision": "a" * 40,
            "branch": "dev",
            "tracked_clean": True,
            "root": repo,
        },
        system=lambda: "Darwin",
        machine=lambda: "arm64",
    )

    assert report["complete"] is True
    assert report["profile"]["source_kind"] == "builtin"
    assert len(report["profile"]["configuration_sha256"]) == 64
    assert report["profile"]["safety"]["require_macos"] is True
    assert report["artifact"]["verification"] == "verified"
    assert report["artifact"]["artifact_kind"] == "directory"
    assert report["runtime_identity"]["protocol_version"] == "local-llm-identity-v1"
    assert len(report["generations"]) == 2
    assert report["observations"]["peak_process_rss_bytes"] == 30 * 1024**3
    assert report["observations"]["minimum_available_memory_bytes"] == 34 * 1024**3
    assert all(value is False for value in report["claims"].values())

    serialized = (output / "image-evidence.json").read_text(encoding="utf-8")
    assert "A clean studio photograph" not in serialized
    assert "b64_json" not in serialized
    assert str(snapshot) not in serialized

    first = output / report["generations"][0]["artifact"]["path"]
    second = output / report["generations"][1]["artifact"]["path"]
    assert first.read_bytes() == b"png-1"
    assert second.read_bytes() == b"png-2"


def test_image_hardware_evidence_is_incomplete_on_generation_failure(tmp_path):
    repo, output, snapshot, store = _setup(tmp_path)

    def server_factory(**kwargs):
        return _FakeServer(**kwargs, fail_generation=2)

    report = execute_image_hardware_evidence(
        ImageHardwareEvidenceOptions(
            profile="qwen-image-2.1-mflux-q8-smoke-v1",
            output_dir=output,
        ),
        verification_store=store,
        observer=_Observer(),
        server_factory=server_factory,
        config_builder=lambda **_kwargs: _config(snapshot),
        git_state=lambda: {
            "revision": "b" * 40,
            "branch": "dev",
            "tracked_clean": True,
            "root": repo,
        },
        system=lambda: "Darwin",
        machine=lambda: "arm64",
    )

    assert report["complete"] is False
    assert report["generations"][0]["status"] == "PASS"
    assert report["generations"][1]["status"] == "FAIL"
    assert report["phases"]["server_stop"]["status"] == "PASS"
    assert "synthetic generation failure" in report["generations"][1]["error"]


def test_image_hardware_evidence_refuses_insufficient_available_memory(tmp_path):
    repo, output, snapshot, store = _setup(tmp_path)

    class LowMemoryObserver(_Observer):
        def snapshot(self):
            return _snapshot(rss=None, available=30 * 1024**3)

    report = execute_image_hardware_evidence(
        ImageHardwareEvidenceOptions(
            profile="qwen-image-2.1-mflux-q8-smoke-v1",
            output_dir=output,
        ),
        verification_store=store,
        observer=LowMemoryObserver(),
        server_factory=_FakeServer,
        config_builder=lambda **_kwargs: _config(snapshot),
        git_state=lambda: {
            "revision": "c" * 40,
            "branch": "dev",
            "tracked_clean": True,
            "root": repo,
        },
        system=lambda: "Darwin",
        machine=lambda: "arm64",
    )

    assert report["complete"] is False
    assert report["phases"]["preflight"]["status"] == "INCONCLUSIVE"
    checks = report["phases"]["preflight"]["details"]["checks"]
    assert checks["exploratory_host_safety_guard"] is False
    assert "server_start" not in report["phases"]



def test_image_hardware_evidence_refuses_in_repo_output_without_residue(tmp_path):
    repo, _output, snapshot, store = _setup(tmp_path)
    output = repo / "evidence"

    try:
        execute_image_hardware_evidence(
            ImageHardwareEvidenceOptions(
                profile="qwen-image-2.1-mflux-q8-smoke-v1",
                output_dir=output,
            ),
            verification_store=store,
            observer=_Observer(),
            server_factory=_FakeServer,
            config_builder=lambda **_kwargs: _config(snapshot),
            git_state=lambda: {
                "revision": "d" * 40,
                "branch": "dev",
                "tracked_clean": True,
                "root": repo,
            },
            system=lambda: "Darwin",
            machine=lambda: "arm64",
        )
    except RuntimeError as exc:
        assert "outside the repository" in str(exc)
    else:
        raise AssertionError("expected in-repository evidence output to fail")

    assert not output.exists()
