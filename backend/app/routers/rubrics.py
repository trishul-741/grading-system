from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.auth.authorization import require_assignment_manage
from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models.user import User
from app.schemas.rubric import RubricCreate, RubricUpdate, RubricResponse
from app.services.rubric_service import (
    create_rubric, get_rubrics, get_rubric, update_rubric, delete_rubric,
)


router = APIRouter(prefix="/assignments/{assignment_id}/rubrics", tags=["Rubrics"])


@router.post("/", response_model=RubricResponse, status_code=status.HTTP_201_CREATED)
def create(
    assignment_id: UUID, data: RubricCreate, db: Session = Depends(get_db),
    actor: User = Depends(get_current_user),
):
    return create_rubric(db, assignment_id, data, actor=actor)


@router.get("/", response_model=list[RubricResponse])
def get_all(
    assignment_id: UUID, db: Session = Depends(get_db),
    actor: User = Depends(get_current_user),
):
    return get_rubrics(db, assignment_id, actor=actor)


@router.put("/{rubric_id}", response_model=RubricResponse)
def update(
    assignment_id: UUID, rubric_id: UUID, data: RubricUpdate,
    db: Session = Depends(get_db), actor: User = Depends(get_current_user),
):
    require_assignment_manage(db, assignment_id, actor)
    rubric = get_rubric(db, rubric_id, actor=actor, assignment_id=assignment_id)
    return update_rubric(db, rubric, data, actor=actor)


@router.delete("/{rubric_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete(
    assignment_id: UUID, rubric_id: UUID, db: Session = Depends(get_db),
    actor: User = Depends(get_current_user),
):
    require_assignment_manage(db, assignment_id, actor)
    rubric = get_rubric(db, rubric_id, actor=actor, assignment_id=assignment_id)
    delete_rubric(db, rubric, actor=actor)
    return None
