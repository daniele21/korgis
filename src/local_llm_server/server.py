"""
server.py — OpenAI-compatible HTTP server built on FastAPI.
Provides automatically generated interactive OpenAPI documentation at /docs.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import threading
import time
import queue
import collections
from collections import OrderedDict
from contextlib import asynccontextmanager
from dataclasses import dataclass
import re
from pathlib import Path
from typing import Any, List, Dict, Optional, Union

from fastapi import APIRouter, FastAPI, Request, HTTPException, status
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .runtime import ModelRuntimeManager
from .resource_telemetry import RequestResourceSampler, request_evidence_payload

logger = logging.getLogger("local-llm.server")


def _get_version() -> str:
    try:
        from importlib.metadata import version as get_version
        return get_version("local-llm-server")
    except Exception:
        try:
            pyproject_path = Path(__file__).resolve().parents[2] / "pyproject.toml"
            if pyproject_path.exists():
                match = re.search(r'version\s*=\s*"([^"]+)"', pyproject_path.read_text(encoding="utf-8"))
                if match:
                    return match.group(1)
        except Exception:
            pass
        return "unknown"




@dataclass
class _CacheEntry:
    value: dict[str, Any]
    stored_at: float


class InferenceResponseCache:
    """Thread-safe, bounded LRU cache for completed inference responses."""

    def __init__(self, max_entries: int = 256, ttl_seconds: float = 600.0) -> None:
        self.max_entries = max(0, int(max_entries))
        self.ttl_seconds = max(0.0, float(ttl_seconds))
        self._entries: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self.max_entries > 0 and self.ttl_seconds > 0

    def get(self, key: str) -> tuple[dict[str, Any] | None, float]:
        if not self.enabled:
            return None, 0.0
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None, 0.0
            age = now - entry.stored_at
            if age >= self.ttl_seconds:
                del self._entries[key]
                return None, age
            self._entries.move_to_end(key)
            return copy.deepcopy(entry.value), age

    def put(self, key: str, value: dict[str, Any]) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._entries[key] = _CacheEntry(copy.deepcopy(value), time.monotonic())
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


def _inference_cache_key(runtime: Any, kwargs: dict[str, Any]) -> str:
    canonical = json.dumps(
        {
            "runtime_key": runtime.key,
            "runtime_loaded_at": runtime.loaded_at,
            "backend": getattr(runtime.engine, "backend", runtime.cfg.get("backend")),
            "request": kwargs,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

# ── Log capturing infrastructure ───────────────────────────────────────────────

class LogStreamBuffer:
    def __init__(self, limit: int = 2000) -> None:
        self.buffer: collections.deque[str] = collections.deque(maxlen=limit)
        self.listeners: list[queue.Queue[str]] = []
        self.lock = threading.Lock()

    def append(self, text: str) -> None:
        if not text:
            return
        with self.lock:
            self.buffer.append(text)
            self.listeners = [listener for listener in self.listeners if not getattr(listener, "closed", False)]
            for listener in self.listeners:
                try:
                    listener.put_nowait(text)
                except queue.Full:
                    pass

    def add_listener(self) -> queue.Queue[str]:
        q: queue.Queue[str] = queue.Queue(maxsize=1000)
        q.closed = False  # type: ignore[attr-defined]
        with self.lock:
            for item in self.buffer:
                q.put_nowait(item)
            self.listeners.append(q)
        return q


class LogBufferHandler(logging.Handler):
    def __init__(self, buffer: LogStreamBuffer) -> None:
        super().__init__()
        self.buffer = buffer

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            self.buffer.append(msg)
        except Exception:
            self.handleError(record)


log_formatter = logging.Formatter("[%(asctime)s] %(levelname)s [%(name)s]: %(message)s", datefmt="%H:%M:%S")


def _new_log_handler(buffer: LogStreamBuffer) -> LogBufferHandler:
    handler = LogBufferHandler(buffer)
    handler.setFormatter(log_formatter)
    return handler


def _install_log_handler(application: FastAPI) -> None:
    handler: LogBufferHandler = application.state.log_handler
    root_logger = logging.getLogger()
    if handler not in root_logger.handlers:
        root_logger.addHandler(handler)


def _remove_log_handler(application: FastAPI) -> None:
    handler = getattr(application.state, "log_handler", None)
    if handler is not None:
        logging.getLogger().removeHandler(handler)


# ── Pydantic Request Models for Swagger ──────────────────────────────────────────

class ChatMessage(BaseModel):
    role: str = Field(..., description="Role of the message author (e.g. system, user, assistant).")
    content: Union[str, List[Dict[str, Any]]] = Field(..., description="The content of the message.")

class ChatCompletionRequest(BaseModel):
    messages: Optional[List[ChatMessage]] = Field(None, description="OpenAI-compatible list of messages.")
    system_prompt: Optional[str] = Field(None, description="Legacy LM Studio system prompt.")
    input: Optional[str] = Field(None, description="Legacy LM Studio input prompt.")
    text: Optional[str] = Field(None, description="Legacy LM Studio input prompt alias.")
    prompt: Optional[str] = Field(None, description="Legacy LM Studio input prompt alias.")
    model: Optional[str] = Field(None, description="ID of the model to use.")
    temperature: Optional[float] = Field(None, description="Sampling temperature.")
    top_p: Optional[float] = Field(None, description="Nucleus sampling threshold.")
    top_k: Optional[int] = Field(None, description="Top-k sampling threshold.")
    min_p: Optional[float] = Field(None, description="Min-p sampling threshold.")
    repeat_penalty: Optional[float] = Field(None, description="Penalty for repeating tokens.")
    presence_penalty: Optional[float] = Field(None, description="OpenAI-compatible presence penalty.")
    frequency_penalty: Optional[float] = Field(None, description="OpenAI-compatible frequency penalty.")
    stream: Optional[bool] = Field(False, description="Enable streaming response.")
    max_tokens: Optional[int] = Field(None, description="Maximum tokens to generate.")
    max_output_tokens: Optional[int] = Field(None, description="Maximum tokens to generate (alias).")
    seed: Optional[int] = Field(None, description="Random seed for deterministic output.")
    stop: Optional[Union[str, List[str]]] = Field(None, description="Stop sequence(s).")
    response_format: Optional[Dict[str, Any]] = Field(None, description="Format specifications (e.g. {'type': 'json_object'}).")
    enable_thinking: Optional[bool] = Field(None, description="Request-level override to enable reasoning/thinking where supported.")
    show_thinking: Optional[bool] = Field(None, description="Request-level override to show or hide <think> blocks in output.")
    enable_reasoning: Optional[bool] = Field(None, description="Alias for enable_thinking.")
    show_reasoning: Optional[bool] = Field(None, description="Alias for show_thinking.")


class ModelActivateRequest(BaseModel):
    model: str = Field(..., description="Registry key of the model to activate.")
    backend: Optional[str] = Field(None, description="Inference backend override (llama_cpp, mlx, llama_server, or mlx_vlm_server).")
    ctx_size: Optional[int] = Field(None, description="Context size override.")
    max_kv_size: Optional[int] = Field(None, ge=1, description="Maximum MLX KV-cache size in tokens.")
    n_gpu_layers: Optional[int] = Field(None, description="Number of GPU layers override.")
    n_threads: Optional[int] = Field(None, description="Number of CPU threads override.")
    n_batch: Optional[int] = Field(None, description="Batch size override.")
    n_ubatch: Optional[int] = Field(None, description="Micro-batch size override.")
    offload_kqv: Optional[bool] = Field(None, description="Offload KQV to GPU.")
    flash_attn: Optional[bool] = Field(None, description="Enable Flash Attention.")
    use_mmap: Optional[bool] = Field(None, description="Use mmap for model loading.")
    timeout: Optional[int] = Field(None, description="Inference timeout in seconds.")
    llama_server_port: Optional[int] = Field(None, description="Internal llama-server subprocess port.")
    llama_server_bin: Optional[str] = Field(None, description="Path to llama-server executable.")
    mlx_vlm_server_port: Optional[int] = Field(None, description="Internal mlx_vlm.server subprocess port.")
    mmproj_path: Optional[str] = Field(None, description="Path to multimodal projector GGUF.")
    startup_timeout: Optional[int] = Field(None, description="Backend subprocess startup timeout in seconds.")
    max_concurrent_requests: Optional[int] = Field(None, ge=1, description="Maximum admitted requests for this runtime.")
    enable_thinking: Optional[bool] = Field(None, description="Enable thinking mode globally.")
    show_thinking: Optional[bool] = Field(None, description="Show thinking globally.")
    verbose: Optional[bool] = Field(None, description="Verbose logging.")


# ── Message normalisation ───────────────────────────────────────────────────────

def _normalize_messages(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Accept both OpenAI-style {messages:[…]} and LM-Studio-style {input:…}."""
    raw_messages = payload.get("messages")

    if isinstance(raw_messages, list):
        messages: list[dict[str, Any]] = []
        for item in raw_messages:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip()
            content = item.get("content")
            if isinstance(content, list):
                has_non_text_part = any(
                    isinstance(part, dict) and part.get("type") not in {None, "text"}
                    for part in content
                )
                if not has_non_text_part:
                    parts = [
                        p if isinstance(p, str) else p.get("text", "")
                        for p in content
                        if isinstance(p, (str, dict))
                    ]
                    content = "\n".join(p for p in parts if p)
            if isinstance(content, str):
                content = content.strip()
            if role and content:
                messages.append({"role": role, "content": content})
        if messages:
            return messages

    system_prompt = str(payload.get("system_prompt") or "").strip()
    user_input = str(
        payload.get("input") or payload.get("text") or payload.get("prompt") or ""
    ).strip()

    if not user_input:
        raise ValueError("Missing required field: 'messages' or 'input'.")

    msgs: list[dict[str, str]] = []
    if system_prompt:
        msgs.append({"role": "system", "content": system_prompt})
    msgs.append({"role": "user", "content": user_input})
    return msgs


def _detect_modalities(messages: list[dict[str, Any]]) -> set[str]:
    required = {"text"}
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            part_type = str(part.get("type") or "")
            if part_type in {"image", "image_url", "input_image"}:
                required.add("image")
            elif part_type in {"audio", "input_audio"}:
                required.add("audio")
    return required


# ── Response helpers ────────────────────────────────────────────────────────────

def _extract_choice_content(response: dict[str, Any]) -> str:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    message = first.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                text = item if isinstance(item, str) else item.get("text") or item.get("content", "")
                if isinstance(text, str):
                    parts.append(text)
            return "\n".join(parts)
    text = first.get("text")
    return text if isinstance(text, str) else ""


def _strip_thinking(content: str) -> tuple[str, str]:
    """Return (thinking, final_answer) from a content string."""
    if "<think>" in content and "</think>" in content:
        before, rest = content.split("<think>", 1)
        thinking, after = rest.split("</think>", 1)
        return thinking.strip(), (before + after).strip()
    elif "</think>" in content:
        thinking, after = content.split("</think>", 1)
        return thinking.strip(), after.strip()
    elif "<think>" in content:
        before, thinking = content.split("<think>", 1)
        return thinking.strip(), before.strip()
    return "", content.strip()


def _usage_stats(response: dict[str, Any], started_at: float, finished_at: float) -> dict[str, Any]:
    usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    total_tokens = int(usage.get("total_tokens") or prompt_tokens + completion_tokens)
    elapsed = max(finished_at - started_at, 1e-6)
    return {
        "input_tokens": prompt_tokens,
        "output_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "tokens_per_second": completion_tokens / elapsed if completion_tokens else 0.0,
        "time_total_seconds": elapsed,
    }


def _build_response(
    raw: dict[str, Any],
    *,
    model_id: str,
    backend: str,
    started_at: float,
    finished_at: float,
    show_thinking: bool,
    korgis_evidence: dict[str, object] | None = None,
) -> dict[str, Any]:
    content = _extract_choice_content(raw)
    thinking, final_answer = _strip_thinking(content)
    exposed = content if show_thinking else final_answer

    payload = dict(raw)
    if "choices" in payload and isinstance(payload["choices"], list) and len(payload["choices"]) > 0:
        import copy
        payload["choices"] = copy.deepcopy(payload["choices"])
        choice = payload["choices"][0]
        if "message" in choice and isinstance(choice["message"], dict):
            choice["message"]["content"] = exposed

    payload["model"] = model_id
    payload["output"] = exposed
    payload["response"] = exposed
    payload["content"] = exposed
    payload["raw_output"] = content
    payload["thinking"] = thinking
    payload["final_answer"] = final_answer
    payload["backend"] = backend
    payload["stats"] = _usage_stats(raw, started_at, finished_at)
    if korgis_evidence is not None:
        payload["korgis"] = dict(korgis_evidence)
    return payload


# ── FastAPI App Creation ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ServerSettings:
    enable_admin_api: bool = False
    cors_origins: tuple[str, ...] = ()
    inference_cache_max_entries: int = 256
    inference_cache_ttl_seconds: float = 600.0


def begin_app_shutdown(application: FastAPI) -> None:
    """Notify long-lived responses before Uvicorn waits for them to drain."""
    application.state.shutdown = True


@asynccontextmanager
async def _app_lifespan(current_app: FastAPI):
    current_app.state.shutdown = False
    try:
        yield
    finally:
        begin_app_shutdown(current_app)
        _remove_log_handler(current_app)

router = APIRouter()
admin_router = APIRouter()


@router.get("/health", tags=["System"])
def get_health(request: Request):
    """Check the health status of the LLM server."""
    manager: ModelRuntimeManager = request.app.state.runtime_manager
    default_runtime = manager.resolve()
    cfg = default_runtime.cfg
    llm = default_runtime.engine
    from local_llm_server.runtime import config_capabilities_for_backend
    admin_enabled = request.app.state.settings.enable_admin_api
    endpoints = [
        "GET /health",
        "GET /v1/models",
        "POST /v1/chat/completions",
        "POST /v1/images/generations",
        "GET /status",
    ]
    if admin_enabled:
        endpoints.extend([
            "GET /api/v1/logs/stream",
            "GET /api/v1/models/registry",
        ])
    return {
        "ok": True,
        "server": "local-llm-server",
        "backend": getattr(llm, "backend", cfg.get("backend", "unknown")),
        "model": cfg["model_id"],
        "model_path": cfg["model_path"],
        "host": cfg.get("host"),
        "port": cfg.get("port"),
        "ctx_size": cfg.get("ctx_size"),
        "max_kv_size": cfg.get("max_kv_size"),
        "n_gpu_layers": cfg.get("n_gpu_layers"),
        "n_threads": cfg.get("n_threads"),
        "n_batch": cfg.get("n_batch"),
        "n_ubatch": cfg.get("n_ubatch"),
        "offload_kqv": cfg.get("offload_kqv"),
        "flash_attn": cfg.get("flash_attn"),
        "use_mmap": cfg.get("use_mmap"),
        "timeout": cfg.get("timeout"),
        "enable_thinking": cfg.get("enable_thinking"),
        "show_thinking": cfg.get("show_thinking"),
        "thinking_mode": cfg.get("thinking_mode", "none"),
        "verbose": cfg.get("verbose"),
        "multimodal": cfg.get("multimodal"),
        "modalities": cfg.get("modalities"),
        "mmproj_path": cfg.get("mmproj_path"),
        "llama_server_port": cfg.get("llama_server_port"),
        "model_key": cfg.get("model"),
        "default_model": manager.default_model,
        "loaded_models": [runtime.key for runtime in manager.list()],
        "admin_api_enabled": admin_enabled,
        "config_capabilities": config_capabilities_for_backend(
            str(cfg.get("backend", "unknown")),
            thinking_mode=str(cfg.get("thinking_mode", "none")),
        ),
        "endpoints": endpoints,
    }


@router.get("/v1/models", tags=["Models"])
@router.get("/api/v1/models", tags=["Models"])
def get_models(request: Request):
    """List loaded models."""
    manager: ModelRuntimeManager = request.app.state.runtime_manager
    return {
        "object": "list",
        "data": [
            {
                "id": runtime.model_id,
                "key": runtime.key,
                "object": "model",
                "created": int(runtime.loaded_at),
                "owned_by": "local",
                "path": runtime.cfg["model_path"],
                "backend": getattr(runtime.engine, "backend", runtime.cfg.get("backend")),
                "default": runtime.key == manager.default_model,
            }
            for runtime in manager.list()
        ],
    }


@router.get("/status", tags=["System"])
@router.get("/api/v1/status", tags=["System"])
def get_status(request: Request):
    """Retrieve real-time inference status and speed statistics."""
    manager: ModelRuntimeManager = request.app.state.runtime_manager
    models = {runtime.key: runtime.snapshot_status() for runtime in manager.list()}
    default_status = dict(models.get(str(manager.default_model), {}))
    return {**default_status, "default_model": manager.default_model, "models": models}


@admin_router.get("/api/v1/models/registry", tags=["Models"])
def get_models_registry(request: Request):
    """List all configured models in local-llm-server registry."""
    try:
        from local_llm_server import list_models
        from local_llm_server.runtime import config_capabilities_for_backend
        models = list_models()
        manager: ModelRuntimeManager = request.app.state.runtime_manager
        loaded = {runtime.key: runtime for runtime in manager.list()}
        for model in models:
            runtime = loaded.get(str(model["key"]))
            model["resident"] = runtime is not None
            model["default"] = runtime is not None and runtime.key == manager.default_model
            model["runtime_status"] = runtime.snapshot_status() if runtime is not None else None
            if runtime is not None:
                model["config_capabilities"] = config_capabilities_for_backend(
                    str(runtime.cfg.get("backend", "")),
                    thinking_mode=str(runtime.cfg.get("thinking_mode", "none")),
                )
                model["runtime_config"] = {
                    key: runtime.cfg.get(key)
                    for key in (
                        "backend", "model_path", "ctx_size", "max_kv_size", "n_gpu_layers", "n_threads",
                        "n_batch", "n_ubatch", "timeout", "offload_kqv", "flash_attn",
                        "use_mmap", "enable_thinking", "show_thinking", "verbose",
                    )
                }
        return {"models": models}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list models: {e}"
        )


@admin_router.get("/api/v1/logs/stream", tags=["System"])
def stream_logs(request: Request):
    """SSE endpoint streaming live console logs of the server process."""
    async def log_generator():
        q = request.app.state.log_buffer.add_listener()
        ping_counter = 0
        try:
            while not getattr(request.app.state, "shutdown", False):
                if await request.is_disconnected():
                    break
                try:
                    msg = q.get_nowait()
                    lines = msg.splitlines()
                    for line in lines:
                        yield f"data: {line}\n\n"
                except queue.Empty:
                    ping_counter += 1
                    if ping_counter >= 25:  # Every 5 seconds (25 * 0.2s)
                        yield ": ping\n\n"
                        ping_counter = 0
                    await asyncio.sleep(0.2)
        except Exception as exc:
            logger.debug("SSE log stream client disconnected: %s", exc)
        finally:
            q.closed = True  # type: ignore[attr-defined]

    return StreamingResponse(log_generator(), media_type="text/event-stream")


@router.get("/example", response_class=HTMLResponse, tags=["Documentation"])
def get_usage_examples(request: Request):
    """Retrieve a interactive HTML page showing how to query the local LLM server."""
    cfg = request.app.state.cfg
    model_id = cfg["model_id"]
    port = cfg["port"]
    host = cfg["host"]
    
    html_content = f"""<!DOCTYPE html>
<html lang="it">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Local LLM Server - API Examples</title>
    <link href="https://fonts.googleapis.com/css2?family=Fira+Code:wght@400;500&family=Inter:wght@300;400;500;600;700&family=Outfit:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        :root {{
            --bg-gradient: linear-gradient(135deg, #0f172a 0%, #020617 100%);
            --primary: #6366f1;
            --primary-glow: rgba(99, 102, 241, 0.15);
            --accent: #10b981;
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --card-bg: rgba(30, 41, 59, 0.4);
            --card-border: rgba(255, 255, 255, 0.06);
            --code-bg: #090d16;
        }}

        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }}

        body {{
            font-family: 'Inter', sans-serif;
            background: var(--bg-gradient);
            color: var(--text-main);
            min-height: 100vh;
            display: flex;
            flex-direction: column;
            align-items: center;
            padding: 2rem 1rem;
            overflow-x: hidden;
        }}

        .container {{
            width: 100%;
            max-width: 900px;
            display: flex;
            flex-direction: column;
            gap: 2rem;
        }}

        header {{
            text-align: center;
            margin-bottom: 1rem;
        }}

        h1 {{
            font-family: 'Outfit', sans-serif;
            font-size: 2.5rem;
            font-weight: 800;
            background: linear-gradient(to right, #a5b4fc, #6366f1);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin-bottom: 0.5rem;
            letter-spacing: -0.025em;
        }}

        .subtitle {{
            color: var(--text-muted);
            font-size: 1.1rem;
        }}

        /* Metadata Card */
        .meta-card {{
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: 16px;
            padding: 1.5rem;
            backdrop-filter: blur(12px);
            display: flex;
            flex-wrap: wrap;
            gap: 1.5rem;
            justify-content: space-between;
            align-items: center;
            box-shadow: 0 4px 30px rgba(0, 0, 0, 0.2);
        }}

        .meta-item {{
            display: flex;
            flex-direction: column;
            gap: 0.25rem;
        }}

        .meta-label {{
            font-size: 0.75rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: var(--text-muted);
            font-weight: 600;
        }}

        .meta-value {{
            font-family: 'Fira Code', monospace;
            font-size: 0.95rem;
            color: #e2e8f0;
            background: rgba(0, 0, 0, 0.25);
            padding: 0.25rem 0.75rem;
            border-radius: 6px;
            border: 1px solid rgba(255, 255, 255, 0.03);
        }}

        .meta-value.accent {{
            color: #818cf8;
            font-weight: 500;
        }}

        /* Navigation Links */
        .nav-links {{
            display: flex;
            gap: 1rem;
        }}

        .btn-nav {{
            background: rgba(255, 255, 255, 0.05);
            color: #f1f5f9;
            text-decoration: none;
            padding: 0.5rem 1rem;
            border-radius: 8px;
            font-size: 0.85rem;
            font-weight: 500;
            border: 1px solid rgba(255, 255, 255, 0.08);
            transition: all 0.2s ease;
        }}

        .btn-nav:hover {{
            background: var(--primary);
            border-color: var(--primary);
            box-shadow: 0 0 15px var(--primary-glow);
            transform: translateY(-1px);
        }}

        /* Tabs Interface */
        .tabs-container {{
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: 20px;
            overflow: hidden;
            backdrop-filter: blur(12px);
            box-shadow: 0 10px 40px rgba(0, 0, 0, 0.3);
        }}

        .tabs-header {{
            display: flex;
            background: rgba(15, 23, 42, 0.6);
            border-bottom: 1px solid var(--card-border);
            overflow-x: auto;
        }}

        .tab-btn {{
            background: none;
            border: none;
            color: var(--text-muted);
            font-family: 'Outfit', sans-serif;
            font-size: 1rem;
            font-weight: 600;
            padding: 1.25rem 1.75rem;
            cursor: pointer;
            transition: all 0.2s ease;
            position: relative;
            white-space: nowrap;
        }}

        .tab-btn:hover {{
            color: var(--text-main);
        }}

        .tab-btn.active {{
            color: var(--primary);
        }}

        .tab-btn.active::after {{
            content: '';
            position: absolute;
            bottom: -1px;
            left: 0;
            width: 100%;
            height: 2px;
            background: var(--primary);
            box-shadow: 0 0 8px var(--primary);
        }}

        .tabs-content {{
            padding: 2rem;
        }}

        .tab-pane {{
            display: none;
            flex-direction: column;
            gap: 1.25rem;
            animation: fadeIn 0.35s ease forwards;
        }}

        .tab-pane.active {{
            display: flex;
        }}

        @keyframes fadeIn {{
            from {{ opacity: 0; transform: translateY(6px); }}
            to {{ opacity: 1; transform: translateY(0); }}
        }}

        .pane-desc {{
            color: var(--text-muted);
            font-size: 0.95rem;
            line-height: 1.5;
        }}

        /* Code Block container */
        .code-container {{
            position: relative;
            background: var(--code-bg);
            border-radius: 12px;
            border: 1px solid rgba(255, 255, 255, 0.05);
            overflow: hidden;
        }}

        pre {{
            padding: 1.25rem;
            overflow-x: auto;
            margin: 0;
        }}

        code {{
            font-family: 'Fira Code', monospace;
            font-size: 0.9rem;
            line-height: 1.6;
            color: #cbd5e1;
        }}

        .btn-copy {{
            position: absolute;
            top: 0.75rem;
            right: 0.75rem;
            background: rgba(255, 255, 255, 0.06);
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 6px;
            color: var(--text-muted);
            cursor: pointer;
            padding: 0.4rem 0.75rem;
            font-size: 0.75rem;
            font-weight: 500;
            display: flex;
            align-items: center;
            gap: 0.35rem;
            transition: all 0.2s ease;
        }}

        .btn-copy:hover {{
            background: rgba(255, 255, 255, 0.12);
            color: var(--text-main);
            border-color: rgba(255, 255, 255, 0.2);
        }}

        .btn-copy.copied {{
            background: rgba(16, 185, 129, 0.15);
            border-color: var(--accent);
            color: var(--accent);
            box-shadow: 0 0 10px rgba(16, 185, 129, 0.2);
        }}

        /* Footer */
        footer {{
            text-align: center;
            margin-top: auto;
            padding-top: 3rem;
            color: rgba(148, 163, 184, 0.4);
            font-size: 0.8rem;
        }}

        /* Responsive */
        @media (max-width: 640px) {{
            .meta-card {{
                flex-direction: column;
                align-items: flex-start;
            }}
            .nav-links {{
                width: 100%;
                justify-content: flex-start;
                margin-top: 0.5rem;
            }}
            .tabs-content {{
                padding: 1.25rem;
            }}
        }}
    </style>
</head>
<body>

<div class="container">
    <header>
        <h1>Local LLM Server</h1>
        <p class="subtitle">Esempi pratici e specifiche per l'interrogazione delle API</p>
    </header>

    <!-- Metadata Card -->
    <div class="meta-card">
        <div class="meta-item">
            <span class="meta-label">Modello Attivo</span>
            <span class="meta-value accent">{model_id}</span>
        </div>
        <div class="meta-item">
            <span class="meta-label">Base URL</span>
            <span class="meta-value">http://{host}:{port}/v1</span>
        </div>
        <div class="nav-links">
            <a href="/" class="btn-nav">Dashboard</a>
            <a href="/docs" class="btn-nav">Swagger Docs</a>
        </div>
    </div>

    <!-- Interactive Tabs -->
    <div class="tabs-container">
        <div class="tabs-header">
            <button class="tab-btn active" onclick="switchTab(event, 'curl-tab')">cURL Request</button>
            <button class="tab-btn" onclick="switchTab(event, 'openai-tab')">OpenAI Python SDK</button>
            <button class="tab-btn" onclick="switchTab(event, 'requests-tab')">Python Requests</button>
            <button class="tab-btn" onclick="switchTab(event, 'batch-tab')">Test Suite & Batch</button>
        </div>

        <div class="tabs-content">
            <!-- cURL Pane -->
            <div id="curl-tab" class="tab-pane active">
                <p class="pane-desc">Il metodo più semplice per interrogare l'endpoint da terminale usando cURL. Compatibile con lo schema standard di OpenAI.</p>
                <div class="code-container">
                    <button class="btn-copy" onclick="copyCode(this)">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>
                        Copy
                    </button>
                    <pre><code id="code-curl">curl http://{host}:{port}/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{{
    "model": "{model_id}",
    "messages": [
      {{"role": "system", "content": "Sei un assistente utile e sintetico."}},
      {{"role": "user", "content": "Ciao!"}}
    ],
    "temperature": 0.3
  }}'</code></pre>
                </div>
            </div>

            <!-- OpenAI Python SDK Pane -->
            <div id="openai-tab" class="tab-pane">
                <p class="pane-desc">Utilizza l'SDK ufficiale di OpenAI in Python. È sufficiente cambiare l'indirizzo `base_url` e passare qualsiasi chiave API non vuota.</p>
                <div class="code-container">
                    <button class="btn-copy" onclick="copyCode(this)">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>
                        Copy
                    </button>
                    <pre><code id="code-openai">from openai import OpenAI

# Inizializza il client indicando il server locale
client = OpenAI(
    base_url="http://{host}:{port}/v1",
    api_key="local"  # Qualsiasi valore non vuoto è accettato
)

response = client.chat.completions.create(
    model="{model_id}",
    messages=[
        {{"role": "system", "content": "Sei un traduttore dall'italiano all'inglese."}},
        {{"role": "user", "content": "La documentazione rende l'integrazione immediata."}}
    ],
    temperature=0.0
)

print(response.choices[0].message.content)</code></pre>
                </div>
            </div>

            <!-- Python Requests Pane -->
            <div id="requests-tab" class="tab-pane">
                <p class="pane-desc">Per fare chiamate senza installare dipendenze esterne oltre a `requests`.</p>
                <div class="code-container">
                    <button class="btn-copy" onclick="copyCode(this)">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>
                        Copy
                    </button>
                    <pre><code id="code-requests">import requests

url = "http://{host}:{port}/v1/chat/completions"
payload = {{
    "model": "{model_id}",
    "messages": [
        {{"role": "system", "content": "Sei un assistente utile e conciso."}},
        {{"role": "user", "content": "Spiega cos'è un LLM in una riga."}}
    ],
    "temperature": 0.5
}}

response = requests.post(url, json=payload)
data = response.json()
print(data["choices"][0]["message"]["content"])</code></pre>
                </div>
            </div>

            <!-- Batch Inference script Pane -->
            <div id="batch-tab" class="tab-pane">
                <p class="pane-desc">Il repository contiene uno script dedicato e modulare per eseguire test di classificazione in batch partendo da liste di parole chiave e configurando risposte strutturate JSON.</p>
                
                <p class="pane-desc"><strong>File dello script:</strong> <code>test_inference.py</code></p>
                
                <div class="code-container">
                    <button class="btn-copy" onclick="copyCode(this)">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>
                        Copy
                    </button>
                    <pre><code id="code-batch"># Esegui il test suite indicando l'indirizzo del server locale
uv run python test_inference.py --server-url http://{host}:{port}/v1</code></pre>
                </div>
            </div>
        </div>
    </div>
</div>

<footer>
    Local LLM Server &mdash; Generazione specifiche dinamiche
</footer>

<script>
    function switchTab(evt, tabId) {{
        // Hide all panes
        const panes = document.querySelectorAll('.tab-pane');
        panes.forEach(pane => pane.classList.remove('active'));

        // Deactivate all buttons
        const buttons = document.querySelectorAll('.tab-btn');
        buttons.forEach(btn => btn.classList.remove('active'));

        // Show target pane and activate button
        document.getElementById(tabId).classList.add('active');
        evt.currentTarget.classList.add('active');
    }}

    async function copyCode(button) {{
        const pre = button.nextElementSibling;
        const codeText = pre.innerText;

        try {{
            await navigator.clipboard.writeText(codeText);
            button.classList.add('copied');
            button.innerHTML = `
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="stroke: var(--accent);"><polyline points="20 6 9 17 4 12"></polyline></svg>
                Copied!
            `;

            setTimeout(() => {{
                button.classList.remove('copied');
                button.innerHTML = `
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>
                    Copy
                `;
            }}, 2000);
        }} catch (err) {{
            console.error('Failed to copy: ', err);
        }}
    }}
</script>
</body>
</html>"""
    return HTMLResponse(content=html_content)


@admin_router.post("/api/v1/models/load", tags=["Models"])
def load_model_compat(request: Request, req: ModelActivateRequest | None = None):
    """Load a model while keeping all currently resident models active."""
    if req is None:
        runtime = request.app.state.runtime_manager.resolve()
        return {"ok": True, "model": runtime.model_id, "key": runtime.key, "already_loaded": True}
    return _load_or_activate_model(req, set_default=False, app_state=request.app.state)


@admin_router.post("/api/v1/models/activate", tags=["Models"])
def activate_model(request: Request, req: ModelActivateRequest):
    """Load a model if necessary and make it the default route."""
    return _load_or_activate_model(req, set_default=True, app_state=request.app.state)


def _load_or_activate_model(
    req: ModelActivateRequest,
    *,
    set_default: bool,
    app_state: Any,
) -> dict[str, Any]:
    manager: ModelRuntimeManager = app_state.runtime_manager
    explicit = {}
    for field_name in [
        "backend", "ctx_size", "max_kv_size", "n_gpu_layers", "n_threads", "n_batch", "n_ubatch",
        "timeout", "offload_kqv", "flash_attn", "use_mmap", "enable_thinking",
        "show_thinking", "verbose", "llama_server_port", "llama_server_bin",
        "mlx_vlm_server_port", "mmproj_path", "startup_timeout",
        "max_concurrent_requests",
    ]:
        value = getattr(req, field_name)
        if value is not None:
            explicit[field_name] = value
    try:
        if set_default and explicit:
            try:
                runtime = manager.reload(req.model, **explicit)
                loaded = True
            except LookupError:
                runtime, loaded = manager.load(req.model, **explicit)
        else:
            runtime, loaded = manager.load(req.model, **explicit)
        if set_default:
            manager.set_default(runtime.key)
            app_state.cfg = runtime.cfg
            app_state.llm = runtime.engine
        logger.info("Model %s ready (newly loaded: %s)", req.model, loaded)
        return {
            "ok": True,
            "model": runtime.model_id,
            "key": runtime.key,
            "loaded": loaded,
            "default": runtime.key == manager.default_model,
            "cfg": {
                "model": runtime.cfg["model"],
                "model_id": runtime.model_id,
                "model_path": runtime.cfg["model_path"],
                "backend": runtime.cfg["backend"],
                "llama_server_port": runtime.cfg.get("llama_server_port"),
                "mlx_vlm_server_port": runtime.cfg.get("mlx_vlm_server_port"),
            },
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to load model %s", req.model)
        raise HTTPException(status_code=500, detail=f"Impossibile caricare il modello '{req.model}': {exc}") from exc


@admin_router.delete("/api/v1/models/{model}", tags=["Models"])
def unload_model(model: str, request: Request):
    """Unload one idle model without affecting other resident models."""
    manager: ModelRuntimeManager = request.app.state.runtime_manager
    try:
        runtime = manager.unload(model)
        if manager.default_model:
            default_runtime = manager.resolve()
            request.app.state.cfg = default_runtime.cfg
            request.app.state.llm = default_runtime.engine
        return {"ok": True, "model": runtime.model_id, "key": runtime.key}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


# Static Files Serving

@router.get("/", response_class=HTMLResponse, tags=["UI"])
def get_ui():
    """Serve the Web UI dashboard."""
    static_file = Path(__file__).parent / "static" / "index.html"
    if static_file.exists():
        return FileResponse(static_file)
    raise HTTPException(status_code=404, detail="Web UI file index.html not found.")


@router.get("/static/{path:path}", tags=["UI"])
def get_static_files(path: str):
    """Serve static assets for the Web UI."""
    static_dir = Path(__file__).parent / "static"
    target_file = (static_dir / path).resolve()
    if static_dir in target_file.parents and target_file.exists():
        return FileResponse(target_file)
    raise HTTPException(status_code=404, detail="File not found")


@router.post("/v1/chat/completions", tags=["Inference"])
@router.post("/api/v1/chat", tags=["Inference"])
def chat_completions(request: Request, req: ChatCompletionRequest):
    """Generate completions for user chat prompts (supports both OpenAI-style and legacy formats)."""
    started_at = time.perf_counter()

    # Convert request object to payload dict for existing logic
    request_payload = req.model_dump(exclude_none=True)
    app_state = request.app.state
    manager: ModelRuntimeManager = app_state.runtime_manager
    try:
        runtime = manager.resolve(request_payload.get("model"))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    cfg = runtime.cfg
    try:
        messages = _normalize_messages(request_payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    required_modalities = _detect_modalities(messages)
    supported_modalities = set(cfg.get("modalities") or ["text"])
    if not required_modalities.issubset(supported_modalities):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "unsupported_modality",
                "required": sorted(required_modalities),
                "supported": sorted(supported_modalities),
                "model": runtime.key,
            },
        )

    max_tokens = request_payload.get("max_tokens") or request_payload.get("max_output_tokens")

    # Resolve reasoning/thinking overrides with fallback to model configuration cfg
    req_enable_thinking = request_payload.get("enable_thinking")
    if req_enable_thinking is None:
        req_enable_thinking = request_payload.get("enable_reasoning")
    thinking_mode = str(cfg.get("thinking_mode", "none"))
    if req_enable_thinking is True and thinking_mode == "none":
        raise HTTPException(
            status_code=400,
            detail={
                "code": "unsupported_thinking_mode",
                "model": runtime.key,
                "thinking_mode": thinking_mode,
            },
        )
    if req_enable_thinking is False and thinking_mode == "always":
        raise HTTPException(
            status_code=400,
            detail={
                "code": "thinking_cannot_be_disabled",
                "model": runtime.key,
                "thinking_mode": thinking_mode,
            },
        )
    enable_thinking = req_enable_thinking if req_enable_thinking is not None else cfg.get("enable_thinking", False)

    req_show_thinking = request_payload.get("show_thinking")
    if req_show_thinking is None:
        req_show_thinking = request_payload.get("show_reasoning")
    show_thinking = req_show_thinking if req_show_thinking is not None else cfg.get("show_thinking", False)

    kwargs: dict[str, Any] = {
        "messages": messages,
        "temperature": float(request_payload.get("temperature", cfg.get("default_temperature", 0.0))),
        "top_p": float(request_payload.get("top_p", cfg.get("default_top_p", 1.0))),
        "top_k": int(request_payload.get("top_k", cfg.get("default_top_k", 40))),
        "min_p": float(request_payload.get("min_p", cfg.get("default_min_p", 0.05))),
        "repeat_penalty": float(request_payload.get("repeat_penalty", cfg.get("default_repeat_penalty", 1.1))),
        "presence_penalty": float(request_payload.get("presence_penalty", 0.0)),
        "frequency_penalty": float(request_payload.get("frequency_penalty", 0.0)),
        "model": runtime.model_id,
    }
    if thinking_mode == "switchable":
        kwargs["enable_thinking"] = bool(enable_thinking)

    if max_tokens is not None:
        kwargs["max_tokens"] = int(max_tokens)
    if request_payload.get("seed") is not None:
        kwargs["seed"] = int(request_payload["seed"])
    if request_payload.get("stop") is not None:
        kwargs["stop"] = request_payload["stop"]

    if isinstance(request_payload.get("response_format"), dict):
        kwargs["response_format"] = request_payload["response_format"]
    elif cfg["force_json"]:
        kwargs["response_format"] = {"type": "json_object"}

    # Handle stream vs non-stream request
    wants_stream = request_payload.get("stream", False)
    def generate_chat_completions_stream():
        with manager.lease_runtime(runtime):
            runtime.mark_started(int(max_tokens) if max_tokens else 0)

            try:
                print(
                    f"[LLM] Request started | model={runtime.key} "
                    f"backend={getattr(runtime.engine, 'backend', 'unknown')} "
                    f"messages={len(messages)} max_tokens={kwargs.get('max_tokens', 'default')}",
                    flush=True,
                )
                logger.info(
                    "LLM inference started | model=%s messages=%d temperature=%s max_tokens=%s",
                    kwargs["model"], len(messages), kwargs["temperature"],
                    kwargs.get("max_tokens", "default"),
                )
                stream = runtime.engine.stream(kwargs)

                is_thinking = False
                is_thinking_for_client = False

                for chunk in stream:
                    if getattr(app_state, "shutdown", False):
                        break
                    if runtime.snapshot_status()["phase"] == "prompt_eval":
                        runtime.mark_generating()
                        print("\033[90mPrompt evaluated. Generating:\033[0m\n", end="", flush=True)

                    choices = chunk.get("choices")
                    if not choices:
                        continue
                    choice = choices[0]
                    if not isinstance(choice, dict):
                        continue
                    delta = choice.get("delta")
                    if not isinstance(delta, dict):
                        delta = {}
                        choice["delta"] = delta
                    token = delta.get("content") or ""
                    if token:
                        if "<think>" in token:
                            is_thinking = True
                            print("\n\033[93m[THINKING]\033[0m ", end="", flush=True)
                        print(f"\033[90m{token}\033[0m" if is_thinking else token, end="", flush=True)
                        if "</think>" in token:
                            is_thinking = False
                            print("\n\033[92m[RESPONSE]\033[0m ", end="", flush=True)

                        runtime.record_output(token)

                    # Handle streaming filtration if show_thinking is False
                    if not show_thinking:
                        client_token = token
                        if is_thinking_for_client:
                            if "</think>" in client_token:
                                parts = client_token.split("</think>", 1)
                                client_token = parts[1]
                                is_thinking_for_client = False
                            else:
                                client_token = ""
                        else:
                            if "<think>" in client_token:
                                is_thinking_for_client = True
                                parts = client_token.split("<think>", 1)
                                before = parts[0]
                                rest = parts[1]
                                if "</think>" in rest:
                                    after = rest.split("</think>", 1)[1]
                                    client_token = before + after
                                    is_thinking_for_client = False
                                else:
                                    client_token = before

                        if client_token:
                            choice["delta"]["content"] = client_token
                        else:
                            continue

                    # Yield chunk to client as SSE
                    yield f"data: {json.dumps(chunk)}\n\n"

                elapsed = time.perf_counter() - started_at
                tokens = runtime.snapshot_status()["output_chunks"]
                speed = tokens / elapsed if elapsed > 0 else 0
                print(f"\n\033[92m[Inference complete] Received {tokens} chunks in {elapsed:.2f}s ({speed:.1f} chunks/s)\033[0m\n", flush=True)
                yield "data: [DONE]\n\n"
            except Exception as e:
                import traceback
                logger.error("Inference stream error:\n%s", traceback.format_exc())
                print(f"[LLM] Request failed | model={runtime.key} error={e}", flush=True)
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
            finally:
                runtime.mark_idle()

    if wants_stream:
        return StreamingResponse(generate_chat_completions_stream(), media_type="text/event-stream")

    # Non-streaming request uses the engine's explicit completion contract.
    # Only greedy requests are cached: sampled responses are intentionally fresh.
    response_cache: InferenceResponseCache = app_state.inference_response_cache
    cache_key: str | None = None
    if response_cache.enabled and kwargs["temperature"] == 0.0:
        cache_key = _inference_cache_key(runtime, kwargs)
        cached_response, cache_age = response_cache.get(cache_key)
        if cached_response is not None:
            logger.info(
                "Inference cache hit | model=%s key=%s age=%.2fs",
                runtime.key,
                cache_key[:12],
                cache_age,
            )
            return _build_response(
                cached_response,
                model_id=cfg["model_id"],
                backend=getattr(runtime.engine, "backend", cfg.get("backend", "unknown")),
                started_at=started_at,
                finished_at=time.perf_counter(),
                show_thinking=show_thinking,
                korgis_evidence=request_evidence_payload(execution_source="cache"),
            )
        logger.debug(
            "Inference cache miss | model=%s key=%s",
            runtime.key,
            cache_key[:12],
        )

    with manager.lease_runtime(runtime):
        runtime.mark_started(int(max_tokens) if max_tokens else 0)
        sampler = RequestResourceSampler().start()
        raw_response: dict[str, Any] | None = None
        failure: Exception | None = None
        failure_trace: str | None = None
        resource_evidence = None

        try:
            raw_response = runtime.engine.complete(kwargs)
        except Exception as exc:
            import traceback
            failure = exc
            failure_trace = traceback.format_exc()
        finally:
            resource_evidence = sampler.stop()
            runtime.latest_request_resource_evidence = resource_evidence
            request.state.resource_snapshot_id = resource_evidence.snapshot_id
            runtime.mark_idle()

        if failure is not None:
            logger.error("Inference error:\n%s", failure_trace)
            raise HTTPException(
                status_code=500,
                detail=f"Inference failed: {failure}",
            ) from failure

        assert raw_response is not None
        if cache_key is not None:
            response_cache.put(cache_key, raw_response)
        return _build_response(
            raw_response,
            model_id=cfg["model_id"],
            backend=getattr(runtime.engine, "backend", cfg.get("backend", "unknown")),
            started_at=started_at,
            finished_at=time.perf_counter(),
            show_thinking=show_thinking,
            korgis_evidence=request_evidence_payload(
                execution_source="inference",
                resources=resource_evidence,
            ),
        )


# ── Public entry point ──────────────────────────────────────────────────────────

def create_app(
    manager: ModelRuntimeManager | None = None,
    *,
    settings: ServerSettings | None = None,
) -> FastAPI:
    """Create an isolated FastAPI application for one runtime manager."""
    resolved_settings = settings or ServerSettings()
    application = FastAPI(
        title="Local LLM Server API",
        description="OpenAI-compatible API serving local LLMs (Llama-cpp, MLX, llama-server multimodal).",
        version=_get_version(),
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=_app_lifespan,
    )
    if resolved_settings.cors_origins:
        origins = list(resolved_settings.cors_origins)
        application.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials="*" not in origins,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    application.include_router(router)
    if resolved_settings.enable_admin_api:
        application.include_router(admin_router)
    application.state.settings = resolved_settings
    application.state.shutdown = False
    application.state.log_buffer = LogStreamBuffer(limit=2000)
    application.state.log_handler = _new_log_handler(application.state.log_buffer)
    application.state.inference_response_cache = InferenceResponseCache(
        max_entries=resolved_settings.inference_cache_max_entries,
        ttl_seconds=resolved_settings.inference_cache_ttl_seconds,
    )
    if manager is not None:
        default_runtime = manager.resolve()
        application.state.runtime_manager = manager
        application.state.cfg = default_runtime.cfg
        application.state.llm = default_runtime.engine
    return application


app = create_app()

def configure_runtime(
    cfg: dict[str, Any],
    llm: Any,
    manager: ModelRuntimeManager | None = None,
    *,
    target_app: FastAPI | None = None,
) -> ModelRuntimeManager:
    """Attach a single- or multi-model runtime manager to the FastAPI app."""
    application = target_app or app
    if manager is None:
        manager = ModelRuntimeManager(default_model=str(cfg["model"]))
        manager.add(cfg, llm)
    default_runtime = manager.resolve()
    application.state.runtime_manager = manager
    application.state.cfg = default_runtime.cfg
    application.state.llm = default_runtime.engine
    application.state.shutdown = False
    application.state.inference_response_cache.clear()
    _install_log_handler(application)
    return manager


def run_server(
    cfg: dict[str, Any],
    llm: Any,
    manager: ModelRuntimeManager | None = None,
    *,
    enable_admin_api: bool = False,
    cors_origins: list[str] | tuple[str, ...] | None = None,
) -> None:
    """Start the FastAPI uvicorn server."""
    import uvicorn
    
    application = create_app(
        settings=ServerSettings(
            enable_admin_api=enable_admin_api,
            cors_origins=tuple(cors_origins or ()),
        )
    )
    manager = configure_runtime(cfg, llm, manager, target_app=application)
    cfg = manager.resolve().cfg

    class CustomUvicornServer(uvicorn.Server):
        def handle_exit(self, sig: int, frame) -> None:
            print("\n[*] Stopping local-llm-server...", flush=True)
            begin_app_shutdown(application)
            super().handle_exit(sig, frame)

    logger.info(
        "local-llm-server starting on http://%s:%d (model: %s)",
        cfg["host"],
        cfg["port"],
        cfg["model_id"],
    )

    print(f"\n[*] local-llm-server listening on http://{cfg['host']}:{cfg['port']}/v1/chat/completions (model: {cfg['model_id']})", flush=True)
    print(f"[*] 👉 Access Web UI at:       http://{cfg['host']}:{cfg['port']}/", flush=True)
    print(f"[*] 👉 API specifications at:  http://{cfg['host']}:{cfg['port']}/docs", flush=True)
    print(f"[*] 👉 API usage examples at:  http://{cfg['host']}:{cfg['port']}/example\n", flush=True)

    try:
        try:
            config = uvicorn.Config(
                application,
                host=cfg["host"],
                port=cfg["port"],
                log_level="warning" if not cfg.get("verbose", False) else "info",
                timeout_graceful_shutdown=10,
            )
            server = CustomUvicornServer(config)
            server.run()
        except KeyboardInterrupt:
            logger.info("Server uvicorn process interrupted by keyboard event.")
    finally:
        begin_app_shutdown(application)
        manager.shutdown()
