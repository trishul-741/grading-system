"""Rubric visibility and mutations are checked at the service boundary."""

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.authorization import (
    require_assignment_manage, require_assignment_read, visible_assignments_query,
)
from app.models.assignment import Assignment
from app.models.rubric import Rubric
from app.models.user import User
from app.schemas.rubric import RubricCreate, RubricUpdate


def create_rubric(
    db: Session, assignment_id: UUID, data: RubricCreate, *, actor: User,
) -> Rubric:
    require_assignment_manage(db, assignment_id, actor)
    rubric = Rubric(assignment_id=assignment_id, **data.model_dump())
    db.add(rubric)
    db.commit()
    db.refresh(rubric)
    return rubric


def get_rubrics(db: Session, assignment_id: UUID, *, actor: User) -> list[Rubric]:
    require_assignment_read(db, assignment_id, actor)
    return list(db.scalars(
        select(Rubric).where(Rubric.assignment_id == assignment_id)
        .order_by(Rubric.created_at.asc(), Rubric.id.asc())
        .execution_options(autoflush=False, populate_existing=True)
    ))


def get_rubric(
    db: Session, rubric_id: UUID, *, actor: User, assignment_id: UUID | None = None,
) -> Rubric:
    if assignment_id is not None:
        require_assignment_read(db, assignment_id, actor)
    visible = visible_assignments_query(db, actor).with_only_columns(Assignment.id).order_by(None)
    query = select(Rubric).where(Rubric.id == rubric_id, Rubric.assignment_id.in_(visible))
    if assignment_id is not None:
        query = query.where(Rubric.assignment_id == assignment_id)
    rubric = db.scalar(query.execution_options(autoflush=False, populate_existing=True))
    if rubric is None:
        raise HTTPException(404, "Rubric not found")
    return rubric


def update_rubric(
    db: Session, rubric: Rubric, data: RubricUpdate, *, actor: User,
) -> Rubric:
    require_assignment_manage(db, rubric.assignment_id, actor)
    rubric = get_rubric(db, rubric.id, actor=actor, assignment_id=rubric.assignment_id)
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(rubric, key, value)
    db.commit()
    db.refresh(rubric)
    return rubric


def delete_rubric(db: Session, rubric: Rubric, *, actor: User) -> None:
    require_assignment_manage(db, rubric.assignment_id, actor)
    rubric = get_rubric(db, rubric.id, actor=actor, assignment_id=rubric.assignment_id)
    db.delete(rubric)
    db.commit()
