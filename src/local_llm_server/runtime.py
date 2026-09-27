"""Lifecycle, routing, and concurrency ownership for resident model engines."""
from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .resource_manager import AdmissionDecision, AdmissionResult, ResourceManager
from .runtime_admission import admission_metadata, estimated_runtime_load_bytes


def _close_engine(engine: Any) -> None:
    close = getattr(engine, "close", None) or getattr(engine, "shutdown", None)
    if close is not None:
        close()


def _loopback_port_is_available(port: int) -> bool:
    """Return whether one concrete loopback TCP port can currently be bound."""
    if port < 1 or port > 65535:
        return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


_BACKEND_CONFIG_CAPABILITIES: dict[str, tuple[str, ...]] = {
    "llama_cpp": (
        "ctx_size", "n_gpu_layers", "n_threads", "n_batch", "n_ubatch",
        "timeout", "offload_kqv", "flash_attn", "use_mmap",
    ),
    "llama_server": ("ctx_size", "timeout", "max_concurrent_requests"),
    "mlx": ("max_kv_size",),
    "mlx_vlm_server": ("timeout", "max_concurrent_requests", "max_kv_size"),
    "mflux_image": (
        "timeout",
        "max_concurrent_requests",
        "image_width",
        "image_height",
        "image_num_inference_steps",
        "image_max_inference_steps",
        "image_max_pixels",
        "image_guidance_scale",
        "image_output_format",
    ),
    "diffusers_image": (
        "timeout",
        "max_concurrent_requests",
        "image_device",
        "image_dtype",
        "image_width",
        "image_height",
        "image_num_inference_steps",
        "image_max_inference_steps",
        "image_max_pixels",
        "image_guidance_scale",
        "image_output_format",
    ),
}


def config_capabilities_for_backend(
    backend: str, *, thinking_mode: str = "none"
) -> list[str]:
    """Return settings consumed by both the backend and the selected model."""
    capabilities = list(_BACKEND_CONFIG_CAPABILITIES.get(backend, ()))
    if thinking_mode == "switchable":
        capabilities.extend(("enable_thinking", "show_thinking"))
    elif thinking_mode == "always":
        capabilities.append("show_thinking")
    return capabilities


def new_runtime_status(model_id: str) -> dict[str, Any]:
    return {
        "active": False,
        "active_requests": 0,
        "phase": "idle",
        "tokens_generated": 0,
        "output_chunks": 0,
        "output_characters": 0,
        "max_tokens": 0,
        "started_at": 0.0,
        "last_token_at": 0.0,
        "tokens_per_second": 0.0,
        "model": model_id,
        "last_content": "",
    }


class RuntimeState(str, Enum):
    STARTING = "starting"
    READY = "ready"
    DRAINING = "draining"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


class ResourceAdmissionError(RuntimeError):
    """Raised before/after load when configured resource admission rejects it."""

    def __init__(self, result: AdmissionResult) -> None:
        self.result = result
        super().__init__(result.reason)


@dataclass
class ModelRuntime:
    key: str
    cfg: dict[str, Any]
    engine: Any
    admission: threading.Semaphore = field(init=False, repr=False)
    status_lock: threading.Lock = field(default_factory=threading.Lock)
    status: dict[str, Any] = field(default_factory=dict)
    loaded_at: float = field(default_factory=time.time)
    state: RuntimeState = RuntimeState.READY
    active_requests: int = 0
    resource_reservation_id: str | None = None

    def __post_init__(self) -> None:
        concurrency = max(1, int(self.cfg.get("max_concurrent_requests") or 1))
        self.admission = threading.Semaphore(concurrency)
        if not self.status:
            self.status = new_runtime_status(str(self.cfg["model_id"]))

    @property
    def model_id(self) -> str:
        return str(self.cfg["model_id"])

    def snapshot_status(self) -> dict[str, Any]:
        with self.status_lock:
            result = dict(self.status)
        if result["active"] and result["phase"] == "generating" and result["output_chunks"] > 0:
            elapsed = time.perf_counter() - result["started_at"]
            if elapsed > 0:
                result["chunks_per_second"] = result["output_chunks"] / elapsed
        result["key"] = self.key
        result["backend"] = getattr(self.engine, "backend", self.cfg.get("backend", "unknown"))
        result["loaded_at"] = self.loaded_at
        result["state"] = self.state.value
        result["active_requests"] = self.active_requests
        result["max_concurrent_requests"] = int(
            self.cfg.get("max_concurrent_requests") or 1
        )
        resource_admission = self.cfg.get("resource_admission")
        result["resource_admission"] = (
            dict(resource_admission) if isinstance(resource_admission, dict) else None
        )
        return result

    def mark_started(self, max_tokens: int) -> None:
        with self.status_lock:
            if not self.status["active"]:
                self.status.update({
                    "phase": "prompt_eval",
                    "tokens_generated": 0,
                    "output_chunks": 0,
                    "output_characters": 0,
                    "max_tokens": max_tokens,
                    "started_at": time.perf_counter(),
                    "last_content": "",
                })
            self.status["active"] = True
            self.status["active_requests"] = self.active_requests

    def mark_generating(self) -> None:
        with self.status_lock:
            self.status["phase"] = "generating"
            self.status["started_at"] = time.perf_counter()

    def record_output(self, content: str, *, full_content: str | None = None) -> None:
        with self.status_lock:
            self.status["output_chunks"] += 1
            self.status["output_characters"] += len(content)
            # Deprecated compatibility field; use output_chunks for new clients.
            self.status["tokens_generated"] = self.status["output_chunks"]
            self.status["last_token_at"] = time.perf_counter()
            previous = self.status["last_content"] + content
            self.status["last_content"] = (full_content or previous)[-100:]

    def mark_idle(self) -> None:
        with self.status_lock:
            remaining = max(0, self.active_requests - 1)
            self.status["active_requests"] = remaining
            if remaining == 0:
                self.status["active"] = False
                self.status["phase"] = "idle"


@dataclass
class _PendingCleanup:
    engine: Any
    resource_reservation_id: str | None
    reason: str


class ModelRuntimeManager:
    """Own all loaded engines and route model keys/IDs to their runtime.

    Logical residency and resource accounting are deliberately fail-conservative:
    an engine is not declared stopped and its reservation is not released until
    backend teardown succeeds. Engines allocated on a failed load/reload are
    retained internally when cleanup itself fails so shutdown can retry them.
    """

    _PORT_FIELDS = {
        "llama_server": "llama_server_port",
        "mlx_vlm_server": "mlx_vlm_server_port",
    }
    _DEFAULT_PRIVATE_PORTS = {
        "llama_server": 8091,
        "mlx_vlm_server": 8092,
    }

    def __init__(
        self,
        default_model: str | None = None,
        *,
        resource_manager: ResourceManager | None = None,
    ) -> None:
        self._runtimes: dict[str, ModelRuntime] = {}
        self._aliases: dict[str, str] = {}
        self._loading: set[str] = set()
        self._reserved_ports: set[int] = set()
        self._pending_cleanup: list[_PendingCleanup] = []
        self._manager_lock = threading.RLock()
        self._condition = threading.Condition(self._manager_lock)
        self._resource_manager = resource_manager
        self._reservation_counter = 0
        self.default_model = default_model

    @property
    def resource_manager(self) -> ResourceManager | None:
        return self._resource_manager

    @property
    def pending_cleanup_count(self) -> int:
        with self._manager_lock:
            return len(self._pending_cleanup)

    def add(self, cfg: dict[str, Any], engine: Any, *, key: str | None = None) -> ModelRuntime:
        runtime_key = str(key or cfg["model"])
        model_id = str(cfg["model_id"])
        with self._manager_lock:
            for alias in {runtime_key, model_id}:
                existing_alias = self._aliases.get(alias)
                if existing_alias is not None:
                    raise ValueError(
                        f"Alias '{alias}' is already used by loaded model '{existing_alias}'."
                    )
            if runtime_key in self._runtimes:
                raise ValueError(
                    f"Model '{runtime_key}' is already loaded."
                )
            runtime = ModelRuntime(runtime_key, cfg, engine)
            self._runtimes[runtime_key] = runtime
            self._aliases[runtime_key] = runtime_key
            self._aliases[runtime.model_id] = runtime_key
            if self.default_model is None:
                self.default_model = runtime_key
            return runtime

    def load(self, model: str, **explicit: Any) -> tuple[ModelRuntime, bool]:
        from .config import build_config
        from .engine import load_llm

        with self._manager_lock:
            existing = self._resolve_unlocked(model)
            if existing is not None:
                if existing.state is not RuntimeState.READY:
                    raise RuntimeError(f"Model '{existing.key}' is not ready.")
                return existing, False
            if model in self._loading:
                raise RuntimeError(f"Model '{model}' is already loading.")
            cfg = build_config(model=model, **explicit)
            self._assign_private_port(cfg)
            field_name = self._PORT_FIELDS.get(str(cfg.get("backend")))
            reserved_port = int(cfg[field_name]) if field_name else None
            if reserved_port is not None:
                self._reserved_ports.add(reserved_port)
            self._loading.add(model)

        reservation_id: str | None = None
        engine = None
        try:
            reservation_id = self._reserve_runtime_load(model, cfg)
            engine = load_llm(cfg)
            self._commit_runtime_load(reservation_id, cfg)
            runtime = self.add(cfg, engine, key=model)
            runtime.resource_reservation_id = reservation_id
            return runtime, True
        except Exception as exc:
            if engine is not None:
                cleanup_error = self._cleanup_unpublished_engine(
                    engine,
                    reservation_id,
                    reason=f"failed load for {model}",
                )
                if cleanup_error is not None:
                    raise RuntimeError(
                        f"Model '{model}' load failed and backend cleanup also failed; "
                        "resource accounting was retained."
                    ) from cleanup_error
            elif reservation_id is not None:
                self._rollback_runtime_load(reservation_id)
            raise exc
        finally:
            with self._manager_lock:
                self._loading.discard(model)
                if reserved_port is not None:
                    self._reserved_ports.discard(reserved_port)

    def resolve(self, model: str | None = None) -> ModelRuntime:
        target = model or self.default_model
        if not target:
            raise LookupError("No default model is configured.")
        with self._manager_lock:
            runtime = self._resolve_unlocked(str(target))
            if runtime is None:
                raise LookupError(f"Model '{target}' is not loaded.")
            return runtime

    @contextmanager
    def lease_runtime(self, runtime: ModelRuntime) -> Iterator[ModelRuntime]:
        """Keep a resolved runtime alive for the full duration of one request."""
        with self._condition:
            current = self._runtimes.get(runtime.key)
            if current is not runtime or runtime.state is not RuntimeState.READY:
                raise LookupError(f"Model '{runtime.key}' is no longer available.")
            runtime.active_requests += 1

        try:
            with runtime.admission:
                yield runtime
        finally:
            with self._condition:
                runtime.active_requests -= 1
                self._condition.notify_all()

    def _resolve_unlocked(self, model: str) -> ModelRuntime | None:
        key = self._aliases.get(model, model)
        return self._runtimes.get(key)

    def set_default(self, model: str) -> ModelRuntime:
        runtime = self.resolve(model)
        with self._manager_lock:
            if runtime.state is not RuntimeState.READY:
                raise RuntimeError(f"Model '{runtime.key}' is not ready.")
            self.default_model = runtime.key
        return runtime

    def reload(self, model: str, **explicit: Any) -> ModelRuntime:
        """Replace one idle runtime without publishing an unowned replacement."""
        from .config import build_config
        from .engine import load_llm

        current = self.resolve(model)
        with self._manager_lock:
            if self._runtimes.get(current.key) is not current:
                raise LookupError(f"Model '{model}' is no longer loaded.")
            if current.state is not RuntimeState.READY:
                raise RuntimeError(f"Model '{current.key}' is not ready.")
            if current.active_requests:
                raise RuntimeError(f"Model '{current.key}' has an active request.")
            current.state = RuntimeState.DRAINING

        reservation_id: str | None = None
        reserved_port: int | None = None
        new_engine = None
        try:
            cfg = build_config(model=current.key, **explicit)
            with self._manager_lock:
                self._assign_private_port(cfg)
                field_name = self._PORT_FIELDS.get(str(cfg.get("backend")))
                reserved_port = int(cfg[field_name]) if field_name else None
                if reserved_port is not None:
                    self._reserved_ports.add(reserved_port)
            reservation_id = self._reserve_runtime_load(current.key, cfg)
            new_engine = load_llm(cfg)
            with self._manager_lock:
                if reserved_port is not None:
                    self._reserved_ports.discard(reserved_port)
                    reserved_port = None
            self._commit_runtime_load(reservation_id, cfg)
            replacement = ModelRuntime(
                current.key,
                cfg,
                new_engine,
                resource_reservation_id=reservation_id,
            )
            with self._manager_lock:
                if self._runtimes.get(current.key) is not current:
                    raise RuntimeError(f"Model '{current.key}' changed while reloading.")
                for alias in {replacement.key, replacement.model_id}:
                    owner = self._aliases.get(alias)
                    if owner is not None and owner != current.key:
                        raise ValueError(
                            f"Alias '{alias}' is already used by loaded model '{owner}'."
                        )
                current.state = RuntimeState.STOPPING
        except Exception as exc:
            if new_engine is not None:
                cleanup_error = self._cleanup_unpublished_engine(
                    new_engine,
                    reservation_id,
                    reason=f"failed replacement for {current.key}",
                )
                if cleanup_error is not None:
                    with self._manager_lock:
                        if self._runtimes.get(current.key) is current:
                            current.state = RuntimeState.READY
                    raise RuntimeError(
                        f"Reload for '{current.key}' failed and replacement cleanup also failed; "
                        "replacement accounting was retained."
                    ) from cleanup_error
            elif reservation_id is not None:
                self._rollback_runtime_load(reservation_id)
            with self._manager_lock:
                if self._runtimes.get(current.key) is current:
                    current.state = RuntimeState.READY
            raise exc
        finally:
            if reserved_port is not None:
                with self._manager_lock:
                    self._reserved_ports.discard(reserved_port)

        try:
            _close_engine(current.engine)
        except Exception:
            with self._manager_lock:
                current.state = RuntimeState.FAILED
            cleanup_error = self._cleanup_unpublished_engine(
                replacement.engine,
                replacement.resource_reservation_id,
                reason=f"replacement abandoned after teardown failure for {current.key}",
            )
            if cleanup_error is not None:
                raise RuntimeError(
                    f"Reload for '{current.key}' could not stop the current runtime and "
                    "could not clean up its replacement; both remain accounted."
                ) from cleanup_error
            raise

        self._release_runtime_load(current.resource_reservation_id)
        with self._manager_lock:
            if self._runtimes.get(current.key) is not current:
                current.state = RuntimeState.STOPPED
                cleanup_error = self._cleanup_unpublished_engine(
                    replacement.engine,
                    replacement.resource_reservation_id,
                    reason=f"replacement lost ownership race for {current.key}",
                )
                if cleanup_error is not None:
                    raise RuntimeError(
                        f"Runtime '{current.key}' changed during final reload publication and "
                        "replacement cleanup failed."
                    ) from cleanup_error
                raise RuntimeError(f"Model '{current.key}' changed while reloading.")

            self._runtimes[current.key] = replacement
            for alias, key in list(self._aliases.items()):
                if key == current.key:
                    self._aliases.pop(alias, None)
            self._aliases[current.key] = current.key
            self._aliases[replacement.model_id] = current.key
            if self.default_model == current.key:
                self.default_model = replacement.key
        current.state = RuntimeState.STOPPED
        return replacement

    def list(self) -> list[ModelRuntime]:
        with self._manager_lock:
            return list(self._runtimes.values())

    def unload(self, model: str) -> ModelRuntime:
        return self._unload(model, allow_last=False)

    def _unload(self, model: str, *, allow_last: bool) -> ModelRuntime:
        """Stop one idle runtime before removing routing/accounting ownership."""
        runtime = self.resolve(model)
        with self._manager_lock:
            if self._runtimes.get(runtime.key) is not runtime:
                raise LookupError(f"Model '{model}' is no longer loaded.")
            if runtime.state not in {RuntimeState.READY, RuntimeState.FAILED}:
                raise RuntimeError(f"Model '{runtime.key}' is not ready.")
            if not allow_last and len(self._runtimes) == 1:
                raise RuntimeError("Cannot unload the last resident model.")
            if runtime.active_requests:
                raise RuntimeError(f"Model '{runtime.key}' has an active request.")
            runtime.state = RuntimeState.DRAINING
            runtime.state = RuntimeState.STOPPING

        try:
            _close_engine(runtime.engine)
        except Exception:
            with self._manager_lock:
                runtime.state = RuntimeState.FAILED
            raise

        self._release_runtime_load(runtime.resource_reservation_id)
        with self._manager_lock:
            if self._runtimes.get(runtime.key) is runtime:
                self._runtimes.pop(runtime.key, None)
                for alias, key in list(self._aliases.items()):
                    if key == runtime.key:
                        self._aliases.pop(alias, None)
                if self.default_model == runtime.key:
                    self.default_model = self._next_ready_runtime_key_unlocked()
            runtime.state = RuntimeState.STOPPED
        return runtime

    def shutdown(self, *, timeout_seconds: float = 30.0) -> None:
        """Bound drain time, then stop every idle owned engine fail-conservatively.

        Runtimes that fail to drain or fail backend teardown remain tracked and
        accounted with state ``FAILED``. This is intentionally truthful: process
        exit may later reclaim them, but the manager does not fabricate STOPPED.
        """
        timeout = float(timeout_seconds)
        if timeout < 0:
            raise ValueError("timeout_seconds must be >= 0")

        deadline = time.monotonic() + timeout
        with self._condition:
            runtimes = list(self._runtimes.values())
            for runtime in runtimes:
                if runtime.state is RuntimeState.READY:
                    runtime.state = RuntimeState.DRAINING
            self.default_model = None
            while any(runtime.active_requests for runtime in runtimes):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(timeout=remaining)

        failures: list[str] = []
        for runtime in runtimes:
            with self._manager_lock:
                if self._runtimes.get(runtime.key) is not runtime:
                    continue
                if runtime.active_requests:
                    runtime.state = RuntimeState.FAILED
                    failures.append(
                        f"{runtime.key}: {runtime.active_requests} active request(s) did not drain"
                    )
                    continue
                runtime.state = RuntimeState.STOPPING
            try:
                _close_engine(runtime.engine)
            except Exception as exc:
                with self._manager_lock:
                    runtime.state = RuntimeState.FAILED
                failures.append(f"{runtime.key}: backend teardown failed: {exc}")
                continue

            self._release_runtime_load(runtime.resource_reservation_id)
            with self._manager_lock:
                if self._runtimes.get(runtime.key) is runtime:
                    self._runtimes.pop(runtime.key, None)
                    for alias, key in list(self._aliases.items()):
                        if key == runtime.key:
                            self._aliases.pop(alias, None)
                runtime.state = RuntimeState.STOPPED

        failures.extend(self._retry_pending_cleanup())
        if failures:
            raise RuntimeError("Runtime shutdown incomplete: " + "; ".join(failures))

    def _cleanup_unpublished_engine(
        self,
        engine: Any,
        reservation_id: str | None,
        *,
        reason: str,
    ) -> Exception | None:
        """Close an allocated engine before releasing its provisional accounting."""
        try:
            _close_engine(engine)
        except Exception as exc:
            with self._manager_lock:
                self._pending_cleanup.append(
                    _PendingCleanup(engine, reservation_id, reason)
                )
            return exc
        self._rollback_runtime_load(reservation_id)
        return None

    def _retry_pending_cleanup(self) -> list[str]:
        with self._manager_lock:
            pending = list(self._pending_cleanup)
            self._pending_cleanup.clear()

        failures: list[str] = []
        for item in pending:
            try:
                _close_engine(item.engine)
            except Exception as exc:
                failures.append(f"pending cleanup ({item.reason}): {exc}")
                with self._manager_lock:
                    self._pending_cleanup.append(item)
                continue
            self._release_runtime_load(item.resource_reservation_id)
        return failures

    def _next_ready_runtime_key_unlocked(self) -> str | None:
        return next(
            (
                runtime.key
                for runtime in self._runtimes.values()
                if runtime.state is RuntimeState.READY
            ),
            None,
        )

    def _reserve_runtime_load(self, runtime_key: str, cfg: dict[str, Any]) -> str | None:
        estimate = estimated_runtime_load_bytes(cfg)
        manager = self._resource_manager
        if manager is None or estimate is None:
            cfg["resource_admission"] = admission_metadata(None, estimate_bytes=estimate)
            return None

        reservation_id = self._next_reservation_id(runtime_key)
        result = manager.reserve(reservation_id, estimate)
        cfg["resource_admission"] = admission_metadata(result, estimate_bytes=estimate)
        if result.decision is AdmissionDecision.REJECT:
            raise ResourceAdmissionError(result)
        if result.decision is AdmissionDecision.UNKNOWN:
            # ResourceManager intentionally does not create a reservation when
            # there is no enforceable configured budget.
            return None
        return reservation_id

    def _commit_runtime_load(self, reservation_id: str | None, cfg: dict[str, Any]) -> None:
        manager = self._resource_manager
        if reservation_id is None or manager is None:
            return
        result = manager.commit(reservation_id)
        cfg["resource_admission"] = admission_metadata(
            result,
            estimate_bytes=estimated_runtime_load_bytes(cfg),
        )
        if result.decision is AdmissionDecision.REJECT:
            # The caller owns the live backend and must close it before the
            # reservation can truthfully be released.
            raise ResourceAdmissionError(result)

    def _rollback_runtime_load(self, reservation_id: str | None) -> None:
        if reservation_id is not None and self._resource_manager is not None:
            self._resource_manager.rollback(reservation_id)

    def _release_runtime_load(self, reservation_id: str | None) -> None:
        if reservation_id is not None and self._resource_manager is not None:
            self._resource_manager.release(reservation_id)

    def _next_reservation_id(self, runtime_key: str) -> str:
        with self._manager_lock:
            self._reservation_counter += 1
            return f"runtime:{runtime_key}:{self._reservation_counter}"

    def _assign_private_port(self, cfg: dict[str, Any]) -> None:
        backend = str(cfg.get("backend"))
        field_name = self._PORT_FIELDS.get(backend)
        if not field_name:
            return
        used = set(self._reserved_ports)
        for runtime in self._runtimes.values():
            for private_port_field in self._PORT_FIELDS.values():
                if runtime.cfg.get(private_port_field) is not None:
                    used.add(int(runtime.cfg[private_port_field]))
        default_port = self._DEFAULT_PRIVATE_PORTS[backend]
        port = int(cfg.get(field_name) or default_port)
        if port < 1 or port > 65535:
            raise ValueError(f"{field_name} must be between 1 and 65535")
        while port <= 65535:
            if port not in used and _loopback_port_is_available(port):
                cfg[field_name] = port
                return
            port += 1
        raise RuntimeError(
            f"No available private loopback port remains for backend '{backend}'."
        )
