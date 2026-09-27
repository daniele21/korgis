"""Public local image-generation API."""
from __future__ import annotations

import base64
import time
from fastapi import FastAPI, HTTPException, Request, status
from .core.contracts import InferenceError
from .image_generation_request import (
    ImageGenerationRequest,
    prepare_image_generation_request,
)
from .request_pipeline import public_error_detail
from .task_policy import enforce_request_capabilities


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
            prepared = prepare_image_generation_request(
                payload,
                runtime_key=runtime.key,
                runtime_config=runtime.cfg,
            )
            enforce_request_capabilities(
                prepared.canonical,
                runtime_config=runtime.cfg,
            )
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

        started = time.perf_counter()
        try:
            with manager.lease_runtime(runtime):
                result = generate(dict(prepared.backend_payload))
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
