"""Shared local image-generation output and encoding primitives."""
from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class GeneratedImage:
    data: bytes
    mime_type: str
    width: int
    height: int
    seed: int | None
    metadata: dict[str, Any]


def encode_image(image: Any, output_format: str) -> tuple[bytes, str]:
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
