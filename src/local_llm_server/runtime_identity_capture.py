"""Controlled automatic runtime-identity capture for evidence-grade runs.

Automatic attachment is deliberately conservative: a runtime receives a
product fingerprint only when the model artifact has strong SHA-256 evidence
and the backend implementation version can be resolved. Strong artifact
evidence may come from an explicit config pin or from the same locally persisted
verification receipt consumed by hardware evidence.
"""
from __future__ import annotations

from typing import Any, Mapping

from .artifact_identity import (
    ArtifactIdentity,
    ArtifactSourceKind,
    VerificationState,
)
from .artifact_verification import ArtifactVerificationStore, verified_receipt_for_config
from .llama_server_compat import probe_llama_server_version
from .runtime_evidence import RuntimeIdentitySnapshot, build_and_attach_runtime_identity
from .runtime_identity import BackendIdentity, backend_identity, local_hardware_profile


_BACKEND_PACKAGES: dict[str, str] = {
    "llama_cpp": "llama-cpp-python",
    "mlx": "mlx-lm",
    "mlx_vlm_server": "mlx-vlm",
    "diffusers_image": "diffusers",
    "mflux_image": "mflux",
}


def capture_verified_runtime_identity(
    runtime: Any,
    *,
    total_memory_bytes: int | None = None,
    accelerator: str | None = None,
    verification_store: ArtifactVerificationStore | None = None,
) -> RuntimeIdentitySnapshot | None:
    """Attach identity once when artifact + backend evidence is strong enough."""
    cfg: Mapping[str, Any] = getattr(runtime, "cfg", {})
    logical_id = str(cfg.get("model_id") or cfg.get("model") or runtime.key)
    sha256, size_bytes = _verified_artifact_evidence(
        cfg,
        store=verification_store,
    )
    if sha256 is None:
        return None

    backend = resolve_backend_identity(runtime)
    if backend is None or backend.version is None:
        return None

    source_kind = _source_kind(cfg.get("model_source"))
    artifact = ArtifactIdentity(
        logical_id=logical_id,
        source_kind=source_kind,
        source_ref=logical_id,
        revision=(
            str(cfg["artifact_revision"])
            if cfg.get("artifact_revision") is not None
            else None
        ),
        sha256=sha256,
        size_bytes=size_bytes,
        verification=VerificationState.VERIFIED,
    )

    return build_and_attach_runtime_identity(
        runtime,
        artifact_key=artifact.stable_key(),
        backend=backend,
        hardware=local_hardware_profile(
            total_memory_bytes=total_memory_bytes,
            accelerator=accelerator,
        ),
        resolved_config=cfg,
    )


def resolve_backend_identity(runtime: Any) -> BackendIdentity | None:
    """Resolve the effective backend identity without requiring artifact verification."""
    cfg: Mapping[str, Any] = getattr(runtime, "cfg", {})
    backend_name = str(cfg.get("backend") or getattr(runtime.engine, "backend", "unknown"))
    implementation = runtime.engine.__class__.__name__
    package_name = _BACKEND_PACKAGES.get(backend_name)
    if package_name is not None:
        resolved = backend_identity(
            backend_name,
            package_name=package_name,
            implementation=implementation,
        )
        if resolved.version is not None:
            return resolved

    explicit = cfg.get("backend_version") or getattr(runtime.engine, "backend_version", None)
    if isinstance(explicit, str) and explicit.strip():
        return BackendIdentity(
            name=backend_name,
            version=explicit.strip(),
            implementation=implementation,
        )

    if backend_name == "llama_server":
        identity = probe_llama_server_version(getattr(runtime.engine, "binary", None))
        if identity is not None:
            return BackendIdentity(
                name=backend_name,
                version=identity.backend_version,
                implementation=implementation,
            )
    return None


def _verified_artifact_evidence(
    cfg: Mapping[str, Any],
    *,
    store: ArtifactVerificationStore | None,
) -> tuple[str | None, int | None]:
    explicit = cfg.get("artifact_sha256")
    if isinstance(explicit, str) and _valid_sha256(explicit):
        return explicit.lower(), _optional_size(cfg.get("artifact_size_bytes"))

    receipt = verified_receipt_for_config(cfg, store=store)
    if receipt is None:
        return None, None
    return receipt.sha256, receipt.size_bytes


def _source_kind(value: Any) -> ArtifactSourceKind:
    try:
        return ArtifactSourceKind(str(value))
    except ValueError:
        return ArtifactSourceKind.UNRESOLVED


def _valid_sha256(value: str) -> bool:
    return len(value) == 64 and all(ch in "0123456789abcdef" for ch in value.lower())


def _optional_size(value: Any) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None