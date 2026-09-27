"""Supported product HTTP composition outside the legacy server monolith."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from .completion_metrics import install_completion_metrics
from .control_plane_api import install_product_api
from .identity_api import install_identity_api
from .image_generation_api import install_image_generation_api
from .policy_evidence import install_policy_evidence_api
from .reasoning_http import install_reasoning_boundary
from .request_middleware import install_request_policy
from .request_resource_admission import install_request_resource_admission
from .request_scheduler import install_request_scheduler
from .residency_api import install_residency_api
from .scheduler_policy import RequestSchedulerSettings
from .streaming_metrics import install_streaming_metrics
from .studio_ui import install_studio_ui_routes


def install_product_http_stack(
    application: FastAPI,
    *,
    evaluation_root: Path | None = None,
    scheduler_settings: RequestSchedulerSettings | None = None,
) -> FastAPI:
    """Install admission, policy, truthful timing and modular product APIs.

    Starlette executes the last-added HTTP middleware outermost. Registration is
    intentionally transient-resource -> scheduler -> canonical policy ->
    completion metrics -> stream metrics -> reasoning boundary. Execution is the
    reverse for those three admission layers: canonical policy prepares the
    request, the optional scheduler waits for a runtime slot, then transient
    memory is reserved only when the request is ready to enter the route.
    Streaming reservations remain held until the body iterator finishes.

    The reasoning boundary receives raw route SSE only after streaming telemetry
    has observed it, and redacts hidden reasoning immediately before bytes reach
    the client. Studio UI routes are product-level navigation and remain
    available independently of the admin API. Product API installs the
    cold-state layer afterwards. Identity is public/read-only and path-free;
    residency and policy evidence routes remain admin-only.
    """
    install_request_resource_admission(application)
    install_request_scheduler(application, settings=scheduler_settings)
    install_request_policy(application)
    install_completion_metrics(application)
    install_streaming_metrics(application)
    install_reasoning_boundary(application)
    install_studio_ui_routes(application)
    install_product_api(application, evaluation_root=evaluation_root)
    install_image_generation_api(application)
    install_identity_api(application)
    install_residency_api(application)
    install_policy_evidence_api(application)
    return application
