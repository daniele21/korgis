"""Pinned model-specific sampler presets for the native image backend.

Never silently substitute an ordinary eight-step schedule for Turbo's
manually specified denoising sigmas.
"""
from __future__ import annotations

import hashlib
import json
import math

QWEN_IMAGE_21_TURBO_V1 = (
    1.0, 0.978453, 0.95418, 0.926626, 0.89508,
    0.845148, 0.704534, 0.414568, 0.0,
)
_PRESETS = {"qwen-image-2.1-turbo-v1": QWEN_IMAGE_21_TURBO_V1}


def resolve_image_sigma_schedule(
    preset: str | None,
    *,
    steps: int,
    guidance_scale: float,
    sampling_method: str,
    scheduler: str | None,
) -> tuple[float, ...] | None:
    """Resolve a validated profile, rejecting incompatible request overrides."""
    if not preset:
        return None
    try:
        sigmas = _PRESETS[preset]
    except KeyError as exc:
        raise ValueError(f"Unknown image sigma schedule: {preset}") from exc

    if not 2 <= len(sigmas) <= 1001:
        raise ValueError("custom sigmas must contain 2–1001 values")
    if any(
        not math.isfinite(value) or value < 0
        for value in sigmas
    ):
        raise ValueError("custom sigmas must be nonnegative and finite")
    if any(a <= b for a, b in zip(sigmas, sigmas[1:])) or sigmas[-1] != 0:
        raise ValueError("custom sigmas must descend strictly and end at zero")
    if steps != len(sigmas) - 1:
        raise ValueError(
            f"{preset} requires exactly {len(sigmas) - 1} denoising steps, got {steps}"
        )
    if guidance_scale != 1.0:
        raise ValueError(f"{preset} requires guidance_scale=1.0")
    if sampling_method != "euler":
        raise ValueError(f"{preset} requires sampling_method=euler")
    if scheduler is not None:
        raise ValueError(f"{preset} does not accept a scheduler override")
    return sigmas


def image_sigma_digest(sigmas: tuple[float, ...]) -> str:
    """SHA-256 of the effective ordered list, for shareable evidence."""
    serialized = json.dumps(list(sigmas), separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(serialized.encode("ascii")).hexdigest()
