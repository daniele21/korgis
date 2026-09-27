from __future__ import annotations

from types import SimpleNamespace

from local_llm_server.product_runtime_manager import ProductRuntimeManager
from local_llm_server.runtime_evidence import attached_runtime_identity
from local_llm_server.runtime_identity import BackendIdentity
from local_llm_server.runtime_identity_capture import capture_verified_runtime_identity


class _Engine:
    backend = "llama_cpp"

    def close(self):
        pass


def _runtime_cfg(*, sha256=None):
    return {
        "model": "demo",
        "model_id": "org/demo",
        "model_path": "/private/models/demo.gguf",
        "model_source": "managed",
        "backend": "llama_cpp",
        "ctx_size": 4096,
        "max_concurrent_requests": 1,
        "artifact_sha256": sha256,
        "artifact_revision": "rev-1",
    }


def _make_executable(path):
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def test_missing_artifact_sha_keeps_runtime_exploratory(monkeypatch):
    runtime = SimpleNamespace(
        key="demo",
        cfg=_runtime_cfg(sha256=None),
        engine=_Engine(),
    )

    snapshot = capture_verified_runtime_identity(runtime)

    assert snapshot is None
    assert attached_runtime_identity(runtime) is None


def test_unversioned_backend_keeps_runtime_exploratory(monkeypatch):
    monkeypatch.setattr(
        "local_llm_server.runtime_identity_capture.backend_identity",
        lambda *args, **kwargs: BackendIdentity(
            name="llama_cpp",
            implementation="_Engine",
            version=None,
        ),
    )
    runtime = SimpleNamespace(
        key="demo",
        cfg=_runtime_cfg(sha256="a" * 64),
        engine=_Engine(),
    )

    snapshot = capture_verified_runtime_identity(runtime)

    assert snapshot is None
    assert attached_runtime_identity(runtime) is None


def test_verified_artifact_plus_backend_version_attaches_privacy_safe_identity(monkeypatch):
    monkeypatch.setattr(
        "local_llm_server.runtime_identity_capture.backend_identity",
        lambda *args, **kwargs: BackendIdentity(
            name="llama_cpp",
            implementation="_Engine",
            version="1.2.3",
        ),
    )
    runtime = SimpleNamespace(
        key="demo",
        cfg=_runtime_cfg(sha256="a" * 64),
        engine=_Engine(),
    )

    snapshot = capture_verified_runtime_identity(runtime)

    assert snapshot is not None
    assert len(snapshot.fingerprint) == 64
    assert attached_runtime_identity(runtime) is snapshot
    rendered = str(snapshot.to_public_dict())
    assert "/private/models" not in rendered
    assert "demo.gguf" not in rendered
    assert "1.2.3" in rendered


def test_unknown_backend_can_use_explicit_version_without_package_probe():
    cfg = _runtime_cfg(sha256="b" * 64)
    cfg["backend"] = "asr_custom"
    cfg["backend_version"] = "2026.08"
    runtime = SimpleNamespace(
        key="demo",
        cfg=cfg,
        engine=SimpleNamespace(backend="asr_custom"),
    )

    snapshot = capture_verified_runtime_identity(runtime)

    assert snapshot is not None
    public = snapshot.to_public_dict()
    assert public["identity"]["backend"]["name"] == "asr_custom"
    assert public["identity"]["backend"]["version"] == "2026.08"


def test_llama_server_engine_identity_is_used_without_reprobe(monkeypatch):
    cfg = _runtime_cfg(sha256="c" * 64)
    cfg["backend"] = "llama_server"
    engine = SimpleNamespace(
        backend="llama_server",
        binary="/private/bin/llama-server",
        backend_version="build-10621@c1d0e7a",
    )
    runtime = SimpleNamespace(key="demo", cfg=cfg, engine=engine)

    monkeypatch.setattr(
        "local_llm_server.runtime_identity_capture.probe_llama_server_version",
        lambda _binary: (_ for _ in ()).throw(AssertionError("must not re-probe")),
    )

    snapshot = capture_verified_runtime_identity(runtime)

    assert snapshot is not None
    rendered = snapshot.to_public_dict()
    assert rendered["identity"]["backend"]["version"] == "build-10621@c1d0e7a"
    assert "/private/bin" not in str(rendered)


def test_llama_server_version_probe_uses_build_and_commit_only(monkeypatch, tmp_path):
    cfg = _runtime_cfg(sha256="d" * 64)
    cfg["backend"] = "llama_server"
    binary = _make_executable(tmp_path / "llama-server")
    engine = SimpleNamespace(
        backend="llama_server",
        binary=str(binary),
    )
    runtime = SimpleNamespace(key="demo", cfg=cfg, engine=engine)

    monkeypatch.setattr(
        "local_llm_server.llama_server_compat.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            stdout="version: 10621 (`c1d0e7a`)\nbuilt with AppleClang for Darwin arm64\n",
            stderr="",
            returncode=0,
        ),
    )

    snapshot = capture_verified_runtime_identity(runtime)

    assert snapshot is not None
    rendered = snapshot.to_public_dict()
    assert rendered["identity"]["backend"]["version"] == "build-10621@c1d0e7a"
    assert str(tmp_path) not in str(rendered)


def test_unparseable_llama_server_version_keeps_runtime_exploratory(monkeypatch, tmp_path):
    cfg = _runtime_cfg(sha256="e" * 64)
    cfg["backend"] = "llama_server"
    binary = _make_executable(tmp_path / "llama-server")
    runtime = SimpleNamespace(
        key="demo",
        cfg=cfg,
        engine=SimpleNamespace(backend="llama_server", binary=str(binary)),
    )
    monkeypatch.setattr(
        "local_llm_server.llama_server_compat.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            stdout="unknown build output",
            stderr="",
            returncode=0,
        ),
    )

    assert capture_verified_runtime_identity(runtime) is None


def test_product_runtime_manager_attempts_capture_after_residency(monkeypatch):
    captured = []
    monkeypatch.setattr(
        "local_llm_server.runtime_identity_capture.capture_verified_runtime_identity",
        lambda runtime: captured.append(runtime.key),
    )
    manager = ProductRuntimeManager(default_model="demo")

    runtime = manager.add(_runtime_cfg(), _Engine())

    assert runtime.key == "demo"
    assert captured == ["demo"]
    assert manager.default_model == "demo"
    assert manager.configured_default_model == "demo"

def test_image_backend_identity_resolves_package_versions(monkeypatch):
    class _Engine:
        backend = "mflux_image"

    runtime = SimpleNamespace(
        cfg={"backend": "mflux_image"},
        engine=_Engine(),
    )
    monkeypatch.setattr(
        "local_llm_server.runtime_identity.metadata.version",
        lambda package: "0.20.0" if package == "mflux" else "unknown",
    )

    identity = resolve_backend_identity(runtime)

    assert identity is not None
    assert identity.name == "mflux_image"
    assert identity.version == "0.20.0"
    assert identity.implementation == "_Engine"
