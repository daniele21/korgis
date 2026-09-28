"""stable-diffusion.cpp-backed local image generation runtime."""
from __future__ import annotations

import base64
import json
import secrets
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any

from .downloader import ensure_model
from .image_generation_common import GeneratedImage
from .model_sources import artifact_download_url
from .process import ManagedProcess
from .stable_diffusion_cpp_compat import (
    build_sd_server_command,
    probe_sd_server_version,
    resolve_sd_server_binary,
)


class StableDiffusionCppImageEngine:
    """Resident sd-server backend for multi-artifact local image generation."""

    backend = "stable_diffusion_cpp_image"
    execution_isolation = "subprocess"

    def __init__(
        self,
        cfg: dict[str, Any],
        *,
        process_factory: Callable[..., Any] | None = None,
        urlopen: Callable[..., Any] | None = None,
        seed_factory: Callable[[], int] | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        self.cfg = cfg
        raw_artifacts = cfg.get("model_artifacts")
        raw_specs = cfg.get("artifact_specs")
        if not isinstance(raw_artifacts, Mapping):
            raise ValueError("stable_diffusion_cpp_image requires model_artifacts")
        if not isinstance(raw_specs, Mapping):
            raise ValueError("stable_diffusion_cpp_image requires artifact_specs")

        self.artifacts = {
            str(name): Path(str(value)).expanduser().resolve()
            for name, value in raw_artifacts.items()
        }
        self._ensure_artifacts(raw_specs)

        self.host = "127.0.0.1"
        self.port = int(cfg.get("sd_server_port") or 8093)
        self.base_url = f"http://{self.host}:{self.port}"
        self.binary = resolve_sd_server_binary(cfg)
        self.backend_version = probe_sd_server_version(self.binary)
        self._urlopen = urlopen or urllib.request.urlopen
        self._seed_factory = seed_factory or (lambda: secrets.randbits(32))
        self._sleep = sleeper or time.sleep
        process_cls = process_factory or ManagedProcess
        command = build_sd_server_command(
            binary=self.binary,
            artifacts=self.artifacts,
            host=self.host,
            port=self.port,
            cfg=cfg,
        )
        self.process = process_cls(
            command,
            name="sd-server",
            logger=__import__("logging").getLogger("local-llm.sd-server"),
        )
        self._start()

    def _ensure_artifacts(self, specs: Mapping[str, Any]) -> None:
        for name in ("diffusion_model", "text_encoder", "vae"):
            path = self.artifacts.get(name)
            spec = specs.get(name)
            if path is None or not isinstance(spec, Mapping):
                raise ValueError(
                    f"stable-diffusion.cpp bundle is missing artifact '{name}'"
                )
            ensure_model(
                url=artifact_download_url(dict(spec)),
                dest=path,
                no_download=bool(self.cfg.get("no_download", False)),
                expected_sha256=(
                    str(spec["sha256"])
                    if spec.get("sha256") is not None
                    else None
                ),
            )

    def _start(self) -> None:
        try:
            self.process.start()
            self.process.wait_ready(
                self._is_ready,
                timeout=float(self.cfg.get("startup_timeout") or 60),
            )
        except Exception:
            self.process.close()
            raise

    def _is_ready(self) -> bool:
        payload = self._json_request(
            "GET",
            "/sdcpp/v1/capabilities",
            timeout=2.0,
        )
        supported = payload.get("supported_modes")
        if not isinstance(supported, list) or "img_gen" not in supported:
            raise RuntimeError(
                "sd-server is reachable but does not expose img_gen capability"
            )
        return True

    def generate_image(self, payload: dict[str, Any]) -> GeneratedImage:
        prompt = str(payload.get("prompt") or "").strip()
        if not prompt:
            raise ValueError("image generation requires a non-empty prompt")

        width = int(payload.get("width") or self.cfg.get("image_width") or 1024)
        height = int(payload.get("height") or self.cfg.get("image_height") or 1024)
        if width <= 0 or height <= 0 or width % 32 or height % 32:
            raise ValueError(
                "Qwen Image 2.1 width and height must be positive multiples of 32"
            )

        steps = int(
            payload.get("num_inference_steps")
            or self.cfg.get("image_num_inference_steps")
            or 20
        )
        if steps <= 0:
            raise ValueError("num_inference_steps must be > 0")

        guidance_raw = payload.get("guidance_scale")
        if guidance_raw is None:
            guidance_raw = self.cfg.get("image_guidance_scale")
        guidance = float(guidance_raw) if guidance_raw is not None else 6.0

        seed_raw = payload.get("seed")
        seed = int(seed_raw) if seed_raw is not None else int(self._seed_factory())
        sample_method = str(
            self.cfg.get("image_sampling_method") or "euler"
        ).strip()
        if not sample_method:
            raise ValueError("image_sampling_method must not be empty")

        output_format = str(
            payload.get("output_format")
            or self.cfg.get("image_output_format")
            or "png"
        ).strip().lower()
        if output_format == "jpg":
            output_format = "jpeg"
        if output_format not in {"png", "jpeg", "webp"}:
            raise ValueError("image output_format must be png, jpeg, jpg, or webp")

        sample_params: dict[str, Any] = {
            "sample_method": sample_method,
            "sample_steps": steps,
            "guidance": {"txt_cfg": guidance},
        }
        scheduler = self.cfg.get("image_scheduler")
        if scheduler:
            sample_params["scheduler"] = str(scheduler)

        submission = self._json_request(
            "POST",
            "/sdcpp/v1/img_gen",
            payload={
                "prompt": prompt,
                "width": width,
                "height": height,
                "seed": seed,
                "batch_count": 1,
                "sample_params": sample_params,
                "output_format": output_format,
            },
        )
        job_id = str(submission.get("id") or "").strip()
        poll_url = str(
            submission.get("poll_url") or f"/sdcpp/v1/jobs/{job_id}"
        ).strip()
        if not job_id:
            raise RuntimeError("sd-server image submission returned no job id")

        job = self._wait_for_job(job_id, poll_url)
        result = job.get("result")
        if not isinstance(result, Mapping):
            raise RuntimeError("sd-server completed image job without a result")
        images = result.get("images")
        if not isinstance(images, list) or not images:
            raise RuntimeError("sd-server completed image job without images")
        first = images[0]
        if not isinstance(first, Mapping):
            raise TypeError("sd-server image result must be an object")
        encoded = str(first.get("b64_json") or "")
        if not encoded:
            raise RuntimeError("sd-server image result contains no b64_json")
        try:
            data = base64.b64decode(encoded, validate=True)
        except ValueError as exc:
            raise RuntimeError("sd-server returned invalid base64 image data") from exc

        effective_format = str(result.get("output_format") or output_format).lower()
        mime_type = {
            "png": "image/png",
            "jpeg": "image/jpeg",
            "jpg": "image/jpeg",
            "webp": "image/webp",
        }.get(effective_format, "application/octet-stream")

        started = job.get("started")
        completed = job.get("completed")
        generation_seconds = None
        if isinstance(started, (int, float)) and isinstance(completed, (int, float)):
            generation_seconds = max(0.0, float(completed) - float(started))

        return GeneratedImage(
            data=data,
            mime_type=mime_type,
            width=width,
            height=height,
            seed=seed,
            metadata={
                "runtime": "stable-diffusion.cpp",
                "backend_version": self.backend_version,
                "quantization": self.cfg.get("quantization"),
                "sampling_method": sample_method,
                "scheduler": scheduler,
                "num_inference_steps": steps,
                "guidance_scale": guidance,
                "generation_time_seconds": generation_seconds,
                "job_id": job_id,
            },
        )

    def _wait_for_job(self, job_id: str, poll_url: str) -> dict[str, Any]:
        timeout = float(self.cfg.get("timeout") or 1200)
        interval = float(
            self.cfg.get("sd_server_poll_interval_seconds") or 0.25
        )
        if interval <= 0:
            raise ValueError("sd_server_poll_interval_seconds must be > 0")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = self._json_request("GET", poll_url, timeout=min(timeout, 30.0))
            status = str(job.get("status") or "").lower()
            if status == "completed":
                return job
            if status in {"failed", "cancelled"}:
                error = job.get("error")
                if isinstance(error, Mapping):
                    message = str(error.get("message") or error.get("code") or status)
                else:
                    message = status
                raise RuntimeError(
                    f"sd-server image job {job_id} {status}: {message}"
                )
            self._sleep(interval)

        try:
            self._json_request(
                "POST",
                f"/sdcpp/v1/jobs/{job_id}/cancel",
                payload={},
                timeout=5.0,
            )
        except Exception:
            pass
        raise TimeoutError(
            f"sd-server image job {job_id} exceeded {timeout:.0f}s timeout"
        )

    def _json_request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        body = (
            json.dumps(payload).encode("utf-8")
            if payload is not None
            else None
        )
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=body,
            headers={"Content-Type": "application/json"} if body is not None else {},
            method=method,
        )
        try:
            with self._urlopen(
                request,
                timeout=timeout or float(self.cfg.get("timeout") or 1200),
            ) as response:
                raw = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"sd-server returned HTTP {exc.code}: {detail or exc.reason}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Cannot reach sd-server: {exc.reason}") from exc
        parsed = json.loads(raw or "{}")
        if not isinstance(parsed, dict):
            raise TypeError("sd-server response must be a JSON object")
        return parsed

    def complete(self, _payload: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError(
            "stable_diffusion_cpp_image does not support chat completion"
        )

    def stream(self, _payload: dict[str, Any]) -> Iterator[dict[str, Any]]:
        raise RuntimeError(
            "stable_diffusion_cpp_image does not support streaming chat"
        )
        yield  # pragma: no cover

    def close(self) -> None:
        self.process.close()

    shutdown = close
