from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models.user import User
from app.schemas.assignment import AssignmentCreate, AssignmentUpdate, AssignmentResponse
from app.services.assignment_service import (
    create_assignment, get_assignment, get_assignments, update_assignment, delete_assignment,
)


router = APIRouter(prefix="/assignments", tags=["Assignments"])


@router.post("/", response_model=AssignmentResponse, status_code=status.HTTP_201_CREATED)
def create(
    data: AssignmentCreate, db: Session = Depends(get_db),
    actor: User = Depends(get_current_user),
):
    return create_assignment(db, data, actor=actor)


@router.get("/", response_model=list[AssignmentResponse])
def get_all(db: Session = Depends(get_db), actor: User = Depends(get_current_user)):
    return get_assignments(db, actor=actor)


@router.get("/{assignment_id}", response_model=AssignmentResponse)
def get_one(
    assignment_id: UUID, db: Session = Depends(get_db),
    actor: User = Depends(get_current_user),
):
    return get_assignment(db, assignment_id, actor=actor)


@router.put("/{assignment_id}", response_model=AssignmentResponse)
def update(
    assignment_id: UUID, data: AssignmentUpdate, db: Session = Depends(get_db),
    actor: User = Depends(get_current_user),
):
    assignment = get_assignment(db, assignment_id, actor=actor)
    return update_assignment(db, assignment, data, actor=actor)


@router.delete("/{assignment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete(
    assignment_id: UUID, db: Session = Depends(get_db),
    actor: User = Depends(get_current_user),
):
    assignment = get_assignment(db, assignment_id, actor=actor)
    delete_assignment(db, assignment, actor=actor)
    return None
