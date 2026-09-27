"""
config.py — resolve final inference configuration.

Priority (highest → lowest):
  1. Explicit kwargs / CLI flags
  2. Environment variables
  3. Registry entry params
  4. Registry defaults section
  5. Hardcoded fallbacks
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .model_sources import resolve_registry_model
from .registry import load_registry

_FALLBACKS: dict[str, Any] = {
    "host": "127.0.0.1",
    "port": 1235,
    "ctx_size": 4096,
    "n_gpu_layers": 0,
    "n_threads": 8,
    "n_batch": 512,
    "n_ubatch": 512,
    "offload_kqv": True,
    "flash_attn": True,
    "use_mmap": True,
    "chat_format": None,
    "timeout": 1200,
    "force_json": False,
    "enable_thinking": False,
    "show_thinking": False,
    "verbose": False,
    "no_download": False,
    "default_temperature": 0.0,
    "default_top_p": 0.8,
    "default_top_k": 20,
    "default_min_p": 0.0,
    "default_repeat_penalty": 1.0,
    "backend": "llama_cpp",
    "mmproj_path": None,
    "llama_server_port": 8091,
    "llama_server_bin": None,
    "llama_server_allow_unvalidated": False,
    "llama_server_cont_batching": True,
    "llama_server_kv_unified": True,
    "llama_server_gpu_layers": "auto",
    "llama_server_load_mode": "auto",
    "llama_server_fit": True,
    "llama_server_fit_target_mib": None,
    "llama_server_fit_ctx": None,
    "llama_server_cache_type_k": None,
    "llama_server_cache_type_v": None,
    "llama_server_cache_ram_mib": None,
    "mlx_vlm_server_port": 8092,
    "image_device": "auto",
    "image_dtype": "bfloat16",
    "image_width": 1024,
    "image_height": 1024,
    "image_num_inference_steps": 40,
    "image_max_inference_steps": 100,
    "image_max_pixels": 4194304,
    "image_guidance_scale": None,
    "image_output_format": "png",
    "multimodal": False,
    "modalities": ["text"],
    "startup_timeout": 60,
    "max_concurrent_requests": 1,
    "max_kv_size": None,
    "thinking_mode": "none",
    "trust_remote_code": False,
    "allow_remote_media": False,
}

_ENV_MAP: dict[str, str] = {
    "host": "LOCAL_LLM_HOST",
    "port": "LOCAL_LLM_PORT",
    "ctx_size": "LOCAL_LLM_CTX_SIZE",
    "n_gpu_layers": "LOCAL_LLM_N_GPU_LAYERS",
    "n_threads": "LOCAL_LLM_N_THREADS",
    "n_batch": "LOCAL_LLM_N_BATCH",
    "n_ubatch": "LOCAL_LLM_N_UBATCH",
    "chat_format": "LOCAL_LLM_CHAT_FORMAT",
    "timeout": "LOCAL_LLM_TIMEOUT",
    "force_json": "LOCAL_LLM_FORCE_JSON",
    "enable_thinking": "LOCAL_LLM_ENABLE_THINKING",
    "show_thinking": "LOCAL_LLM_SHOW_THINKING",
    "verbose": "LOCAL_LLM_VERBOSE",
    "backend": "LOCAL_LLM_BACKEND",
    "llama_server_port": "LOCAL_LLM_SERVER_PORT",
    "llama_server_bin": "LOCAL_LLM_SERVER_BIN",
    "llama_server_allow_unvalidated": "LOCAL_LLM_SERVER_ALLOW_UNVALIDATED",
    "llama_server_cont_batching": "LOCAL_LLM_SERVER_CONT_BATCHING",
    "llama_server_kv_unified": "LOCAL_LLM_SERVER_KV_UNIFIED",
    "llama_server_gpu_layers": "LOCAL_LLM_SERVER_GPU_LAYERS",
    "llama_server_load_mode": "LOCAL_LLM_SERVER_LOAD_MODE",
    "llama_server_fit": "LOCAL_LLM_SERVER_FIT",
    "llama_server_fit_target_mib": "LOCAL_LLM_SERVER_FIT_TARGET_MIB",
    "llama_server_fit_ctx": "LOCAL_LLM_SERVER_FIT_CTX",
    "llama_server_cache_type_k": "LOCAL_LLM_SERVER_CACHE_TYPE_K",
    "llama_server_cache_type_v": "LOCAL_LLM_SERVER_CACHE_TYPE_V",
    "llama_server_cache_ram_mib": "LOCAL_LLM_SERVER_CACHE_RAM_MIB",
    "mlx_vlm_server_port": "LOCAL_LLM_MLX_VLM_SERVER_PORT",
    "image_device": "LOCAL_LLM_IMAGE_DEVICE",
    "image_dtype": "LOCAL_LLM_IMAGE_DTYPE",
    "image_width": "LOCAL_LLM_IMAGE_WIDTH",
    "image_height": "LOCAL_LLM_IMAGE_HEIGHT",
    "image_num_inference_steps": "LOCAL_LLM_IMAGE_NUM_INFERENCE_STEPS",
    "image_max_inference_steps": "LOCAL_LLM_IMAGE_MAX_INFERENCE_STEPS",
    "image_max_pixels": "LOCAL_LLM_IMAGE_MAX_PIXELS",
    "image_guidance_scale": "LOCAL_LLM_IMAGE_GUIDANCE_SCALE",
    "image_output_format": "LOCAL_LLM_IMAGE_OUTPUT_FORMAT",
    "startup_timeout": "LOCAL_LLM_STARTUP_TIMEOUT",
    "max_concurrent_requests": "LOCAL_LLM_MAX_CONCURRENT_REQUESTS",
    "max_kv_size": "LOCAL_LLM_MAX_KV_SIZE",
    "default_temperature": "LOCAL_LLM_DEFAULT_TEMPERATURE",
    "default_top_p": "LOCAL_LLM_DEFAULT_TOP_P",
    "default_top_k": "LOCAL_LLM_DEFAULT_TOP_K",
    "default_min_p": "LOCAL_LLM_DEFAULT_MIN_P",
    "default_repeat_penalty": "LOCAL_LLM_DEFAULT_REPEAT_PENALTY",
    "trust_remote_code": "LOCAL_LLM_TRUST_REMOTE_CODE",
    "allow_remote_media": "LOCAL_LLM_ALLOW_REMOTE_MEDIA",
}

_BOOL_ENV = {
    "force_json", "enable_thinking", "show_thinking", "verbose", "offload_kqv",
    "flash_attn", "use_mmap", "multimodal", "trust_remote_code", "allow_remote_media",
    "llama_server_allow_unvalidated", "llama_server_cont_batching",
    "llama_server_kv_unified", "llama_server_fit",
}
_INT_ENV = {
    "port", "ctx_size", "n_gpu_layers", "n_threads", "n_batch", "n_ubatch", "timeout",
    "llama_server_port", "mlx_vlm_server_port", "startup_timeout", "default_top_k",
    "image_width", "image_height", "image_num_inference_steps",
    "image_max_inference_steps", "image_max_pixels",
    "max_concurrent_requests", "max_kv_size", "llama_server_fit_target_mib",
    "llama_server_fit_ctx", "llama_server_cache_ram_mib",
}
_FLOAT_ENV = {
    "default_temperature", "default_top_p", "default_min_p",
    "default_repeat_penalty", "image_guidance_scale",
}
_RESOURCE_MEMORY_INT_ENV: dict[str, str] = {
    "resource_estimate_bytes": "LOCAL_LLM_RESOURCE_ESTIMATE_BYTES",
    "resource_model_weights_bytes": "LOCAL_LLM_RESOURCE_MODEL_WEIGHTS_BYTES",
    "resource_backend_overhead_bytes": "LOCAL_LLM_RESOURCE_BACKEND_OVERHEAD_BYTES",
    "resource_context_cache_bytes": "LOCAL_LLM_RESOURCE_CONTEXT_CACHE_BYTES",
    "resource_prompt_cache_bytes": "LOCAL_LLM_RESOURCE_PROMPT_CACHE_BYTES",
    "resource_projector_bytes": "LOCAL_LLM_RESOURCE_PROJECTOR_BYTES",
    "resource_safety_margin_bytes": "LOCAL_LLM_RESOURCE_SAFETY_MARGIN_BYTES",
    "resource_request_estimate_bytes": "LOCAL_LLM_RESOURCE_REQUEST_ESTIMATE_BYTES",
    "resource_request_base_bytes": "LOCAL_LLM_RESOURCE_REQUEST_BASE_BYTES",
    "resource_request_output_token_bytes": "LOCAL_LLM_RESOURCE_REQUEST_OUTPUT_TOKEN_BYTES",
    "resource_request_input_byte_multiplier": "LOCAL_LLM_RESOURCE_REQUEST_INPUT_BYTE_MULTIPLIER",
    "resource_request_safety_margin_bytes": "LOCAL_LLM_RESOURCE_REQUEST_SAFETY_MARGIN_BYTES",
}


def build_config(
    model: str | None = None,
    model_path: str | None = None,
    **explicit: Any,
) -> dict[str, Any]:
    """Return a fully resolved config dict ready for runtime construction."""
    registry = load_registry()
    models_dir: Path = registry["models_dir"]

    if model is None:
        model = registry["default_model"]

    entry: dict[str, Any] = registry["models"].get(model) or {}
    reg_params: dict[str, Any] = {
        **registry.get("defaults", {}),
        **entry.get("params", {}),
    }

    backend = explicit.get("backend") or os.getenv("LOCAL_LLM_BACKEND") or entry.get("backend") or reg_params.get("backend") or "llama_cpp"

    model_id: str = entry.get("model_id", model)
    download_url: str = entry.get("url", "")

    cfg: dict[str, Any] = {}

    for key, fallback in _FALLBACKS.items():
        if key in explicit and explicit[key] is not None:
            cfg[key] = explicit[key]
            continue

        env_name = _ENV_MAP.get(key)
        env_val = os.getenv(env_name, "") if env_name else ""
        if env_val:
            if key in _INT_ENV:
                cfg[key] = int(env_val)
            elif key in _FLOAT_ENV:
                cfg[key] = float(env_val)
            elif key in _BOOL_ENV:
                cfg[key] = env_val.lower() in {"1", "true", "yes", "on"}
            else:
                cfg[key] = env_val
            continue

        if key in reg_params:
            cfg[key] = reg_params[key]
            continue

        # Never share mutable fallback values across independently built runtime
        # configurations. This matters for modalities now that text-only is an
        # explicit conservative default instead of an empty legacy sentinel.
        if isinstance(fallback, list):
            cfg[key] = list(fallback)
        elif isinstance(fallback, dict):
            cfg[key] = dict(fallback)
        else:
            cfg[key] = fallback

    for key, env_name in _RESOURCE_MEMORY_INT_ENV.items():
        cfg[key] = _resolve_non_negative_int_setting(
            key,
            explicit=explicit,
            environment_name=env_name,
            registry_params=reg_params,
        )

    cfg["model"] = model
    cfg["model_id"] = model_id
    cfg["download_url"] = download_url
    cfg["models_dir"] = models_dir
    cfg["backend"] = backend
    cfg["quantization"] = explicit.get("quantization") or entry.get("quantization")
    cfg["mmproj_filename"] = entry.get("mmproj_filename")
    cfg["mmproj_url"] = entry.get("mmproj_url", "")
    cfg["lmstudio_path"] = entry.get("lmstudio_path")
    cfg["size_gb"] = entry.get("size_gb")
    # Immutable identity metadata is optional. Absence means the runtime can
    # still execute, but automatic evidence-grade identity is intentionally not
    # claimed. Explicit kwargs allow controlled direct-path deployments to pin
    # an artifact without adding private paths to public identity.
    cfg["artifact_sha256"] = explicit.get("artifact_sha256") or entry.get("sha256")
    cfg["artifact_revision"] = (
        explicit.get("artifact_revision")
        or entry.get("revision")
        or entry.get("hf_revision")
    )

    cfg["tokenizer_config"] = {
        "trust_remote_code": bool(cfg.get("trust_remote_code", False))
    }

    if "thinking_mode" in entry:
        cfg["thinking_mode"] = str(entry["thinking_mode"])
    elif "enable_thinking" in entry.get("params", {}):
        cfg["thinking_mode"] = "switchable"
    if cfg["thinking_mode"] == "none":
        if explicit.get("enable_thinking") is True:
            raise ValueError(f"Model '{model}' does not support thinking mode.")
        if explicit.get("show_thinking") is True:
            raise ValueError(f"Model '{model}' cannot expose thinking output.")
        cfg["enable_thinking"] = False
        cfg["show_thinking"] = False
    elif cfg["thinking_mode"] == "always":
        if explicit.get("enable_thinking") is False:
            raise ValueError(f"Thinking cannot be disabled for model '{model}'.")
        cfg["enable_thinking"] = True

    resolved_source = resolve_registry_model(
        str(model),
        entry,
        models_dir,
        backend=backend,
        explicit_path=model_path,
    )
    cfg["model_path"] = resolved_source.model_path
    cfg["model_source"] = resolved_source.source_type
    cfg["model_downloaded"] = resolved_source.downloaded

    if not cfg.get("mmproj_path") and entry.get("mmproj_filename"):
        cfg["mmproj_path"] = str(
            resolved_source.mmproj_path
            or (models_dir / str(entry["mmproj_filename"]))
        )

    if "multimodal" in entry and "multimodal" not in explicit:
        cfg["multimodal"] = bool(entry["multimodal"])
    if "modalities" in entry and "modalities" not in explicit:
        cfg["modalities"] = list(entry.get("modalities") or [])
    for capability_key in (
        "tasks", "input_modalities", "output_modalities", "features"
    ):
        if capability_key in entry and capability_key not in explicit:
            cfg[capability_key] = list(entry.get(capability_key) or [])

    return cfg


def _resolve_non_negative_int_setting(
    key: str,
    *,
    explicit: dict[str, Any],
    environment_name: str,
    registry_params: dict[str, Any],
) -> int | None:
    if key in explicit and explicit[key] is not None:
        value = explicit[key]
    else:
        environment_value = os.getenv(environment_name, "")
        if environment_value:
            value = environment_value
        else:
            value = registry_params.get(key)
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError(f"{key} must be a non-negative integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be a non-negative integer") from exc
    if parsed < 0:
        raise ValueError(f"{key} must be a non-negative integer")
    return parsed
