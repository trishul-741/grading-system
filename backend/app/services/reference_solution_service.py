"""Reference solutions require assignment staff access before lookup."""

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.authorization import (
    authenticated_actor, is_student, require_assignment_manage, require_assignment_staff,
)
from app.models.reference_solution import ReferenceSolution
from app.models.user import User
from app.schemas.reference_solution import ReferenceSolutionCreate, ReferenceSolutionUpdate


def _require_staff(db: Session, assignment_id: UUID, *, actor: User) -> None:
    actor = authenticated_actor(db, actor)
    # A student's response never depends on whether a reference row exists.
    if is_student(actor):
        raise HTTPException(403, "Reference solutions are restricted to assignment staff")
    require_assignment_staff(db, assignment_id, actor)


def get_reference_solution(
    db: Session, assignment_id: UUID, *, actor: User,
) -> ReferenceSolution | None:
    _require_staff(db, assignment_id, actor=actor)
    return db.scalar(select(ReferenceSolution).where(ReferenceSolution.assignment_id == assignment_id)
                     .execution_options(autoflush=False, populate_existing=True))


def create_reference_solution(
    db: Session, assignment_id: UUID, data: ReferenceSolutionCreate, *, actor: User,
) -> ReferenceSolution:
    require_assignment_manage(db, assignment_id, actor)
    if get_reference_solution(db, assignment_id, actor=actor) is not None:
        raise HTTPException(409, "Reference solution already exists")
    solution = ReferenceSolution(assignment_id=assignment_id, **data.model_dump())
    db.add(solution)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Reference solution already exists or conflicted with another operation") from exc
    db.refresh(solution)
    return solution


def update_reference_solution(
    db: Session, solution: ReferenceSolution, data: ReferenceSolutionUpdate, *, actor: User,
) -> ReferenceSolution:
    require_assignment_manage(db, solution.assignment_id, actor)
    stored = get_reference_solution(db, solution.assignment_id, actor=actor)
    if stored is None or stored.id != solution.id:
        raise HTTPException(404, "Reference solution not found")
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(stored, key, value)
    db.commit()
    db.refresh(stored)
    return stored


def delete_reference_solution(
    db: Session, solution: ReferenceSolution, *, actor: User,
) -> None:
    require_assignment_manage(db, solution.assignment_id, actor)
    stored = get_reference_solution(db, solution.assignment_id, actor=actor)
    if stored is None or stored.id != solution.id:
        raise HTTPException(404, "Reference solution not found")
    db.delete(stored)
    db.commit()
