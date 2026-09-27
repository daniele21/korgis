"""Public local image-generation API."""
from __future__ import annotations

import base64
import time
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request, status
from pydantic import BaseModel, Field

from .core import ImageGenerationOptions, InferenceRequest, TaskType
from .core.contracts import InferenceError
from .request_pipeline import public_error_detail
from .task_policy import enforce_request_capabilities


class ImageGenerationRequest(BaseModel):
    prompt: str = Field(..., min_length=1)
    model: str | None = None
    n: int = Field(default=1, ge=1, le=1)
    size: str | None = None
    response_format: Literal["b64_json"] = "b64_json"
    output_format: Literal["png", "jpeg", "jpg", "webp"] | None = None
    seed: int | None = None
    num_inference_steps: int | None = Field(default=None, ge=1)
    guidance_scale: float | None = None


def _parse_size(value: str | None, runtime_cfg: dict[str, Any]) -> tuple[int, int]:
    if value is None:
        return (
            int(runtime_cfg.get("image_width") or 1024),
            int(runtime_cfg.get("image_height") or 1024),
        )
    parts = value.lower().split("x", 1)
    if len(parts) != 2:
        raise ValueError("size must use WIDTHxHEIGHT format")
    try:
        width, height = (int(part) for part in parts)
    except ValueError as exc:
        raise ValueError("size must use integer WIDTHxHEIGHT values") from exc
    if width <= 0 or height <= 0:
        raise ValueError("image width and height must be > 0")
    return width, height


def install_image_generation_api(application: FastAPI) -> FastAPI:
    """Install the image-generation route exactly once."""
    if getattr(application.state, "image_generation_api_installed", False):
        return application
    application.state.image_generation_api_installed = True

    @application.post("/v1/images/generations", tags=["Images"])
    def generate_image(payload: ImageGenerationRequest, request: Request):
        manager = getattr(request.app.state, "runtime_manager", None)
        if manager is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Runtime manager is unavailable.",
            )

        try:
            runtime = manager.resolve(payload.model)
        except LookupError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "model_not_resident",
                    "message": str(exc),
                    "retryable": False,
                    "details": {"model": payload.model},
                },
            ) from exc

        try:
            width, height = _parse_size(payload.size, runtime.cfg)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "invalid_request",
                    "message": str(exc),
                    "retryable": False,
                    "details": {},
                },
            ) from exc

        options = ImageGenerationOptions(
            width=width,
            height=height,
            num_inference_steps=payload.num_inference_steps,
            guidance_scale=payload.guidance_scale,
            seed=payload.seed,
            output_format=payload.output_format,
        )
        canonical = InferenceRequest(
            task=TaskType.IMAGE_GENERATION,
            model=runtime.key,
            input_text=payload.prompt.strip(),
            image_generation=options,
        )
        try:
            enforce_request_capabilities(
                canonical,
                runtime_config=runtime.cfg,
            )
        except InferenceError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=public_error_detail(exc),
            ) from exc

        generate = getattr(runtime.engine, "generate_image", None)
        if not callable(generate):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code": "backend_error",
                    "message": "Selected runtime does not expose image generation.",
                    "retryable": False,
                    "details": {},
                },
            )

        backend_payload = {
            "prompt": payload.prompt.strip(),
            "width": width,
            "height": height,
            "seed": payload.seed,
            "num_inference_steps": payload.num_inference_steps,
            "guidance_scale": payload.guidance_scale,
            "output_format": payload.output_format,
        }
        backend_payload = {
            key: value for key, value in backend_payload.items() if value is not None
        }

        started = time.perf_counter()
        try:
            with manager.lease_runtime(runtime):
                result = generate(backend_payload)
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code": "backend_error",
                    "message": str(exc),
                    "retryable": False,
                    "details": {},
                },
            ) from exc
        latency_ms = (time.perf_counter() - started) * 1000.0

        encoded = base64.b64encode(result.data).decode("ascii")
        return {
            "created": int(time.time()),
            "model": runtime.model_id,
            "data": [{"b64_json": encoded}],
            "korgis": {
                "runtime_key": runtime.key,
                "backend": getattr(
                    runtime.engine,
                    "backend",
                    runtime.cfg.get("backend", "unknown"),
                ),
                "mime_type": result.mime_type,
                "width": result.width,
                "height": result.height,
                "seed": result.seed,
                "latency_ms": latency_ms,
                "generation": result.metadata,
            },
        }

    return application
