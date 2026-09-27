"""Diffusers-backed local image generation runtime."""
from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any, Callable, Iterator

from .model_sources import resolve_diffusers_runtime_path


@dataclass(frozen=True, slots=True)
class GeneratedImage:
    data: bytes
    mime_type: str
    width: int
    height: int
    seed: int | None
    metadata: dict[str, Any]


def _load_torch() -> Any:
    try:
        import torch
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Local image generation requires the image extra. "
            'Install with: python -m pip install -r requirements/image.txt'
        ) from exc
    return torch


def _load_pipeline(model_ref: str, *, torch_dtype: Any) -> Any:
    try:
        from diffusers import DiffusionPipeline
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Local image generation requires the image extra. "
            'Install with: python -m pip install -r requirements/image.txt'
        ) from exc
    return DiffusionPipeline.from_pretrained(model_ref, torch_dtype=torch_dtype)


def _resolve_device(torch_module: Any, configured: str) -> str:
    requested = configured.strip().lower()
    if requested != "auto":
        if requested not in {"mps", "cuda", "cpu"}:
            raise ValueError("image_device must be one of auto, mps, cuda, cpu")
        return requested

    mps = getattr(getattr(torch_module, "backends", None), "mps", None)
    if mps is not None and callable(getattr(mps, "is_available", None)) and mps.is_available():
        return "mps"
    cuda = getattr(torch_module, "cuda", None)
    if cuda is not None and callable(getattr(cuda, "is_available", None)) and cuda.is_available():
        return "cuda"
    return "cpu"


def _resolve_dtype(torch_module: Any, configured: str) -> Any:
    normalized = configured.strip().lower()
    mapping = {
        "bfloat16": "bfloat16",
        "float16": "float16",
        "float32": "float32",
    }
    attr = mapping.get(normalized)
    if attr is None:
        raise ValueError("image_dtype must be one of bfloat16, float16, float32")
    return getattr(torch_module, attr)


def _encode_image(image: Any, output_format: str) -> tuple[bytes, str]:
    normalized = output_format.strip().lower()
    formats = {
        "png": ("PNG", "image/png"),
        "jpeg": ("JPEG", "image/jpeg"),
        "jpg": ("JPEG", "image/jpeg"),
        "webp": ("WEBP", "image/webp"),
    }
    if normalized not in formats:
        raise ValueError("image output_format must be png, jpeg, jpg, or webp")

    pillow_format, mime_type = formats[normalized]
    buffer = io.BytesIO()
    image.save(buffer, format=pillow_format)
    return buffer.getvalue(), mime_type


class DiffusersImageEngine:
    """Resident Diffusers pipeline for bounded local text-to-image generation."""

    backend = "diffusers_image"
    execution_isolation = "in_process"

    def __init__(
        self,
        cfg: dict[str, Any],
        *,
        pipeline_loader: Callable[..., Any] | None = None,
        torch_module: Any | None = None,
    ) -> None:
        self.cfg = cfg
        self._torch = torch_module or _load_torch()
        reference = str(cfg["model_path"])
        resolved_model = resolve_diffusers_runtime_path(
            reference,
            no_download=bool(cfg.get("no_download", False)),
        )
        self.model_ref = str(resolved_model)
        self.cfg["model_path"] = self.model_ref

        self.device = _resolve_device(
            self._torch,
            str(cfg.get("image_device") or "auto"),
        )
        self.dtype_name = str(cfg.get("image_dtype") or "bfloat16")
        dtype = _resolve_dtype(self._torch, self.dtype_name)

        loader = pipeline_loader or _load_pipeline
        self.pipeline = loader(self.model_ref, torch_dtype=dtype)
        self.pipeline.to(self.device)

    def generate_image(self, payload: dict[str, Any]) -> GeneratedImage:
        if self.pipeline is None:
            raise RuntimeError("Diffusers image runtime is closed")

        prompt = str(payload.get("prompt") or "").strip()
        if not prompt:
            raise ValueError("image generation requires a non-empty prompt")

        width = int(payload.get("width") or self.cfg.get("image_width") or 1024)
        height = int(payload.get("height") or self.cfg.get("image_height") or 1024)
        steps = int(
            payload.get("num_inference_steps")
            or self.cfg.get("image_num_inference_steps")
            or 40
        )
        if width <= 0 or height <= 0:
            raise ValueError("image width and height must be > 0")
        if steps <= 0:
            raise ValueError("num_inference_steps must be > 0")

        seed_value = payload.get("seed")
        seed = int(seed_value) if seed_value is not None else None
        generator = None
        if seed is not None:
            generator = self._torch.Generator(device="cpu").manual_seed(seed)

        kwargs: dict[str, Any] = {
            "prompt": prompt,
            "width": width,
            "height": height,
            "num_inference_steps": steps,
        }
        if generator is not None:
            kwargs["generator"] = generator

        guidance = payload.get("guidance_scale")
        if guidance is None:
            guidance = self.cfg.get("image_guidance_scale")
        if guidance is not None:
            kwargs["guidance_scale"] = float(guidance)

        result = self.pipeline(**kwargs)
        images = getattr(result, "images", None)
        if not isinstance(images, list) or not images:
            raise RuntimeError("Diffusers pipeline returned no images")
        image = images[0]

        output_format = str(
            payload.get("output_format")
            or self.cfg.get("image_output_format")
            or "png"
        )
        data, mime_type = _encode_image(image, output_format)
        actual_width, actual_height = getattr(image, "size", (width, height))
        return GeneratedImage(
            data=data,
            mime_type=mime_type,
            width=int(actual_width),
            height=int(actual_height),
            seed=seed,
            metadata={
                "device": self.device,
                "dtype": self.dtype_name,
                "num_inference_steps": steps,
                "guidance_scale": (
                    float(guidance) if guidance is not None else None
                ),
            },
        )

    def complete(self, _payload: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("diffusers_image does not support chat completion")

    def stream(self, _payload: dict[str, Any]) -> Iterator[dict[str, Any]]:
        raise RuntimeError("diffusers_image does not support streaming chat")
        yield  # pragma: no cover

    def close(self) -> None:
        self.pipeline = None
        if self.device == "mps":
            empty_cache = getattr(getattr(self._torch, "mps", None), "empty_cache", None)
            if callable(empty_cache):
                empty_cache()
        elif self.device == "cuda":
            empty_cache = getattr(getattr(self._torch, "cuda", None), "empty_cache", None)
            if callable(empty_cache):
                empty_cache()

    shutdown = close
