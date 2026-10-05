"""Composable product/control-plane API routes outside the legacy server monolith."""
from __future__ import annotations

import os
from pathlib import Path
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, status
from pydantic import BaseModel, Field

from .cold_state import install_cold_state
from .core.contracts import ErrorCode, InferenceError
from .evaluation_builtin import GENERAL_PURPOSE_V1
from .evaluation_history_api import install_evaluation_history_api
from .evaluation_service import (
    EvaluationRunRequest,
    EvaluationService,
    EvaluationStore,
    report_to_dict,
)
from .evaluation_testsets import CustomTestSetStore, parse_test_set_bytes
from .live_evidence import manager_evidence_payload
from .resource_observation import resource_observation_payload
from .resource_policy import ResourcePolicySettings, resource_policy_snapshot
from .scheduler_evidence import scheduler_evidence_payload
from .transcription import ResidentTranscriptionService, TranscriptionRequest

_MAX_TRANSCRIPTION_BYTES = 100 * 1024 * 1024
_MAX_TEST_SET_BYTES = 5 * 1024 * 1024


class EvaluationRunBody(BaseModel):
    model: str = Field(..., min_length=1)
    test_set_id: str = Field("general-purpose", min_length=1)
    test_set_version: str | None = None
    sample_count: int = Field(20, ge=10)
    seed: int = 0
    retain_content: bool = Field(
        True,
        description="Persist generated model output in private local run history.",
    )
    reasoning_policy: str | None = Field(
        None,
        pattern="^(off|on|runtime_default)$",
        description="Explicit evaluation thinking policy; omitted uses the test-set default.",
    )


def install_product_api(
    application: FastAPI,
    *,
    evaluation_root: Path | None = None,
) -> FastAPI:
    """Install product routes and cold-state behavior exactly once."""
    if getattr(application.state, "product_api_installed", False):
        return application
    application.state.product_api_installed = True
    install_cold_state(application)

    root = evaluation_root or Path(
        os.getenv(
            "LOCAL_LLM_EVALUATION_DIR",
            str(Path.home() / ".local-llm-server" / "evaluations"),
        )
    )
    application.state.evaluation_store = EvaluationStore(root)
    application.state.evaluation_test_set_store = CustomTestSetStore(
        root / "test_sets",
        reserved_ids={GENERAL_PURPOSE_V1.test_set_id},
    )

    async def transcribe_audio(
        request: Request,
        file: UploadFile = File(...),
        model: str = Form(...),
        language: str | None = Form(None),
        prompt: str | None = Form(None),
    ):
        audio = await file.read(_MAX_TRANSCRIPTION_BYTES + 1)
        if len(audio) > _MAX_TRANSCRIPTION_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail={
                    "code": "audio_too_large",
                    "max_bytes": _MAX_TRANSCRIPTION_BYTES,
                },
            )
        try:
            result = ResidentTranscriptionService(
                request.app.state.runtime_manager
            ).transcribe(
                TranscriptionRequest(
                    model=model,
                    audio=audio,
                    filename=file.filename,
                    language=language,
                    prompt=prompt,
                )
            )
        except InferenceError as exc:
            raise _http_error(exc) from exc
        return {
            "text": result.text,
            "model": result.model,
            "language": result.language,
            "duration_seconds": result.duration_seconds,
            "segments": [dict(segment) for segment in result.segments],
        }

    application.add_api_route(
        "/v1/audio/transcriptions",
        transcribe_audio,
        methods=["POST"],
        tags=["Inference"],
        name="transcribe_audio",
    )

    settings = getattr(application.state, "settings", None)
    if not bool(getattr(settings, "enable_admin_api", False)):
        return application

    def evaluation_service(request: Request) -> EvaluationService:
        return EvaluationService(
            request.app.state.runtime_manager,
            store=request.app.state.evaluation_store,
            test_set_store=request.app.state.evaluation_test_set_store,
        )

    def get_resource_state(request: Request):
        policy_settings = getattr(
            request.app.state,
            "resource_policy_settings",
            ResourcePolicySettings(),
        )
        manager = request.app.state.runtime_manager
        return {
            **resource_policy_snapshot(policy_settings, manager.resource_manager),
            "observation": resource_observation_payload(manager),
        }

    def get_evidence(request: Request):
        return manager_evidence_payload(request.app.state.runtime_manager)

    async def get_scheduler_state(request: Request):
        return await scheduler_evidence_payload(request.app)

    def list_test_sets(request: Request):
        return {"test_sets": list(evaluation_service(request).list_test_sets())}

    async def import_test_set(
        request: Request,
        file: UploadFile = File(...),
        replace: bool = Form(False),
    ):
        raw = await file.read(_MAX_TEST_SET_BYTES + 1)
        if len(raw) > _MAX_TEST_SET_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail={"code": "test_set_too_large", "max_bytes": _MAX_TEST_SET_BYTES},
            )
        try:
            test_set = parse_test_set_bytes(raw)
            request.app.state.evaluation_test_set_store.save(
                test_set,
                replace=replace,
            )
        except FileExistsError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "test_set": {
                "id": test_set.test_set_id,
                "version": test_set.version,
                "identity": test_set.identity,
                "sample_count": len(test_set.samples),
                "source": "custom",
            }
        }

    def list_evaluation_runs(request: Request):
        store: EvaluationStore = request.app.state.evaluation_store
        return {"run_ids": list(store.list_run_ids())}

    def run_evaluation(body: EvaluationRunBody, request: Request):
        try:
            outcome = evaluation_service(request).run(
                EvaluationRunRequest(
                    model=body.model,
                    test_set_id=body.test_set_id,
                    test_set_version=body.test_set_version,
                    sample_count=body.sample_count,
                    seed=body.seed,
                    reasoning_policy=body.reasoning_policy,
                    retain_content=body.retain_content,
                )
            )
        except InferenceError as exc:
            raise _http_error(exc) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "evidence_grade": outcome.evidence_grade,
            "report": report_to_dict(
                outcome.report,
                include_content=True,
                include_output=True,
            ),
        }

    application.add_api_route(
        "/api/v1/resources",
        get_resource_state,
        methods=["GET"],
        tags=["Resources"],
        name="get_resource_state",
    )
    application.add_api_route(
        "/api/v1/evidence",
        get_evidence,
        methods=["GET"],
        tags=["Observability"],
        name="get_runtime_evidence",
    )
    application.add_api_route(
        "/api/v1/scheduler",
        get_scheduler_state,
        methods=["GET"],
        tags=["Observability"],
        name="get_scheduler_state",
    )
    application.add_api_route(
        "/api/v1/evaluation/test-sets",
        list_test_sets,
        methods=["GET"],
        tags=["Evaluation"],
        name="list_evaluation_test_sets",
    )
    application.add_api_route(
        "/api/v1/evaluation/test-sets/import",
        import_test_set,
        methods=["POST"],
        tags=["Evaluation"],
        name="import_evaluation_test_set",
    )
    application.add_api_route(
        "/api/v1/evaluation/runs",
        list_evaluation_runs,
        methods=["GET"],
        tags=["Evaluation"],
        name="list_evaluation_runs",
    )
    application.add_api_route(
        "/api/v1/evaluation/runs",
        run_evaluation,
        methods=["POST"],
        tags=["Evaluation"],
        name="run_evaluation",
    )
    def resolve_history_test_set(test_set_id: str, version: str | None):
        if (
            test_set_id == GENERAL_PURPOSE_V1.test_set_id
            and version == GENERAL_PURPOSE_V1.version
        ):
            return GENERAL_PURPOSE_V1
        return application.state.evaluation_test_set_store.resolve(test_set_id, version)

    install_evaluation_history_api(
        application,
        root=root,
        test_set_resolver=resolve_history_test_set,
    )
    return application


def _http_error(error: InferenceError) -> HTTPException:
    status_code = 400
    if error.code is ErrorCode.MODEL_NOT_RESIDENT:
        status_code = 409
    elif error.code is ErrorCode.RESOURCE_EXHAUSTED:
        status_code = 429
    elif error.code is ErrorCode.TIMEOUT:
        status_code = 408
    return HTTPException(
        status_code=status_code,
        detail={
            "code": error.code.value,
            "message": error.message,
            "retryable": error.retryable,
            "details": dict(error.details),
        },
    )
