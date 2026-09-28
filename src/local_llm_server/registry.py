"""
registry.py — load and merge the built-in, external, and user model registries.

Resolution order (lowest → highest priority):
  1. Built-in registry  (src/local_llm_server/models_registry.yaml)
  2. Explicit/external registry layers
  3. User registry      (~/.local-llm/models.yaml)

External registry layers are opt-in and can be supplied either through the
``extra_registry_paths`` argument or the ``LOCAL_LLM_REGISTRY_PATHS``
environment variable. Paths are separated with ``os.pathsep`` and may point to
YAML or JSON files using the same top-level schema as the built-in registry.

The result is a dict with keys:
  models_dir: Path
  defaults:   dict
  models:     dict[str, dict]
  default_model: str
  startup_models: list[str]
"""
from __future__ import annotations

import json
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import yaml

from .capability_catalog import validate_registry_capability_entry

_BUILTIN_REGISTRY = Path(__file__).parent / "models_registry.yaml"
_SUPPORTED_BACKENDS = {
    "llama_cpp",
    "gguf",
    "mlx",
    "llama_server",
    "mlx_vlm_server",
    "diffusers_image",
    "mflux_image",
    "stable_diffusion_cpp_image",
}
_VALID_MODALITIES = {"text", "image", "audio"}
_EXTERNAL_REGISTRY_ENV = "LOCAL_LLM_REGISTRY_PATHS"
_SDCPP_REQUIRED_ARTIFACTS = ("diffusion_model", "text_encoder", "vae")


def load_registry(
    *,
    extra_registry_paths: Iterable[str | Path] | None = None,
) -> dict[str, Any]:
    """Return the merged registry as a plain dict.

    External registries are deliberately generic: the core package never reads
    application-specific state or paths. Consumers that need to inject model
    configuration can pass one or more registry files explicitly or set
    ``LOCAL_LLM_REGISTRY_PATHS``.
    """
    builtin = _load_mapping(_BUILTIN_REGISTRY)
    user_registry_path = Path.home() / ".local-llm" / "models.yaml"
    user = _load_mapping(user_registry_path) if user_registry_path.exists() else {}

    external_layers = [
        _load_mapping(path)
        for path in _resolve_external_registry_paths(extra_registry_paths)
    ]

    layers = [builtin, *external_layers, user]

    models: dict[str, Any] = {}
    defaults: dict[str, Any] = {}
    for layer in layers:
        models = _merge_models(models, layer.get("models") or {})
        defaults.update(layer.get("defaults") or {})

    raw_dir = _last_defined(layers, "models_dir") or "~/.local-llm/models"
    models_dir = Path(str(raw_dir)).expanduser().resolve()

    default_model = str(
        _last_defined(layers, "default_model")
        or (next(iter(models)) if models else "")
    )

    startup_models_value = _last_defined(layers, "startup_models")
    startup_models = list(startup_models_value or [])

    registry = {
        "models_dir": models_dir,
        "defaults": defaults,
        "models": models,
        "default_model": default_model,
        "startup_models": startup_models,
    }
    validate_registry(registry)
    return registry


def _resolve_external_registry_paths(
    extra_registry_paths: Iterable[str | Path] | None,
) -> list[Path]:
    paths: list[Path] = []

    env_value = os.getenv(_EXTERNAL_REGISTRY_ENV, "").strip()
    if env_value:
        paths.extend(Path(raw).expanduser() for raw in env_value.split(os.pathsep) if raw.strip())

    if extra_registry_paths:
        paths.extend(Path(raw).expanduser() for raw in extra_registry_paths)

    resolved: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        canonical = path.resolve()
        if canonical in seen:
            continue
        if not canonical.is_file():
            raise FileNotFoundError(f"External registry not found: {canonical}")
        seen.add(canonical)
        resolved.append(canonical)
    return resolved


def _merge_models(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged_models = dict(base)
    for key, entry in override.items():
        if not isinstance(entry, dict):
            merged_models[key] = entry
            continue
        if key not in merged_models or not isinstance(merged_models[key], dict):
            merged_models[key] = dict(entry)
            continue

        merged = dict(merged_models[key])
        merged["params"] = {
            **(merged.get("params") or {}),
            **(entry.get("params") or {}),
        }
        for field_name, value in entry.items():
            if field_name != "params":
                merged[field_name] = value
        merged_models[key] = merged
    return merged_models


def _last_defined(layers: list[dict[str, Any]], key: str) -> Any:
    for layer in reversed(layers):
        if key in layer and layer[key] is not None:
            return layer[key]
    return None


def validate_registry(registry: dict[str, Any]) -> None:
    """Raise a clear error when the merged registry violates runtime invariants."""
    errors: list[str] = []
    models = registry.get("models")
    if not isinstance(models, dict) or not models:
        raise ValueError("Registry validation failed:\n- models must be a non-empty mapping.")

    aliases: dict[str, str] = {}
    for key, entry in models.items():
        label = f"models.{key}"
        if not isinstance(key, str) or not key.strip():
            errors.append("model keys must be non-empty strings")
            continue
        if not isinstance(entry, dict):
            errors.append(f"{label} must be a mapping")
            continue

        model_id = entry.get("model_id", key)
        if not isinstance(model_id, str) or not model_id.strip():
            errors.append(f"{label}.model_id must be a non-empty string")
        for alias in {key, str(model_id)}:
            owner = aliases.get(alias)
            if owner is not None and owner != key:
                errors.append(f"alias '{alias}' is shared by '{owner}' and '{key}'")
            aliases[alias] = key

        backend = str(entry.get("backend") or registry.get("defaults", {}).get("backend") or "llama_cpp")
        if backend not in _SUPPORTED_BACKENDS:
            errors.append(f"{label}.backend '{backend}' is unsupported")

        params = entry.get("params", {})
        if not isinstance(params, dict):
            errors.append(f"{label}.params must be a mapping")
            params = {}
        for field_name in (
            "ctx_size", "max_kv_size", "max_concurrent_requests",
            "llama_server_port", "mlx_vlm_server_port", "sd_server_port", "startup_timeout",
            "image_width", "image_height", "image_num_inference_steps",
            "image_quantization_bits", "image_max_inference_steps", "image_max_pixels",
        ):
            if field_name in params:
                value = params[field_name]
                if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                    errors.append(f"{label}.params.{field_name} must be a positive integer")

        thinking_mode = entry.get("thinking_mode", "none")
        if thinking_mode not in {"none", "switchable", "always"}:
            errors.append(
                f"{label}.thinking_mode must be 'none', 'switchable', or 'always'"
            )

        modalities = entry.get("modalities", ["text"])
        if not isinstance(modalities, list) or not modalities:
            errors.append(f"{label}.modalities must be a non-empty list")
        elif not set(modalities).issubset(_VALID_MODALITIES):
            errors.append(f"{label}.modalities contains an unsupported value")
        elif "text" not in modalities:
            errors.append(f"{label}.modalities must include 'text'")

        multimodal = entry.get("multimodal", False)
        if not isinstance(multimodal, bool):
            errors.append(f"{label}.multimodal must be a boolean")
        elif multimodal != (isinstance(modalities, list) and any(mode != "text" for mode in modalities)):
            errors.append(f"{label}.multimodal must match its declared modalities")

        try:
            validate_registry_capability_entry(entry)
        except ValueError as exc:
            errors.append(f"{label}: {exc}")

        has_model_source = bool(entry.get("path") or entry.get("filename") or entry.get("model_id"))
        if backend in {
            "llama_cpp",
            "gguf",
            "llama_server",
            "mlx",
            "mlx_vlm_server",
            "diffusers_image",
            "mflux_image",
            "stable_diffusion_cpp_image",
        } and not has_model_source:
            errors.append(f"{label} needs path, filename, or model_id")
        if backend == "mlx_vlm_server" and not (entry.get("path") or entry.get("model_id")):
            errors.append(f"{label} with mlx_vlm_server needs path or model_id")
        if backend == "diffusers_image" and not (entry.get("path") or entry.get("model_id")):
            errors.append(f"{label} with diffusers_image needs path or model_id")
        if backend == "mflux_image" and not (entry.get("path") or entry.get("model_id")):
            errors.append(f"{label} with mflux_image needs path or model_id")
        if backend == "stable_diffusion_cpp_image":
            _validate_sdcpp_artifacts(entry, label, errors)
        if backend == "llama_server" and multimodal and not (
            entry.get("mmproj_filename") or params.get("mmproj_path")
        ):
            errors.append(f"{label} multimodal llama_server needs mmproj_filename or params.mmproj_path")

    default_model = registry.get("default_model")
    if default_model not in models:
        errors.append(f"default_model '{default_model}' is not present in models")
    startup_models = registry.get("startup_models", [])
    if not isinstance(startup_models, list) or any(model not in models for model in startup_models):
        errors.append("startup_models must contain only registered model keys")

    if errors:
        formatted = "\n".join(f"- {error}" for error in errors)
        raise ValueError(f"Registry validation failed:\n{formatted}")


def _validate_sdcpp_artifacts(
    entry: dict[str, Any],
    label: str,
    errors: list[str],
) -> None:
    artifacts = entry.get("artifacts")
    if not isinstance(artifacts, dict):
        errors.append(
            f"{label} with stable_diffusion_cpp_image needs an artifacts mapping"
        )
        return
    for name in _SDCPP_REQUIRED_ARTIFACTS:
        spec = artifacts.get(name)
        artifact_label = f"{label}.artifacts.{name}"
        if not isinstance(spec, dict):
            errors.append(f"{artifact_label} must be a mapping")
            continue
        filename = spec.get("filename")
        local_path = spec.get("local_path")
        if not (
            isinstance(filename, str) and filename.strip()
        ) and not (
            isinstance(local_path, str) and local_path.strip()
        ):
            errors.append(f"{artifact_label} needs filename or local_path")
        url = spec.get("url")
        repo = spec.get("repo")
        if not (
            isinstance(url, str) and url.strip()
        ) and not (
            isinstance(repo, str)
            and repo.strip()
            and isinstance(filename, str)
            and filename.strip()
        ):
            errors.append(
                f"{artifact_label} needs url or repo+filename"
            )
        sha256 = spec.get("sha256")
        if sha256 is not None and (
            not isinstance(sha256, str)
            or len(sha256) != 64
            or any(ch not in "0123456789abcdef" for ch in sha256.lower())
        ):
            errors.append(f"{artifact_label}.sha256 must be SHA-256 hex")


def _load_mapping(path: Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as file_handle:
        if path.suffix.lower() == ".json":
            data = json.load(file_handle)
        else:
            data = yaml.safe_load(file_handle)
    return data if isinstance(data, dict) else {}
