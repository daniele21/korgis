"""
engine.py — load either llama-cpp-python or mlx-lm depending on configuration.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterator, Protocol

from .downloader import ensure_model
from .llama_cpp_request import chat_completion as llama_cpp_chat_completion
from .llama_server_compat import (
    build_llama_server_command,
    resolve_llama_server_binary,
)
from .mlx_generation_evidence import openai_evidence_from_mlx_generation
from .process import ManagedProcess

logger = logging.getLogger("local-llm.engine")


class Engine(Protocol):
    backend: str

    def complete(self, payload: dict[str, Any]) -> dict[str, Any]: ...
    def stream(self, payload: dict[str, Any]) -> Iterator[dict[str, Any]]: ...
    def close(self) -> None: ...


class LlamaCppEngine:
    backend = "llama-cpp-python"
    execution_isolation = "in_process"

    def __init__(self, cfg: dict[str, Any]) -> None:
        model_path = Path(cfg["model_path"]).expanduser()

        ensure_model(
            url=cfg.get("download_url", ""),
            dest=model_path,
            no_download=cfg.get("no_download", False),
        )

        try:
            from llama_cpp import Llama
        except ModuleNotFoundError as exc:
            raise SystemExit(
                "Missing dependency: llama-cpp-python. "
                "Install it with: pip install llama-cpp-python"
            ) from exc

        kwargs: dict[str, Any] = {
            "model_path": str(model_path),
            "n_ctx": cfg["ctx_size"],
            "n_batch": cfg["n_batch"],
            "n_ubatch": cfg["n_ubatch"],
            "n_gpu_layers": cfg["n_gpu_layers"],
            "n_threads": cfg["n_threads"],
            "offload_kqv": cfg["offload_kqv"],
            "flash_attn": cfg["flash_attn"],
            "use_mmap": cfg["use_mmap"],
            "verbose": cfg["verbose"],
        }

        if cfg.get("chat_format"):
            kwargs["chat_format"] = cfg["chat_format"]

        logger.info("Loading GGUF model: %s", model_path)
        logger.info(
            "Model configuration: %s",
            json.dumps(
                {k: v for k, v in kwargs.items() if k != "model_path"}, indent=2
            ),
        )

        self.llm = Llama(**kwargs)

    def complete(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.llm is None:
            raise RuntimeError("llama-cpp-python runtime is closed")
        result = llama_cpp_chat_completion(self.llm, payload, stream=False)
        if not isinstance(result, dict):
            raise RuntimeError("llama_cpp non-stream request returned an invalid iterator")
        return result

    def stream(self, payload: dict[str, Any]) -> Iterator[dict[str, Any]]:
        if self.llm is None:
            raise RuntimeError("llama-cpp-python runtime is closed")
        result = llama_cpp_chat_completion(self.llm, payload, stream=True)
        if isinstance(result, dict):
            raise RuntimeError("llama_cpp stream request returned a completed response")
        return iter(result)

    def close(self) -> None:
        """Explicitly release the native llama.cpp model/context owner.

        llama-cpp-python exposes ``Llama.close()`` in the currently locked
        0.3.x line. Calling it makes the teardown boundary explicit, but this is
        still not evidence that host/unified memory has returned to baseline.
        """
        llm = getattr(self, "llm", None)
        if llm is None:
            return
        llm.close()
        self.llm = None

    shutdown = close


class MLXEngine:
    backend = "mlx-lm"
    execution_isolation = "in_process"

    def __init__(self, cfg: dict[str, Any]) -> None:
        try:
            from mlx_lm import load
        except ModuleNotFoundError as exc:
            raise SystemExit(
                "Missing dependency: mlx-lm. "
                'Install it with: pip install "local-llm-server[mlx]"'
            ) from exc

        from .model_sources import resolve_mlx_runtime_path

        resolved_model = resolve_mlx_runtime_path(
            str(cfg["model_path"]),
            no_download=bool(cfg.get("no_download", False)),
            multimodal=False,
        )
        self.model_ref = str(resolved_model)
        cfg["model_path"] = self.model_ref
        self.cfg = cfg

        tokenizer_config = cfg.get("tokenizer_config") or {"trust_remote_code": True}

        logger.info("Loading MLX model: %s", self.model_ref)
        self.model, self.tokenizer = load(
            self.model_ref,
            tokenizer_config=tokenizer_config,
        )
        logger.info("MLX model loaded.")

    def stream(self, payload: dict[str, Any]) -> Iterator[dict[str, Any]]:
        from mlx_lm import stream_generate
        from mlx_lm.sample_utils import make_logits_processors, make_sampler

        kwargs = dict(payload)
        messages = kwargs["messages"]
        max_tokens = int(kwargs.get("max_tokens") or 512)

        enable_thinking = kwargs.pop("enable_thinking", None)
        template_kwargs = {}
        if enable_thinking is not None:
            template_kwargs["enable_thinking"] = enable_thinking
            template_kwargs["thinking"] = enable_thinking

        prompt = self.tokenizer.apply_chat_template(
            messages,
            add_generation_prompt=True,
            **template_kwargs,
        )

        sampler = make_sampler(
            temp=float(kwargs.get("temperature", 0.0)),
            top_p=float(kwargs.get("top_p", 1.0)),
            min_p=float(kwargs.get("min_p", 0.0)),
            top_k=int(kwargs.get("top_k", 0)),
        )

        logits_processors = make_logits_processors(
            repetition_penalty=kwargs.get("repeat_penalty"),
            presence_penalty=kwargs.get("presence_penalty"),
            frequency_penalty=kwargs.get("frequency_penalty"),
        )

        model_name = str(kwargs.get("model") or self.model_ref)

        for response in stream_generate(
            self.model,
            self.tokenizer,
            prompt=prompt,
            max_tokens=max_tokens,
            sampler=sampler,
            logits_processors=logits_processors,
            max_kv_size=self.cfg.get("max_kv_size"),
        ):
            text = response.text or ""
            usage, timings, finish_reason = openai_evidence_from_mlx_generation(response)

            choices: list[dict[str, Any]] = []
            if text or finish_reason is not None:
                choices.append(
                    {
                        "index": 0,
                        "delta": {"content": text} if text else {},
                        "finish_reason": finish_reason,
                    }
                )

            if not choices and not usage and not timings:
                continue

            chunk: dict[str, Any] = {
                "id": "chatcmpl-local-mlx",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": model_name,
                "choices": choices,
            }
            if usage:
                chunk["usage"] = usage
            if timings:
                chunk["timings"] = timings
            yield chunk

    def complete(self, payload: dict[str, Any]) -> dict[str, Any]:
        chunks = list(self.stream(payload))
        content_parts: list[str] = []
        latest_usage: dict[str, Any] | None = None
        latest_timings: dict[str, Any] | None = None
        finish_reason: str | None = None

        for chunk in chunks:
            choices = chunk.get("choices")
            if isinstance(choices, list) and choices:
                first = choices[0]
                if isinstance(first, dict):
                    delta = first.get("delta")
                    if isinstance(delta, dict):
                        text = delta.get("content")
                        if isinstance(text, str) and text:
                            content_parts.append(text)
                    reason = first.get("finish_reason")
                    if isinstance(reason, str) and reason:
                        finish_reason = reason
            usage = chunk.get("usage")
            if isinstance(usage, dict) and usage:
                latest_usage = dict(usage)
            timings = chunk.get("timings")
            if isinstance(timings, dict) and timings:
                latest_timings = dict(timings)

        result: dict[str, Any] = {
            "id": "chatcmpl-local-mlx",
            "object": "chat.completion",
            "created": 0,
            "model": str(payload.get("model") or self.model_ref),
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": "".join(content_parts)},
                "finish_reason": finish_reason or "stop",
            }],
        }
        if latest_usage:
            result["usage"] = latest_usage
        if latest_timings:
            result["timings"] = latest_timings
        return result

    def close(self) -> None:
        return None


class LlamaServerEngine:
    """Managed external llama.cpp server with attributable runtime identity."""

    backend = "llama_server"
    execution_isolation = "subprocess"

    def __init__(self, cfg: dict[str, Any]) -> None:
        self.cfg = cfg
        self.model_path = Path(cfg["model_path"]).expanduser()
        self.mmproj_path = (
            Path(cfg["mmproj_path"]).expanduser() if cfg.get("mmproj_path") else None
        )
        self.port = int(cfg.get("llama_server_port") or 8091)
        self.host = "127.0.0.1"
        self.base_url = f"http://{self.host}:{self.port}"
        self.process: ManagedProcess | None = None

        ensure_model(
            url=cfg.get("download_url", ""),
            dest=self.model_path,
            no_download=cfg.get("no_download", False),
        )
        if self.mmproj_path:
            ensure_model(
                url=cfg.get("mmproj_url", ""),
                dest=self.mmproj_path,
                no_download=cfg.get("no_download", False),
            )

        self.binary, self.compatibility = resolve_llama_server_binary(cfg)
        self.backend_version = self.compatibility.backend_version
        self.backend_compatibility_profile = self.compatibility.profile
        self._start()

    def _start(self) -> None:
        cmd = build_llama_server_command(
            binary=self.binary,
            model_path=self.model_path,
            mmproj_path=self.mmproj_path,
            host=self.host,
            port=self.port,
            cfg=self.cfg,
            compatibility=self.compatibility,
        )
        logger.info(
            "Starting llama-server (%s, %s): %s",
            self.backend_compatibility_profile,
            self.backend_version or "unattributed",
            " ".join(cmd),
        )
        self.process = ManagedProcess(
            cmd,
            name="llama-server",
            logger=logger,
        )
        try:
            self.process.start()
            self.process.wait_ready(
                self._is_ready,
                timeout=float(self.cfg.get("startup_timeout") or 60),
            )
        except Exception:
            process = self.process
            self.process = None
            if process is not None:
                process.close()
            raise

    def _is_ready(self) -> bool:
        with urllib.request.urlopen(f"{self.base_url}/health", timeout=2) as response:
            body = response.read().decode("utf-8", errors="replace")
            ready = response.status == 200 and (
                not body or '"ok"' in body or '"status"' in body
            )
            if ready:
                logger.info("llama-server ready on %s", self.base_url)
            return ready

    def _request(self, payload: dict[str, Any], *, stream: bool) -> Any:
        kwargs = dict(payload)
        enable_thinking = kwargs.pop("enable_thinking", None)
        if enable_thinking is not None and "chat_template_kwargs" not in kwargs:
            # For modern llama-server templates that support enable_thinking variable
            kwargs["chat_template_kwargs"] = {"enable_thinking": enable_thinking}

        kwargs["stream"] = stream
        payload = json.dumps(kwargs).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/v1/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        if not stream:
            with urllib.request.urlopen(
                request, timeout=float(self.cfg.get("timeout") or 1200)
            ) as response:
                return json.loads(response.read().decode("utf-8"))

        def iter_chunks() -> Iterator[dict[str, Any]]:
            with urllib.request.urlopen(
                request, timeout=float(self.cfg.get("timeout") or 1200)
            ) as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data = line.removeprefix("data:").strip()
                    if data == "[DONE]":
                        break
                    yield json.loads(data)

        return iter_chunks()

    def complete(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request(payload, stream=False)

    def stream(self, payload: dict[str, Any]) -> Iterator[dict[str, Any]]:
        return self._request(payload, stream=True)

    def close(self) -> None:
        process = self.process
        if process is None:
            return
        process.close()
        self.process = None

    shutdown = close


class MLXVLMServerEngine:
    """Engine backed by the OpenAI-compatible ``mlx_vlm.server`` subprocess."""

    backend = "mlx_vlm_server"
    execution_isolation = "subprocess"

    def __init__(self, cfg: dict[str, Any]) -> None:
        from .model_sources import resolve_mlx_runtime_path

        self.cfg = cfg
        resolved_model = resolve_mlx_runtime_path(
            str(cfg["model_path"]),
            no_download=bool(cfg.get("no_download", False)),
            multimodal=True,
        )
        self.model_path = str(resolved_model)
        self.cfg["model_path"] = self.model_path
        self.port = int(cfg.get("mlx_vlm_server_port") or 8092)
        self.host = "127.0.0.1"
        self.base_url = f"http://{self.host}:{self.port}"
        self.process: ManagedProcess | None = None
        self._start()

    def _start(self) -> None:
        cmd = [
            sys.executable,
            "-m",
            "mlx_vlm.server",
            "--model",
            self.model_path,
            "--host",
            self.host,
            "--port",
            str(self.port),
        ]
        if self.cfg.get("max_kv_size") is not None:
            cmd.extend(["--max-kv-size", str(int(self.cfg["max_kv_size"]))])
        if self.cfg.get("thinking_mode") == "always" or (
            self.cfg.get("thinking_mode") == "switchable"
            and self.cfg.get("enable_thinking")
        ):
            cmd.append("--enable-thinking")
        logger.info("Starting mlx_vlm.server: %s", " ".join(cmd))
        self.process = ManagedProcess(
            cmd,
            name="mlx_vlm_server",
            logger=logger,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        self.process.start()
        self.process.wait_ready(
            self._is_ready,
            timeout=float(self.cfg.get("startup_timeout") or 60),
        )

    def _is_ready(self) -> bool:
        with urllib.request.urlopen(f"{self.base_url}/health", timeout=2) as response:
            if response.status != 200:
                return False
            body = json.loads(response.read().decode("utf-8", errors="replace"))
            loaded_model = body.get("loaded_model")
            if loaded_model != self.model_path:
                raise RuntimeError(
                    "mlx_vlm.server is healthy but loaded an unexpected model: "
                    f"expected {self.model_path!r}, got {loaded_model!r}"
                )
            ready = True
            if ready:
                logger.info("mlx_vlm.server ready on %s", self.base_url)
            return ready

    def _request(self, payload: dict[str, Any], *, stream: bool) -> Any:
        kwargs = dict(payload)
        enable_thinking = kwargs.pop("enable_thinking", None)
        kwargs.pop("show_thinking", None)
        if (
            self.cfg.get("thinking_mode") == "switchable"
            and enable_thinking is not None
        ):
            kwargs["enable_thinking"] = bool(enable_thinking)
        if "repeat_penalty" in kwargs:
            kwargs["repetition_penalty"] = kwargs.pop("repeat_penalty")
        # The subprocess is preloaded using ``self.model_path``. Sending the
        # registry/Hugging Face model id here creates a different cache key in
        # mlx_vlm.server and can trigger a second, very expensive model load.
        backend_kwargs = {**kwargs, "model": self.model_path, "stream": stream}
        payload = json.dumps(backend_kwargs).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/v1/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        timeout = float(self.cfg.get("timeout") or 1200)

        def open_backend():
            try:
                return urllib.request.urlopen(request, timeout=timeout)
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                raise RuntimeError(
                    f"mlx_vlm.server returned HTTP {exc.code}: {detail or exc.reason}"
                ) from exc
            except urllib.error.URLError as exc:
                raise RuntimeError(f"Cannot reach mlx_vlm.server: {exc.reason}") from exc

        if not stream:
            with open_backend() as response:
                return json.loads(response.read().decode("utf-8"))

        def iter_chunks() -> Iterator[dict[str, Any]]:
            with open_backend() as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data = line.removeprefix("data:").strip()
                    if data == "[DONE]":
                        break
                    yield json.loads(data)

        return iter_chunks()

    def complete(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request(payload, stream=False)

    def stream(self, payload: dict[str, Any]) -> Iterator[dict[str, Any]]:
        return self._request(payload, stream=True)

    def close(self) -> None:
        if self.process is not None:
            self.process.close()

    shutdown = close


def load_llm(cfg: dict[str, Any]) -> Any:
    backend = cfg.get("backend", "llama_cpp")

    if backend in {"llama_cpp", "gguf"}:
        return LlamaCppEngine(cfg)

    if backend == "mlx":
        return MLXEngine(cfg)

    if backend == "llama_server":
        return LlamaServerEngine(cfg)

    if backend == "mlx_vlm_server":
        return MLXVLMServerEngine(cfg)

    if backend == "diffusers_image":
        from .image_generation_engine import DiffusersImageEngine

        return DiffusersImageEngine(cfg)

    if backend == "mflux_image":
        from .mflux_image_engine import MFluxImageEngine

        return MFluxImageEngine(cfg)

    raise ValueError(f"Unsupported backend: {backend}")