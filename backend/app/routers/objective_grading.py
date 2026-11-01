"""Development-gated grading coordinated by authenticated assignment staff."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth.dependencies import get_current_user
from app.models.user import User
from app.grading.config import ObjectiveGradingSettings, get_objective_grading_settings
from app.grading.errors import GradingError
from app.schemas.objective_grading import ObjectiveGradingRequest, ObjectiveGradingRunResponse
from app.services.objective_grading_service import (
    enqueue_objective_grading, get_objective_grading_run, get_latest_objective_grading_run,
)


router = APIRouter(prefix="/assignments/{assignment_id}/submissions/{submission_id}", tags=["Objective Grading"])


def require_objective_grading_access(
    settings: ObjectiveGradingSettings = Depends(get_objective_grading_settings),
) -> ObjectiveGradingSettings:
    if not settings.api_enabled or settings.environment != "development":
        raise HTTPException(404, "Objective grading API is disabled")
    return settings


@router.post("/grade/objective", response_model=ObjectiveGradingRunResponse, status_code=202)
def queue_objective_grading(
    assignment_id: UUID, submission_id: UUID,
    data: ObjectiveGradingRequest = ObjectiveGradingRequest(),
    actor: User = Depends(get_current_user),
    settings: ObjectiveGradingSettings = Depends(require_objective_grading_access),
    db: Session = Depends(get_db),
) -> ObjectiveGradingRunResponse:
    try:
        run = enqueue_objective_grading(db, assignment_id, submission_id, actor=actor, settings=settings)
        return ObjectiveGradingRunResponse.model_validate(run)
    except GradingError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc


@router.get("/grading-runs/latest", response_model=ObjectiveGradingRunResponse)
def latest_objective_grading(
    assignment_id: UUID, submission_id: UUID,
    actor: User = Depends(get_current_user),
    settings: ObjectiveGradingSettings = Depends(require_objective_grading_access),
    db: Session = Depends(get_db),
) -> ObjectiveGradingRunResponse:
    try:
        return ObjectiveGradingRunResponse.model_validate(get_latest_objective_grading_run(db, assignment_id, submission_id, actor=actor))
    except GradingError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc


@router.get("/grading-runs/{run_id}", response_model=ObjectiveGradingRunResponse)
def read_objective_grading(
    assignment_id: UUID, submission_id: UUID, run_id: UUID,
    actor: User = Depends(get_current_user),
    settings: ObjectiveGradingSettings = Depends(require_objective_grading_access),
    db: Session = Depends(get_db),
) -> ObjectiveGradingRunResponse:
    try:
        return ObjectiveGradingRunResponse.model_validate(get_objective_grading_run(db, assignment_id, submission_id, run_id, actor=actor))
    except GradingError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
