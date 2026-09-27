"""Public path-free runtime identity contract for external evaluators.

The endpoint is intentionally separate from dynamic ``/status`` telemetry. It
exposes stable model/runtime/config/hardware identity known by the serving
process while preserving unknown values instead of inferring them from paths or
filenames.
"""
from __future__ import annotations

from importlib import metadata
from typing import Any, Mapping

from fastapi import APIRouter, FastAPI, Request

from .runtime_evidence import attached_runtime_identity
from .runtime_identity import (
    BackendIdentity,
    local_hardware_profile,
    resolved_config_digest,
    resolved_config_payload,
)
from .runtime_identity_capture import resolve_backend_identity

LOCAL_LLM_IDENTITY_PROTOCOL_VERSION = "local-llm-identity-v1"

router = APIRouter()


@router.get("/v1/runtime/identity", tags=["System"])
def get_runtime_identity(request: Request) -> dict[str, object]:
    """Return identity for resident runtimes without private paths or content."""
    manager = request.app.state.runtime_manager
    runtimes = manager.list()
    return {
        "protocol_version": LOCAL_LLM_IDENTITY_PROTOCOL_VERSION,
        "server": {
            "name": "local-llm-server",
            "version": _server_version(),
        },
        "default_model": manager.default_model,
        "models": {
            runtime.key: _runtime_identity_payload(runtime)
            for runtime in runtimes
        },
    }


def install_identity_api(application: FastAPI) -> FastAPI:
    """Install the public identity route exactly once on the supported stack."""
    if getattr(application.state, "identity_api_installed", False):
        return application
    application.include_router(router)
    application.state.identity_api_installed = True
    return application


def _runtime_identity_payload(runtime: Any) -> dict[str, object]:
    cfg: Mapping[str, Any] = getattr(runtime, "cfg", {})
    resolved_backend = resolve_backend_identity(runtime)
    if resolved_backend is None:
        resolved_backend = BackendIdentity(
            name=str(cfg.get("backend") or getattr(runtime.engine, "backend", "unknown")),
            implementation=runtime.engine.__class__.__name__,
        )

    snapshot = attached_runtime_identity(runtime)
    sha256 = _valid_sha256_text(
        cfg.get("artifact_sha256")
        or getattr(runtime, "verified_artifact_sha256", None)
    )
    revision = _optional_text(cfg.get("artifact_revision"))
    quantization = _optional_text(cfg.get("quantization"))
    total_memory_bytes = _positive_int_or_none(cfg.get("hardware_total_memory_bytes"))
    accelerator = _optional_text(cfg.get("hardware_accelerator"))
    hardware = local_hardware_profile(
        total_memory_bytes=total_memory_bytes,
        accelerator=accelerator,
    )

    artifact_key: object | None = None
    if snapshot is not None:
        artifact_key = snapshot.payload.get("artifact_key")

    return {
        "model": {
            "id": str(getattr(runtime, "model_id", None) or cfg.get("model_id") or runtime.key),
            "revision": revision,
            "artifact_digest": f"sha256:{sha256}" if sha256 is not None else None,
            "artifact_key": artifact_key,
            "quantization": quantization,
            "verification": "verified" if sha256 is not None else "available_unverified",
        },
        "runtime": {
            "name": resolved_backend.name,
            "version": resolved_backend.version,
            "implementation": resolved_backend.implementation,
            "config_digest": resolved_config_digest(cfg),
            "config": resolved_config_payload(cfg),
            "fingerprint": snapshot.fingerprint if snapshot is not None else None,
            "captured_at": snapshot.captured_at if snapshot is not None else None,
            "evidence_grade": "verified" if snapshot is not None else "partial",
        },
        "hardware": hardware.stable_payload(),
    }


def _server_version() -> str:
    try:
        return metadata.version("local-llm-server")
    except metadata.PackageNotFoundError:
        return "unknown"


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _valid_sha256_text(value: object) -> str | None:
    text = _optional_text(value)
    if text is None:
        return None
    lowered = text.lower()
    if len(lowered) != 64 or any(ch not in "0123456789abcdef" for ch in lowered):
        return None
    return lowered


def _positive_int_or_none(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    return None
