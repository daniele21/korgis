"""Backend-neutral inference contracts for Local LLM Server."""

from .contracts import (
    ErrorCode,
    GenerationOptions,
    ImageGenerationOptions,
    InferenceError,
    InferenceRequest,
    InferenceResult,
    OutputConstraints,
    TaskType,
    TerminationReason,
)
from .compat import chat_payload_to_inference_request

__all__ = [
    "ErrorCode",
    "GenerationOptions",
    "ImageGenerationOptions",
    "InferenceError",
    "InferenceRequest",
    "InferenceResult",
    "OutputConstraints",
    "TaskType",
    "TerminationReason",
    "chat_payload_to_inference_request",
]
