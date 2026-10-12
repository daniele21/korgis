"""Policy-enabled HTTP server entrypoint.

This wrapper composes the existing FastAPI application/runtime manager with the
canonical product HTTP stack without editing the historical server monolith.
"""
from __future__ import annotations

from typing import Any


def browser_base_url(host: object, port: object) -> str:
    """Return a browser-usable URL for a configured HTTP bind endpoint."""
    configured_host = str(host)
    browser_host = "127.0.0.1" if configured_host in {"0.0.0.0", "::"} else configured_host
    if ":" in browser_host and not browser_host.startswith("["):
        browser_host = f"[{browser_host}]"
    return f"http://{browser_host}:{int(port)}/"


def _run_until_stopped(server: Any) -> None:
    """Run Uvicorn while treating its re-raised SIGINT as a clean CLI stop."""
    try:
        server.run()
    except KeyboardInterrupt:
        print("\n[*] local-llm-server stopped.", flush=True)


def _shutdown_aware_server(uvicorn: Any, config: Any, application: Any) -> Any:
    """Create a Uvicorn server that notifies long-lived app responses first.

    Uvicorn waits for active ASGI tasks during graceful shutdown. Product SSE
    endpoints also wait for ``application.state.shutdown`` before terminating,
    so that flag must be set from the signal handler before Uvicorn starts its
    drain. Setting it only after ``server.run()`` returns creates a lifecycle
    deadlock that ends in forced task cancellation at the graceful timeout.
    """
    from .server import begin_app_shutdown

    class ShutdownAwareServer(uvicorn.Server):
        async def startup(self, sockets: Any = None) -> None:
            await super().startup(sockets=sockets)
            if self.started:
                base_url = browser_base_url(self.config.host, self.config.port)
                print(f"\n[*] Web UI ready:      {base_url}", flush=True)
                print(f"[*] API documentation: {base_url}docs", flush=True)
                print(f"[*] API examples:      {base_url}example\n", flush=True)

        def handle_exit(self, sig: int, frame: Any) -> None:
            begin_app_shutdown(application)
            super().handle_exit(sig, frame)

    return ShutdownAwareServer(config)


def run_server(
    cfg: dict[str, Any],
    llm: Any,
    manager: Any | None = None,
    *,
    enable_admin_api: bool = False,
    cors_origins: list[str] | tuple[str, ...] | None = None,
    resource_policy_settings: Any | None = None,
    inference_cache_max_entries: int = 256,
) -> None:
    import uvicorn

    from .product_composition import install_product_http_stack
    from .product_runtime import effective_policy_for_manager
    from .server import (
        ServerSettings,
        begin_app_shutdown,
        configure_runtime,
        create_app,
    )

    application = create_app(
        settings=ServerSettings(
            enable_admin_api=enable_admin_api,
            cors_origins=tuple(cors_origins or ()),
            inference_cache_max_entries=inference_cache_max_entries,
        )
    )
    manager = configure_runtime(cfg, llm, manager, target_app=application)
    application.state.resource_policy_settings = (
        resource_policy_settings
        if resource_policy_settings is not None
        else effective_policy_for_manager(manager)
    )
    install_product_http_stack(application)
    resolved_cfg = manager.resolve().cfg

    config = uvicorn.Config(
        application,
        host=resolved_cfg["host"],
        port=resolved_cfg["port"],
        log_level="warning" if not resolved_cfg.get("verbose", False) else "info",
        timeout_graceful_shutdown=10,
    )
    server = _shutdown_aware_server(uvicorn, config, application)

    try:
        _run_until_stopped(server)
    finally:
        # Idempotent fallback for normal programmatic exits or startup failures.
        begin_app_shutdown(application)
        manager.shutdown()
