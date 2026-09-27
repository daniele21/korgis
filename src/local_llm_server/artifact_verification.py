"""Explicit local artifact verification and receipt persistence.

Receipts are private machine-local state. Public identity consumes only the
strong digest after the receipt has been revalidated against the exact local
file stamp; filesystem paths never leave this module through public summaries.
"""
from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .artifact_identity import ArtifactVerificationReceipt, sha256_file
from .model_sources import resolve_registry_model
from .registry import load_registry

_ENV_RECEIPT_DIR = "LOCAL_LLM_ARTIFACT_VERIFICATION_DIR"


def default_receipt_dir() -> Path:
    configured = os.getenv(_ENV_RECEIPT_DIR)
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".local" / "share" / "local-llm-server" / "artifact-verification"


class ArtifactVerificationStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or default_receipt_dir()).expanduser()

    def save(self, receipt: ArtifactVerificationReceipt) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        target = self._path_for(receipt.logical_id)
        temp = target.with_suffix(".json.tmp")
        temp.write_text(
            json.dumps(receipt.private_payload(), sort_keys=True, indent=2),
            encoding="utf-8",
        )
        os.replace(temp, target)
        return target

    def load(self, logical_id: str) -> ArtifactVerificationReceipt | None:
        path = self._path_for(logical_id)
        if not path.is_file():
            return None
        try:
            payload: Any = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return None
            receipt = ArtifactVerificationReceipt.from_private_payload(payload)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None
        return receipt if receipt.logical_id == logical_id else None

    def valid_for_artifact(
        self,
        logical_id: str,
        artifact_path: str | Path,
    ) -> ArtifactVerificationReceipt | None:
        receipt = self.load(logical_id)
        if receipt is None or not receipt.matches_artifact(artifact_path):
            return None
        return receipt

    def valid_for_file(
        self,
        logical_id: str,
        artifact_path: str | Path,
    ) -> ArtifactVerificationReceipt | None:
        """Backwards-compatible single-file receipt lookup."""
        receipt = self.valid_for_artifact(logical_id, artifact_path)
        if receipt is None or receipt.artifact_kind != "file":
            return None
        return receipt

    def _path_for(self, logical_id: str) -> Path:
        key = hashlib.sha256(logical_id.encode("utf-8")).hexdigest()
        return self.root / f"{key}.json"


def verified_receipt_for_config(
    config: Mapping[str, Any],
    *,
    store: ArtifactVerificationStore | None = None,
) -> ArtifactVerificationReceipt | None:
    """Return the ID-2 receipt only when it still matches this exact runtime artifact."""
    logical_id = str(config.get("model_id") or config.get("model") or "").strip()
    model_path = config.get("model_path")
    if not logical_id or not isinstance(model_path, str) or not model_path:
        return None
    artifact = Path(model_path).expanduser()
    if not artifact.exists():
        return None
    return (store or ArtifactVerificationStore()).valid_for_artifact(
        logical_id,
        artifact,
    )


def verify_model_artifact(
    model: str,
    *,
    model_path: str | None = None,
    store: ArtifactVerificationStore | None = None,
) -> ArtifactVerificationReceipt:
    """Hash one resolved local file or directory model artifact and persist its receipt."""
    registry = load_registry()
    entry = registry["models"].get(model)
    if not isinstance(entry, dict):
        raise ValueError(f"Unknown model: {model}")

    backend = str(entry.get("backend") or entry.get("params", {}).get("backend") or "llama_cpp")
    resolved = resolve_registry_model(
        model,
        entry,
        registry["models_dir"],
        backend=backend,
        explicit_path=model_path,
    )
    artifact = resolved.local_path
    if artifact is None or not resolved.downloaded:
        raise FileNotFoundError(
            f"Model '{model}' does not resolve to a complete local artifact."
        )
    logical_id = str(entry.get("model_id") or model)
    if artifact.is_file():
        receipt = ArtifactVerificationReceipt.for_file(
            logical_id,
            artifact,
            sha256=sha256_file(artifact),
        )
    elif artifact.is_dir():
        receipt = ArtifactVerificationReceipt.for_directory(
            logical_id,
            artifact,
        )
    else:
        raise ValueError("resolved model artifact must be a regular file or directory")
    (store or ArtifactVerificationStore()).save(receipt)
    return receipt


def public_verification_summary(receipt: ArtifactVerificationReceipt) -> dict[str, object]:
    """Return path-free CLI/API-safe verification evidence."""
    return {
        "logical_id": receipt.logical_id,
        "sha256": receipt.sha256,
        "size_bytes": receipt.size_bytes,
        "verification": "verified",
        "artifact_kind": receipt.artifact_kind,
        "file_count": (
            len(receipt.manifest_files)
            if receipt.artifact_kind == "directory"
            else 1
        ),
    }
