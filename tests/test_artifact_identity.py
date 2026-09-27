from __future__ import annotations

import hashlib

import pytest

from local_llm_server.artifact_identity import (
    ArtifactSourceKind,
    ArtifactVerificationReceipt,
    VerificationState,
    identify_resolved_artifact,
    sha256_directory_manifest,
    sha256_file,
)
from local_llm_server.model_sources import ResolvedModel


def test_explicit_file_can_be_verified_by_digest(tmp_path):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"model-bytes")
    resolved = ResolvedModel(str(model), model, "explicit", True)

    identity = identify_resolved_artifact(
        "demo",
        {"filename": "model.gguf", "model_id": "org/demo"},
        resolved,
        hash_local_file=True,
    )

    assert identity.source_kind is ArtifactSourceKind.EXPLICIT
    assert identity.sha256 == hashlib.sha256(b"model-bytes").hexdigest()
    assert identity.size_bytes == len(b"model-bytes")
    assert identity.verification is VerificationState.VERIFIED
    assert str(tmp_path) not in str(identity.stable_payload())


def test_available_directory_is_not_falsely_marked_verified(tmp_path):
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    resolved = ResolvedModel(str(snapshot), snapshot, "huggingface", True)

    identity = identify_resolved_artifact(
        "demo",
        {"model_id": "org/demo", "revision": "abc123"},
        resolved,
    )

    assert identity.source_ref == "org/demo"
    assert identity.revision == "abc123"
    assert identity.sha256 is None
    assert identity.verification is VerificationState.AVAILABLE_UNVERIFIED


def test_unavailable_artifact_is_explicit():
    resolved = ResolvedModel("org/demo", None, "huggingface", False)
    identity = identify_resolved_artifact("demo", {"model_id": "org/demo"}, resolved)
    assert identity.verification is VerificationState.UNAVAILABLE


def test_stable_key_changes_when_digest_changes(tmp_path):
    first = tmp_path / "first.gguf"
    second = tmp_path / "second.gguf"
    first.write_bytes(b"one")
    second.write_bytes(b"two")

    a = identify_resolved_artifact(
        "same",
        {"filename": "model.gguf"},
        ResolvedModel(str(first), first, "managed", True),
        hash_local_file=True,
    )
    b = identify_resolved_artifact(
        "same",
        {"filename": "model.gguf"},
        ResolvedModel(str(second), second, "managed", True),
        hash_local_file=True,
    )

    assert a.stable_key() != b.stable_key()


def test_sha256_file_is_deterministic(tmp_path):
    path = tmp_path / "artifact.bin"
    path.write_bytes(b"abc")
    assert sha256_file(path) == hashlib.sha256(b"abc").hexdigest()


def test_single_file_verification_receipt_reuses_strong_digest_while_stamp_matches(tmp_path):
    path = tmp_path / "model.gguf"
    path.write_bytes(b"verified-model")
    digest = sha256_file(path)

    receipt = ArtifactVerificationReceipt.for_file(
        "org/demo",
        path,
        sha256=digest,
    )

    assert len(receipt.sha256) == 64
    assert receipt.sha256 == digest
    assert receipt.matches_file() is True
    assert receipt.size_bytes == path.stat().st_size


def test_receipt_is_invalidated_by_ordinary_file_replacement(tmp_path):
    path = tmp_path / "model.gguf"
    path.write_bytes(b"first-model")
    receipt = ArtifactVerificationReceipt.for_file(
        "org/demo",
        path,
        sha256=sha256_file(path),
    )

    path.write_bytes(b"replacement-model-is-different-size")

    assert receipt.matches_file() is False


def test_receipt_round_trip_is_local_private_state(tmp_path):
    path = tmp_path / "model.gguf"
    path.write_bytes(b"model")
    receipt = ArtifactVerificationReceipt.for_file(
        "org/demo",
        path,
        sha256=sha256_file(path),
    )

    restored = ArtifactVerificationReceipt.from_private_payload(receipt.private_payload())

    assert restored == receipt
    assert restored.matches_file() is True
    assert str(path.resolve()) in str(restored.private_payload())


def test_single_file_receipt_refuses_directory_manifest_guess(tmp_path):
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()

    with pytest.raises(ValueError, match="regular file"):
        ArtifactVerificationReceipt.for_file(
            "org/demo",
            snapshot,
            sha256="a" * 64,
        )


def test_receipt_rejects_non_sha256_digest(tmp_path):
    path = tmp_path / "model.gguf"
    path.write_bytes(b"model")

    with pytest.raises(ValueError, match="64-character"):
        ArtifactVerificationReceipt.for_file(
            "org/demo",
            path,
            sha256="not-a-digest",
        )



def test_directory_manifest_digest_is_path_order_independent(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    for root in (first, second):
        (root / "b").mkdir(parents=True)
        (root / "a.txt").write_bytes(b"alpha")
        (root / "b" / "c.bin").write_bytes(b"charlie")

    first_digest, first_size, first_entries = sha256_directory_manifest(first)
    second_digest, second_size, second_entries = sha256_directory_manifest(second)

    assert first_digest == second_digest
    assert first_size == second_size == len(b"alpha") + len(b"charlie")
    assert [entry.relative_path for entry in first_entries] == ["a.txt", "b/c.bin"]
    assert [entry.digest_payload() for entry in first_entries] == [
        entry.digest_payload() for entry in second_entries
    ]


def test_directory_receipt_round_trip_and_content_change_invalidation(tmp_path):
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    (snapshot / "weights.bin").write_bytes(b"weights")
    (snapshot / "config.json").write_text("{}", encoding="utf-8")

    receipt = ArtifactVerificationReceipt.for_directory("org/demo", snapshot)
    restored = ArtifactVerificationReceipt.from_private_payload(
        receipt.private_payload()
    )

    assert restored == receipt
    assert restored.artifact_kind == "directory"
    assert restored.matches_artifact(snapshot) is True
    assert restored.matches_file(snapshot) is False

    (snapshot / "weights.bin").write_bytes(b"replacement-weights")
    assert restored.matches_artifact(snapshot) is False


def test_directory_receipt_invalidates_added_or_removed_files(tmp_path):
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    original = snapshot / "weights.bin"
    original.write_bytes(b"weights")
    receipt = ArtifactVerificationReceipt.for_directory("org/demo", snapshot)

    added = snapshot / "extra.json"
    added.write_text("{}", encoding="utf-8")
    assert receipt.matches_artifact(snapshot) is False

    added.unlink()
    assert receipt.matches_artifact(snapshot) is True

    original.unlink()
    assert receipt.matches_artifact(snapshot) is False



def test_legacy_single_file_receipt_payload_remains_backward_compatible(tmp_path):
    path = tmp_path / "model.gguf"
    path.write_bytes(b"legacy")
    receipt = ArtifactVerificationReceipt.for_file(
        "org/demo",
        path,
        sha256=sha256_file(path),
    )
    legacy = receipt.private_payload()
    legacy.pop("artifact_kind")
    legacy.pop("manifest_files")

    restored = ArtifactVerificationReceipt.from_private_payload(legacy)

    assert restored.artifact_kind == "file"
    assert restored.manifest_files == ()
    assert restored.matches_file(path) is True
