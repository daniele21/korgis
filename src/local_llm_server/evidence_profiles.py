"""Versioned configuration for representative hardware evidence workloads."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, Mapping

import yaml


@dataclass(frozen=True, slots=True)
class ImageEvidenceWorkload:
    prompt: str
    width: int
    height: int
    num_inference_steps: int
    guidance_scale: float
    seed: int
    output_format: str

    def __post_init__(self) -> None:
        if not self.prompt.strip():
            raise ValueError("evidence workload prompt must be non-empty")
        if self.width < 1 or self.height < 1:
            raise ValueError("evidence image dimensions must be positive")
        if self.num_inference_steps < 1:
            raise ValueError("evidence inference steps must be positive")
        if self.output_format not in {"png", "jpeg", "jpg", "webp"}:
            raise ValueError("unsupported evidence image output format")


@dataclass(frozen=True, slots=True)
class ImageEvidenceExecution:
    host: str
    port: int

    def __post_init__(self) -> None:
        if not self.host.strip():
            raise ValueError("evidence host must be non-empty")
        if self.port < 1 or self.port > 65535:
            raise ValueError("evidence port must be between 1 and 65535")


@dataclass(frozen=True, slots=True)
class ImageEvidenceSafety:
    host_safety_margin_gib: float
    require_macos: bool
    require_apple_silicon: bool
    require_clean_dev_checkout: bool
    require_output_outside_repo: bool

    def __post_init__(self) -> None:
        if self.host_safety_margin_gib < 0:
            raise ValueError("host_safety_margin_gib must be >= 0")


@dataclass(frozen=True, slots=True)
class ImageEvidenceProfile:
    profile_id: str
    source_kind: str
    model: str
    repetitions: int
    sample_interval_seconds: float
    settle_seconds: float
    startup_timeout_seconds: float
    request_timeout_seconds: float
    execution: ImageEvidenceExecution
    workload: ImageEvidenceWorkload
    safety: ImageEvidenceSafety
    claims: Mapping[str, bool]

    def __post_init__(self) -> None:
        if not self.profile_id.strip():
            raise ValueError("evidence profile id must be non-empty")
        if self.source_kind not in {"builtin", "explicit"}:
            raise ValueError("evidence profile source_kind must be builtin or explicit")
        if not self.model.strip():
            raise ValueError("evidence profile model must be non-empty")
        if self.repetitions < 1:
            raise ValueError("evidence repetitions must be >= 1")
        for name, value in (
            ("sample_interval_seconds", self.sample_interval_seconds),
            ("settle_seconds", self.settle_seconds),
            ("startup_timeout_seconds", self.startup_timeout_seconds),
            ("request_timeout_seconds", self.request_timeout_seconds),
        ):
            if value < 0:
                raise ValueError(f"{name} must be >= 0")
        if self.startup_timeout_seconds == 0 or self.request_timeout_seconds == 0:
            raise ValueError("evidence startup/request timeouts must be > 0")
        if not self.claims:
            raise ValueError("evidence profile must declare explicit claim flags")
        enabled = [name for name, value in self.claims.items() if value]
        if enabled:
            raise ValueError(
                "measurement profiles cannot pre-authorize claims: "
                + ", ".join(sorted(enabled))
            )

    def configuration_digest(self) -> str:
        """Hash the exact effective workload/safety configuration without exposing it."""
        payload = {
            "profile_id": self.profile_id,
            "model": self.model,
            "repetitions": self.repetitions,
            "sample_interval_seconds": self.sample_interval_seconds,
            "settle_seconds": self.settle_seconds,
            "startup_timeout_seconds": self.startup_timeout_seconds,
            "request_timeout_seconds": self.request_timeout_seconds,
            "execution": {
                "host": self.execution.host,
                "port": self.execution.port,
            },
            "workload": {
                "prompt": self.workload.prompt,
                "width": self.workload.width,
                "height": self.workload.height,
                "num_inference_steps": self.workload.num_inference_steps,
                "guidance_scale": self.workload.guidance_scale,
                "seed": self.workload.seed,
                "output_format": self.workload.output_format,
            },
            "safety": {
                "host_safety_margin_gib": self.safety.host_safety_margin_gib,
                "require_macos": self.safety.require_macos,
                "require_apple_silicon": self.safety.require_apple_silicon,
                "require_clean_dev_checkout": self.safety.require_clean_dev_checkout,
                "require_output_outside_repo": self.safety.require_output_outside_repo,
            },
            "claims": dict(sorted(self.claims.items())),
        }
        canonical = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()


def load_image_evidence_profile(
    profile: str | Path,
) -> ImageEvidenceProfile:
    """Load a built-in profile ID or an explicit YAML path."""
    path = Path(profile).expanduser()
    if path.is_file():
        source_kind = "explicit"
        payload = _load_yaml(path.read_text(encoding="utf-8"))
    else:
        source_kind = "builtin"
        filename = str(profile)
        if not filename.endswith(".yaml"):
            filename += ".yaml"
        resource = resources.files("local_llm_server").joinpath(
            "evidence_profiles",
            filename,
        )
        if not resource.is_file():
            raise FileNotFoundError(f"Unknown image evidence profile: {profile}")
        payload = _load_yaml(resource.read_text(encoding="utf-8"))

    if payload.get("schema_version") != 1:
        raise ValueError("image evidence profile schema_version must be 1")
    raw = _mapping(payload.get("profile"), "profile")
    execution = _mapping(raw.get("execution"), "profile.execution")
    workload = _mapping(raw.get("workload"), "profile.workload")
    safety = _mapping(raw.get("safety"), "profile.safety")
    claims = _mapping(raw.get("claims"), "profile.claims")

    return ImageEvidenceProfile(
        profile_id=_required_text(raw, "id"),
        source_kind=source_kind,
        model=_required_text(raw, "model"),
        repetitions=_required_int(raw, "repetitions"),
        sample_interval_seconds=_required_float(raw, "sample_interval_seconds"),
        settle_seconds=_required_float(raw, "settle_seconds"),
        startup_timeout_seconds=_required_float(raw, "startup_timeout_seconds"),
        request_timeout_seconds=_required_float(raw, "request_timeout_seconds"),
        execution=ImageEvidenceExecution(
            host=_required_text(execution, "host"),
            port=_required_int(execution, "port"),
        ),
        workload=ImageEvidenceWorkload(
            prompt=_required_text(workload, "prompt"),
            width=_required_int(workload, "width"),
            height=_required_int(workload, "height"),
            num_inference_steps=_required_int(workload, "num_inference_steps"),
            guidance_scale=_required_float(workload, "guidance_scale"),
            seed=_required_int(workload, "seed"),
            output_format=_required_text(workload, "output_format").lower(),
        ),
        safety=ImageEvidenceSafety(
            host_safety_margin_gib=_required_float(
                safety,
                "host_safety_margin_gib",
            ),
            require_macos=_required_bool(safety, "require_macos"),
            require_apple_silicon=_required_bool(
                safety,
                "require_apple_silicon",
            ),
            require_clean_dev_checkout=_required_bool(
                safety,
                "require_clean_dev_checkout",
            ),
            require_output_outside_repo=_required_bool(
                safety,
                "require_output_outside_repo",
            ),
        ),
        claims={
            str(key): _coerce_bool(value, f"profile.claims.{key}")
            for key, value in claims.items()
        },
    )


def _load_yaml(text: str) -> dict[str, Any]:
    payload = yaml.safe_load(text)
    if not isinstance(payload, dict):
        raise TypeError("image evidence profile root must be a mapping")
    return payload


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    return value


def _required_text(mapping: Mapping[str, Any], key: str) -> str:
    value = str(mapping.get(key) or "").strip()
    if not value:
        raise ValueError(f"{key} must be non-empty")
    return value


def _required_int(mapping: Mapping[str, Any], key: str) -> int:
    value = mapping.get(key)
    if isinstance(value, bool):
        raise ValueError(f"{key} must be an integer")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be an integer") from exc


def _required_float(mapping: Mapping[str, Any], key: str) -> float:
    value = mapping.get(key)
    if isinstance(value, bool):
        raise ValueError(f"{key} must be numeric")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be numeric") from exc


def _required_bool(mapping: Mapping[str, Any], key: str) -> bool:
    return _coerce_bool(mapping.get(key), key)


def _coerce_bool(value: Any, key: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean")
    return value
