"""Canonical preparation for the public image-generation request shape."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping

from pydantic import BaseModel, Field

from .core import ImageGenerationOptions, InferenceRequest, TaskType


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


@dataclass(frozen=True, slots=True)
class PreparedImageGenerationRequest:
    canonical: InferenceRequest
    backend_payload: Mapping[str, Any]
    width: int
    height: int
    prompt: str


def prepare_image_generation_request(
    payload: ImageGenerationRequest,
    *,
    runtime_key: str,
    runtime_config: Mapping[str, Any],
) -> PreparedImageGenerationRequest:
    """Validate runtime-specific image bounds and build one canonical request."""
    width, height = _parse_size(payload.size, runtime_config)
    prompt = payload.prompt.strip()
    if not prompt:
        raise ValueError("image generation requires a non-empty prompt")

    max_steps = int(runtime_config.get("image_max_inference_steps") or 100)
    if (
        payload.num_inference_steps is not None
        and payload.num_inference_steps > max_steps
    ):
        raise ValueError(
            "num_inference_steps exceeds configured maximum "
            f"{max_steps}"
        )

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
        model=runtime_key,
        input_text=prompt,
        image_generation=options,
    )
    backend_payload = {
        "prompt": prompt,
        "width": width,
        "height": height,
        "seed": payload.seed,
        "num_inference_steps": payload.num_inference_steps,
        "guidance_scale": payload.guidance_scale,
        "output_format": payload.output_format,
    }
    return PreparedImageGenerationRequest(
        canonical=canonical,
        backend_payload={
            key: value
            for key, value in backend_payload.items()
            if value is not None
        },
        width=width,
        height=height,
        prompt=prompt,
    )


def _parse_size(
    value: str | None,
    runtime_config: Mapping[str, Any],
) -> tuple[int, int]:
    if value is None:
        return (
            int(runtime_config.get("image_width") or 1024),
            int(runtime_config.get("image_height") or 1024),
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
    max_pixels = int(runtime_config.get("image_max_pixels") or 4194304)
    if width * height > max_pixels:
        raise ValueError(
            f"requested image has {width * height} pixels; "
            f"configured maximum is {max_pixels}"
        )
    return width, height
