"""local_llm_server — public API."""
from __future__ import annotations

from typing import Any

from .audio import audio_to_base64, prepare_audio, prepare_audio_message
from .client import LocalLLMClient
from .vision import image_to_data_url, prepare_image_message

__all__ = [
    "run_server",
    "serve",
    "download_model",
    "list_models",
    "ServerHandle",
    "LocalLLMClient",
    "prepare_audio",
    "audio_to_base64",
    "prepare_audio_message",
    "image_to_data_url",
    "prepare_image_message",
]


def run_server(*args: Any, **kwargs: Any) -> None:
    """Start the policy-enabled HTTP server without import-time side effects."""
    from .policy_server import run_server as _run_server

    _run_server(*args, **kwargs)


def serve(
    model: str | None = None,
    model_path: str | None = None,
    models: list[str] | None = None,
    default_model: str | None = None,
    host: str = "127.0.0.1",
    port: int = 1235,
    background: bool = False,
    no_download: bool = False,
    enable_admin_api: bool = False,
    cors_origins: list[str] | None = None,
    **kwargs,
) -> "ServerHandle | None":
    """Start the supported resource-aware local LLM product server."""
    import threading
    import uvicorn

    from .product_composition import install_product_http_stack
    from .product_runtime import bootstrap_product_runtimes
    from .server import (
        ServerSettings,
        begin_app_shutdown,
        configure_runtime,
        create_app,
    )

    explicit = dict(kwargs)
    explicit.update(
        {
            "host": host,
            "port": port,
            "no_download": no_download,
        }
    )
    bootstrap = bootstrap_product_runtimes(
        model=model,
        model_path=model_path,
        models=models,
        default_model=default_model,
        explicit=explicit,
    )
    manager = bootstrap.manager
    cfg = bootstrap.cfg
    llm = bootstrap.engine

    application = create_app(
        settings=ServerSettings(
            enable_admin_api=enable_admin_api,
            cors_origins=tuple(cors_origins or ()),
        )
    )
    configure_runtime(cfg, llm, manager, target_app=application)
    application.state.resource_policy_settings = bootstrap.resource_policy
    install_product_http_stack(application)

    config = uvicorn.Config(
        application,
        host=cfg["host"],
        port=cfg["port"],
        log_level="warning" if not cfg.get("verbose", False) else "info",
        timeout_graceful_shutdown=10,
    )
    server = uvicorn.Server(config)

    if background:
        t = threading.Thread(target=server.run, daemon=True)
        t.start()
        return ServerHandle(
            server, thread=t, manager=manager, application=application
        )

    import signal as _signal

    def _shutdown(signum, _frame):
        begin_app_shutdown(application)
        server.should_exit = True

    _signal.signal(_signal.SIGINT, _shutdown)
    _signal.signal(_signal.SIGTERM, _shutdown)
    try:
        server.run()
    finally:
        begin_app_shutdown(application)
        manager.shutdown()
    return None


def download_model(model: str) -> None:
    """Download a model from the registry if not already on disk."""
    from .model_sources import (
        resolve_diffusers_runtime_path,
        resolve_mflux_image_runtime_path,
        resolve_mlx_runtime_path,
        resolve_registry_model,
    )
    from .registry import load_registry
    from .downloader import ensure_model

    registry = load_registry()
    models_dir = registry["models_dir"]
    entry = registry["models"].get(model)
    if entry is None:
        raise ValueError(f"Model '{model}' not found in registry. Run 'local-llm models' to list available models.")
    backend = str(entry.get("backend") or "llama_cpp")
    resolved = resolve_registry_model(model, entry, models_dir, backend=backend)
    if resolved.downloaded:
        return
    if backend in {"mlx", "mlx_vlm_server"}:
        resolve_mlx_runtime_path(
            resolved.model_path,
            no_download=False,
            multimodal=bool(entry.get("multimodal", False)),
        )
        return
    if backend == "diffusers_image":
        resolve_diffusers_runtime_path(
            resolved.model_path,
            no_download=False,
        )
        return
    if backend == "mflux_image":
        resolve_mflux_image_runtime_path(
            resolved.model_path,
            no_download=False,
            expected_quantization_bits=(
                int(entry["params"]["image_quantization_bits"])
                if isinstance(entry.get("params"), dict)
                and entry["params"].get("image_quantization_bits") is not None
                else None
            ),
        )
        return
    if entry.get("path"):
        raise FileNotFoundError(f"Local model path not found: {resolved.model_path}")
    ensure_model(
        url=entry.get("url", ""),
        dest=models_dir / str(entry["filename"]),
    )
    if entry.get("mmproj_filename"):
        ensure_model(
            url=entry.get("mmproj_url", ""),
            dest=models_dir / entry["mmproj_filename"],
        )


def list_models() -> list[dict]:
    """Return the merged registry with source and capability metadata."""
    from .capability_catalog import capability_catalog_item
    from .model_sources import resolve_registry_model
    from .registry import load_registry

    registry = load_registry()
    models_dir = registry["models_dir"]
    result = []
    for key, entry in registry["models"].items():
        resolved = resolve_registry_model(
            str(key), entry, models_dir,
            backend=str(entry.get("backend") or "llama_cpp"),
        )
        capability = capability_catalog_item(str(key), entry)
        result.append(
            {
                "key": key,
                "model_id": entry.get("model_id", key),
                "size_gb": entry.get("size_gb"),
                "tags": entry.get("tags", []),
                "backend": entry.get("backend", "llama_cpp"),
                "multimodal": bool(entry.get("multimodal", False)),
                "modalities": entry.get("modalities", []),
                "capabilities": capability["capabilities"],
                "capability_source": capability["capability_source"],
                "generation_parameter_domains": capability["generation_parameter_domains"],
                "downloaded": resolved.downloaded,
                "path": resolved.model_path,
                "source": resolved.source_type,
                "mmproj_path": str(resolved.mmproj_path) if resolved.mmproj_path else None,
            }
        )
    return result


class ServerHandle:
    def __init__(
        self,
        server: Any,
        thread: Any | None = None,
        manager: Any | None = None,
        application: Any | None = None,
    ):
        self._server = server
        self._thread = thread
        self._manager = manager
        self._application = application

    def shutdown(self) -> None:
        if self._application is not None:
            self._application.state.shutdown = True
        self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=10)
        if self._manager is not None:
            self._manager.shutdown()
