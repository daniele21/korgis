"""Execution identity contracts for reproducible local inference evidence.

A runtime fingerprint composes artifact identity, backend implementation,
resolved non-sensitive configuration and hardware profile. It never includes
prompts, outputs, private paths, URLs or mutable runtime counters.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
from dataclasses import dataclass, field
from importlib import metadata
from typing import Any, Mapping


_ALLOWED_CONFIG_KEYS = frozenset({
    "backend",
    "ctx_size",
    "max_kv_size",
    "n_gpu_layers",
    "n_threads",
    "n_batch",
    "n_ubatch",
    "offload_kqv",
    "flash_attn",
    "use_mmap",
    "max_concurrent_requests",
    "llama_server_cont_batching",
    "llama_server_kv_unified",
    "llama_server_gpu_layers",
    "llama_server_load_mode",
    "llama_server_fit",
    "llama_server_fit_target_mib",
    "llama_server_fit_ctx",
    "llama_server_cache_type_k",
    "llama_server_cache_type_v",
    "llama_server_cache_ram_mib",
    "thinking_mode",
    "enable_thinking",
    "default_temperature",
    "default_top_p",
    "default_top_k",
    "default_min_p",
    "default_repeat_penalty",
    "image_device",
    "image_dtype",
    "image_width",
    "image_height",
    "image_num_inference_steps",
    "image_quantization_bits",
    "image_max_inference_steps",
    "image_max_pixels",
    "image_guidance_scale",
    "image_output_format",
    "image_sampling_method",
    "image_scheduler",
    "image_diffusion_flash_attention",
    "image_offload_to_cpu",
})


@dataclass(frozen=True, slots=True)
class BackendIdentity:
    name: str
    version: str | None = None
    implementation: str | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("backend name must be non-empty")

    def stable_payload(self) -> dict[str, object]:
        return {
            "name": self.name,
            "version": self.version,
            "implementation": self.implementation,
        }


@dataclass(frozen=True, slots=True)
class HardwareProfile:
    system: str
    machine: str
    processor: str | None
    logical_cpus: int | None
    total_memory_bytes: int | None = None
    accelerator: str | None = None
    extra: Mapping[str, str | int | float | bool] = field(default_factory=dict)

    def stable_payload(self) -> dict[str, object]:
        return {
            "system": self.system,
            "machine": self.machine,
            "processor": self.processor,
            "logical_cpus": self.logical_cpus,
            "total_memory_bytes": self.total_memory_bytes,
            "accelerator": self.accelerator,
            "extra": dict(sorted(self.extra.items())),
        }

    def stable_key(self) -> str:
        return _digest_payload(self.stable_payload())


@dataclass(frozen=True, slots=True)
class RuntimeFingerprint:
    artifact_key: str
    backend: BackendIdentity
    config_digest: str
    hardware: HardwareProfile
    schema_version: int = 1

    def __post_init__(self) -> None:
        for name, digest in (("artifact_key", self.artifact_key), ("config_digest", self.config_digest)):
            if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest.lower()):
                raise ValueError(f"{name} must be a SHA-256 hex digest")

    def stable_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "artifact_key": self.artifact_key.lower(),
            "backend": self.backend.stable_payload(),
            "config_digest": self.config_digest.lower(),
            "hardware_key": self.hardware.stable_key(),
        }

    def stable_key(self) -> str:
        return _digest_payload(self.stable_payload())


def resolved_config_payload(config: Mapping[str, Any]) -> dict[str, object]:
    """Return the path/secret-free effective runtime config used for identity.

    The allowlist is deliberately shared with ``resolved_config_digest`` so a
    public identity document can show exactly which safe settings are covered
    by the digest without exposing model paths, URLs, credentials or content.
    """
    return {
        key: _canonical_scalar(config[key])
        for key in sorted(_ALLOWED_CONFIG_KEYS)
        if key in config and config[key] is not None
    }


def resolved_config_digest(config: Mapping[str, Any]) -> str:
    """Digest an allowlisted effective config without paths, URLs or secrets."""
    return _digest_payload(resolved_config_payload(config))


def backend_identity(
    name: str,
    *,
    package_name: str | None = None,
    implementation: str | None = None,
) -> BackendIdentity:
    """Resolve a package version once when explicitly requested by the caller."""
    version: str | None = None
    if package_name:
        try:
            version = metadata.version(package_name)
        except metadata.PackageNotFoundError:
            version = None
    return BackendIdentity(name=name, version=version, implementation=implementation)


def local_hardware_profile(
    *,
    total_memory_bytes: int | None = None,
    accelerator: str | None = None,
    extra: Mapping[str, str | int | float | bool] | None = None,
) -> HardwareProfile:
    """Build a hostname-free hardware profile from stable local characteristics."""
    return HardwareProfile(
        system=platform.system().lower() or "unknown",
        machine=platform.machine().lower() or "unknown",
        processor=(platform.processor() or None),
        logical_cpus=os.cpu_count(),
        total_memory_bytes=total_memory_bytes,
        accelerator=accelerator,
        extra=extra or {},
    )


def build_runtime_fingerprint(
    *,
    artifact_key: str,
    backend: BackendIdentity,
    resolved_config: Mapping[str, Any],
    hardware: HardwareProfile,
) -> RuntimeFingerprint:
    return RuntimeFingerprint(
        artifact_key=artifact_key,
        backend=backend,
        config_digest=resolved_config_digest(resolved_config),
        hardware=hardware,
    )


def _canonical_scalar(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_canonical_scalar(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _canonical_scalar(item) for key, item in sorted(value.items(), key=lambda item: str(item[0]))}
    return str(value)


def _digest_payload(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()