"""Opt-in, bounded image editing through the existing native sd-server.

No remote image URLs, paths, masks or arbitrary native arguments enter this API.
The first supported format is one PNG reference; all bytes stay local and are
discarded at request end. Full imaging support follows measured Mac E2E tests.
"""
from __future__ import annotations

import base64
import hashlib
import struct
import time
import zlib
from dataclasses import replace

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, status
from starlette.concurrency import run_in_threadpool

from .core.contracts import InferenceError
from .image_generation_request import ImageGenerationRequest, prepare_image_generation_request
from .request_pipeline import public_error_detail
from .task_policy import enforce_request_capabilities

MAX_UPLOAD_BYTES = 8 * 1024 * 1024
MAX_SOURCE_PIXELS = 1_048_576  # first release: <=1 megapixel, to bound vision activations
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def validate_png_reference(data: bytes, *, max_pixels: int = MAX_SOURCE_PIXELS) -> tuple[int, int]:
    """Inspect fixed PNG signature/IHDR without expanding untrusted compressed pixels."""
    if len(data) < 33 or len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("PNG reference must be between 33 bytes and 8 MiB")
    if not data.startswith(PNG_SIGNATURE) or data[12:16] != b"IHDR":
        raise ValueError("Only PNG reference images are supported")
    if struct.unpack(">I", data[8:12])[0] != 13:
        raise ValueError("Invalid PNG IHDR")
    crc = struct.unpack(">I", data[29:33])[0]
    if zlib.crc32(data[12:29]) & 0xFFFFFFFF != crc:
        raise ValueError("Corrupt PNG IHDR")
    width, height = struct.unpack(">II", data[16:24])
    if not (32 <= width <= 2048 and 32 <= height <= 2048):
        raise ValueError("Reference dimensions must be between 32 and 2048 pixels")
    if width * height > max_pixels:
        raise ValueError("Reference image exceeds configured pixel budget")
    return width, height


def install_image_editing_api(application: FastAPI) -> FastAPI:
    if getattr(application.state, "image_editing_api_installed", False):
        return application
    application.state.image_editing_api_installed = True

    @application.post("/v1/images/edits", tags=["Images"])
    async def edit_image(
        request: Request,
        image: UploadFile = File(...),
        prompt: str = Form(...),
        model: str | None = Form(None),
        size: str | None = Form(None),
        seed: int | None = Form(None),
        num_inference_steps: int | None = Form(None),
        n: int = Form(1),
    ):
        manager = getattr(request.app.state, "runtime_manager", None)
        if manager is None:
            raise HTTPException(status_code=503, detail={
                "code": "runtime_unavailable", "message": "Runtime manager unavailable",
            })
        try:
            runtime = manager.resolve(model)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail={
                "code": "model_not_resident", "message": str(exc),
            }) from exc

        if not runtime.cfg.get("image_editing_enabled"):
            raise HTTPException(status_code=400, detail={
                "code": "unsupported_task",
                "message": "Start this model with --image-editing-enabled to use image edits.",
            })

        if n != 1:
            raise HTTPException(status_code=400, detail={
                "code": "invalid_request", "message": "Only one output is supported",
            })
        if image.content_type != "image/png":
            raise HTTPException(status_code=415, detail={
                "code": "unsupported_media_type", "message": "Upload one PNG image",
            })

        try:
            source = await image.read(MAX_UPLOAD_BYTES + 1)
        finally:
            await image.close()

        try:
            width, height = validate_png_reference(
                source,
                max_pixels=min(
                    MAX_SOURCE_PIXELS,
                    int(runtime.cfg.get("image_max_pixels") or MAX_SOURCE_PIXELS),
                ),
            )
            image_request = ImageGenerationRequest(
                model=runtime.key,
                prompt=prompt,
                n=n,
                size=size or f"{width}x{height}",
                seed=seed,
                num_inference_steps=num_inference_steps,
            )
            prepared = prepare_image_generation_request(
                image_request,
                runtime_key=runtime.key,
                runtime_config=runtime.cfg,
            )
            if prepared.width % 32 or prepared.height % 32:
                raise ValueError("Edited output dimensions must be multiples of 32")
            if prepared.width * prepared.height > MAX_SOURCE_PIXELS:
                raise ValueError("Edited output exceeds the first-release 1MP safety limit")
            # Describe the presence of an image without inserting raw image
            # bytes into the public canonical request or policy logs.
            canonical = replace(
                prepared.canonical,
                messages=({"role": "user", "content": [{"type": "input_image"}]},),
            )
            enforce_request_capabilities(canonical, runtime_config=runtime.cfg)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={
                "code": "invalid_request", "message": str(exc),
            }) from exc
        except InferenceError as exc:
            raise HTTPException(
                status_code=400, detail=public_error_detail(exc),
            ) from exc

        # Editing has higher transient memory requirements than text-to-image.
        # Refuse a new request under elevated pressure; swap used is not RAM.
        resource_manager = manager.resource_manager
        if resource_manager is not None and resource_manager.host_protection_enabled:
            observation = resource_manager.host_memory_status()
            if observation is None or observation.get("pressure") != "normal":
                raise HTTPException(status_code=503, detail={
                    "code": "resource_exhausted",
                    "message": "Host memory pressure is not normal; edit was not started",
                    "retryable": True,
                })

        generate = getattr(runtime.engine, "generate_image", None)
        if not callable(generate):
            raise HTTPException(status_code=400, detail={
                "code": "unsupported_task",
                "message": "Image editing backend is unavailable",
            })
        params = dict(prepared.backend_payload)
        params["ref_images"] = [
            "data:image/png;base64," + base64.b64encode(source).decode("ascii")
        ]
        source_digest = hashlib.sha256(source).hexdigest()
        started = time.perf_counter()

        def run_native_edit():
            # Native submission and polling are synchronous I/O. Keeping the
            # lease in this worker prevents blocking FastAPI's event loop for
            # the minutes an image job may take.
            with manager.lease_runtime(runtime):
                if resource_manager is not None and resource_manager.host_protection_enabled:
                    latest = resource_manager.host_memory_status()
                    if latest is None or latest.get("pressure") != "normal":
                        raise HTTPException(status_code=503, detail={
                            "code": "resource_exhausted",
                            "message": "Host memory pressure increased while edit was queued",
                            "retryable": True,
                        })
                return generate(params)

        try:
            result = await run_in_threadpool(run_native_edit)
        except (RuntimeError, ValueError, TimeoutError) as exc:
            raise HTTPException(status_code=500, detail={
                "code": "backend_error", "message": str(exc),
            }) from exc
        return {
            "created": int(time.time()),
            "model": runtime.model_id,
            "data": [{"b64_json": base64.b64encode(result.data).decode("ascii")}],
            "korgis": {
                "runtime_key": runtime.key,
                "backend": getattr(runtime.engine, "backend", "unknown"),
                "mime_type": result.mime_type,
                "width": result.width,
                "height": result.height,
                "seed": result.seed,
                "latency_ms": (time.perf_counter() - started) * 1000,
                "source_sha256": source_digest,
                "source_width": width,
                "source_height": height,
                "generation": result.metadata,
            },
        }

    return application
