"""MFlux/MLX-backed local image generation runtime."""
from __future__ import annotations

import gc
import secrets
from collections.abc import Callable
from typing import Any, Iterator

from .image_generation_common import GeneratedImage, encode_image
from .model_sources import resolve_mflux_image_runtime_path


def _load_mflux_qwen21(model_ref: str) -> Any:
    try:
        from mflux.models.common.config import ModelConfig
        from mflux.models.qwen21.variants.txt2img.qwen_image_21 import QwenImage21
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Local MLX image generation requires the MFlux image dependencies. "
            "Install with: python -m pip install -r requirements/image-mlx.txt"
        ) from exc

    # quantize=None is deliberate. MFlux reads quantization_level from its saved
    # checkpoint and reconstructs the stored precision instead of requantizing it.
    return QwenImage21(
        quantize=None,
        model_path=model_ref,
        model_config=ModelConfig.qwen_image_21(),
    )


def _default_seed() -> int:
    return secrets.randbits(32)


class MFluxImageEngine:
    """Resident MFlux Qwen-Image runtime preserving stored checkpoint quantization."""

    backend = "mflux_image"
    execution_isolation = "in_process"

    def __init__(
        self,
        cfg: dict[str, Any],
        *,
        model_loader: Callable[[str], Any] | None = None,
        seed_factory: Callable[[], int] | None = None,
    ) -> None:
        self.cfg = cfg
        reference = str(cfg["model_path"])
        expected_bits = cfg.get("image_quantization_bits")
        expected_bits = int(expected_bits) if expected_bits is not None else None
        resolved_model = resolve_mflux_image_runtime_path(
            reference,
            no_download=bool(cfg.get("no_download", False)),
            expected_quantization_bits=expected_bits,
        )
        self.model_ref = str(resolved_model)
        self.cfg["model_path"] = self.model_ref
        self._seed_factory = seed_factory or _default_seed
        loader = model_loader or _load_mflux_qwen21
        self.model = loader(self.model_ref)
        actual_bits = getattr(self.model, "bits", None)
        if (
            expected_bits is not None
            and actual_bits is not None
            and int(actual_bits) != expected_bits
        ):
            raise RuntimeError(
                "MFlux checkpoint quantization does not match configured "
                f"Q{expected_bits}: loaded Q{actual_bits}"
            )

    def generate_image(self, payload: dict[str, Any]) -> GeneratedImage:
        if self.model is None:
            raise RuntimeError("MFlux image runtime is closed")

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
        if width % 32 or height % 32:
            raise ValueError("MFlux Qwen Image 2.1 dimensions must be multiples of 32")
        if steps < 2:
            raise ValueError("MFlux Qwen Image 2.1 requires at least two inference steps")

        seed_value = payload.get("seed")
        seed = int(seed_value) if seed_value is not None else int(self._seed_factory())

        guidance = payload.get("guidance_scale")
        if guidance is None:
            guidance = self.cfg.get("image_guidance_scale")
        guidance_value = float(guidance) if guidance is not None else 1.0

        result = self.model.generate_image(
            seed=seed,
            prompt=prompt,
            num_inference_steps=steps,
            width=width,
            height=height,
            guidance=guidance_value,
        )
        image = getattr(result, "image", None)
        if image is None:
            raise RuntimeError("MFlux image model returned no image")

        output_format = str(
            payload.get("output_format")
            or self.cfg.get("image_output_format")
            or "png"
        )
        data, mime_type = encode_image(image, output_format)
        actual_width, actual_height = getattr(image, "size", (width, height))
        quantization = getattr(result, "quantization", None)
        if quantization is None:
            quantization = getattr(self.model, "bits", None)

        return GeneratedImage(
            data=data,
            mime_type=mime_type,
            width=int(actual_width),
            height=int(actual_height),
            seed=seed,
            metadata={
                "runtime": "mflux",
                "quantization_bits": (
                    int(quantization) if quantization is not None else None
                ),
                "num_inference_steps": steps,
                "guidance_scale": guidance_value,
                "generation_time_seconds": getattr(
                    result,
                    "generation_time",
                    None,
                ),
            },
        )

    def complete(self, _payload: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("mflux_image does not support chat completion")

    def stream(self, _payload: dict[str, Any]) -> Iterator[dict[str, Any]]:
        raise RuntimeError("mflux_image does not support streaming chat")
        yield  # pragma: no cover

    def close(self) -> None:
        self.model = None
        gc.collect()
        try:
            import mlx.core as mx
        except ModuleNotFoundError:
            return
        mx.clear_cache()

    shutdown = close
