from __future__ import annotations

import pytest

from local_llm_server.evidence_profiles import (
    ImageEvidenceProfile,
    load_image_evidence_profile,
)


def test_builtin_qwen_image_evidence_profile_is_typed_and_claim_free():
    profile = load_image_evidence_profile(
        "qwen-image-2.1-mflux-q8-smoke-v1"
    )

    assert isinstance(profile, ImageEvidenceProfile)
    assert profile.model == "qwen-image-2.1-mflux-q8"
    assert profile.repetitions == 2
    assert profile.workload.width == 1024
    assert profile.workload.height == 1024
    assert profile.workload.num_inference_steps == 40
    assert profile.workload.guidance_scale == 1.0
    assert profile.workload.seed == 42
    assert profile.safety.host_safety_margin_gib == 8.0
    assert all(value is False for value in profile.claims.values())


def test_explicit_profile_cannot_pre_authorize_claims(tmp_path):
    path = tmp_path / "unsafe.yaml"
    path.write_text(
        """
schema_version: 1
profile:
  id: unsafe
  model: demo
  repetitions: 1
  sample_interval_seconds: 0.1
  settle_seconds: 0
  startup_timeout_seconds: 10
  request_timeout_seconds: 10
  workload:
    prompt: test
    width: 512
    height: 512
    num_inference_steps: 2
    guidance_scale: 1.0
    seed: 1
    output_format: png
  safety:
    host_safety_margin_gib: 1
    require_macos: false
    require_apple_silicon: false
    require_clean_dev_checkout: false
    require_output_outside_repo: false
  claims:
    performance_claim: true
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="cannot pre-authorize claims"):
        load_image_evidence_profile(path)


def test_unknown_builtin_profile_fails_closed():
    with pytest.raises(FileNotFoundError, match="Unknown image evidence profile"):
        load_image_evidence_profile("does-not-exist")
