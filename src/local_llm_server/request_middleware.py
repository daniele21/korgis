"""FastAPI request-policy middleware for the public inference entrypoints.

The middleware canonicalizes and validates chat requests before the historical
route creates backend kwargs. It is deliberately installed by product entry
points rather than duplicating policy inside each backend.
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from .core import ErrorCode, InferenceError
from .image_generation_request import (
    ImageGenerationRequest,
    prepare_image_generation_request,
)
from .request_pipeline import prepare_chat_request, public_error_detail
from .task_policy import enforce_request_capabilities

_CHAT_PATHS = frozenset({"/v1/chat/completions", "/api/v1/chat"})
_IMAGE_PATHS = frozenset({"/v1/images/generations"})
_INFERENCE_PATHS = _CHAT_PATHS | _IMAGE_PATHS


def install_request_policy(application: FastAPI) -> FastAPI:
    """Install canonical pre-backend request validation exactly once."""
    if getattr(application.state, "canonical_request_policy_installed", False):
        return application
    application.state.canonical_request_policy_installed = True

    @application.middleware("http")
    async def canonical_request_policy(request: Request, call_next):
        if request.method.upper() != "POST" or request.url.path not in _INFERENCE_PATHS:
            return await call_next(request)

        try:
            payload: Any = await request.json()
        except Exception:
            # Preserve FastAPI/Pydantic's existing malformed-body behavior.
            return await call_next(request)
        if not isinstance(payload, dict):
            return await call_next(request)

        manager = getattr(request.app.state, "runtime_manager", None)
        if manager is None:
            return await call_next(request)
        try:
            runtime = manager.resolve(payload.get("model"))
        except LookupError:
            # Preserve the route's existing model-not-resident response.
            return await call_next(request)

        try:
            if request.url.path in _IMAGE_PATHS:
                try:
                    image_payload = ImageGenerationRequest.model_validate(payload)
                except ValidationError:
                    # Preserve FastAPI/Pydantic's public 422 behavior for malformed
                    # image bodies. Invalid requests never reach admission anyway.
                    return await call_next(request)
                prepared = prepare_image_generation_request(
                    image_payload,
                    runtime_key=runtime.key,
                    runtime_config=runtime.cfg,
                )
            else:
                prepared = prepare_chat_request(
                    payload,
                    runtime_config=runtime.cfg,
                )
            descriptor = enforce_request_capabilities(
                prepared.canonical,
                runtime_config=runtime.cfg,
            )
        except ValueError as exc:
            return JSONResponse(
                status_code=400,
                content={
                    "detail": {
                        "code": ErrorCode.INVALID_REQUEST.value,
                        "message": str(exc),
                        "retryable": False,
                        "details": {},
                    }
                },
            )
        except InferenceError as exc:
            return JSONResponse(
                status_code=400,
                content={"detail": public_error_detail(exc)},
            )

        # The current route still builds backend kwargs for compatibility, but
        # downstream integration can consume this canonical object without
        # translating the HTTP body a second time.
        request.state.prepared_inference_request = prepared
        request.state.runtime_capabilities = descriptor
        return await call_next(request)

    return application
