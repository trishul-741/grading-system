from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth.authorization import require_assignment_manage
from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models.user import User
from app.schemas.reference_solution import (
    ReferenceSolutionCreate, ReferenceSolutionUpdate, ReferenceSolutionResponse,
)
from app.services.reference_solution_service import (
    get_reference_solution, create_reference_solution,
    update_reference_solution, delete_reference_solution,
)


router = APIRouter(
    prefix="/assignments/{assignment_id}/reference-solution", tags=["Reference Solution"],
)


@router.post("/", response_model=ReferenceSolutionResponse, status_code=status.HTTP_201_CREATED)
def create(
    assignment_id: UUID, data: ReferenceSolutionCreate, db: Session = Depends(get_db),
    actor: User = Depends(get_current_user),
):
    return create_reference_solution(db, assignment_id, data, actor=actor)


@router.get("/", response_model=ReferenceSolutionResponse)
def get(
    assignment_id: UUID, db: Session = Depends(get_db),
    actor: User = Depends(get_current_user),
):
    solution = get_reference_solution(db, assignment_id, actor=actor)
    if solution is None:
        raise HTTPException(404, "Reference solution not found")
    return solution


@router.put("/", response_model=ReferenceSolutionResponse)
def update(
    assignment_id: UUID, data: ReferenceSolutionUpdate, db: Session = Depends(get_db),
    actor: User = Depends(get_current_user),
):
    require_assignment_manage(db, assignment_id, actor)
    solution = get_reference_solution(db, assignment_id, actor=actor)
    if solution is None:
        raise HTTPException(404, "Reference solution not found")
    return update_reference_solution(db, solution, data, actor=actor)


@router.delete("/", status_code=status.HTTP_204_NO_CONTENT)
def delete(
    assignment_id: UUID, db: Session = Depends(get_db),
    actor: User = Depends(get_current_user),
):
    require_assignment_manage(db, assignment_id, actor)
    solution = get_reference_solution(db, assignment_id, actor=actor)
    if solution is None:
        raise HTTPException(404, "Reference solution not found")
    delete_reference_solution(db, solution, actor=actor)
    return None
