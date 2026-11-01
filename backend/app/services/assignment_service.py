"""Assignment operations enforce ownership independently of HTTP routing."""

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.auth.authorization import (
    require_assignment_create, require_assignment_manage, require_assignment_read,
    visible_assignments_query,
)
from app.enums import AssignmentStatus, TestStatus
from app.models.assignment import Assignment
from app.models.user import User
from app.schemas.assignment import AssignmentCreate, AssignmentUpdate


def validate_assignment_for_publish(
    db: Session, assignment: Assignment, *, actor: User,
) -> bool:
    assignment = require_assignment_manage(db, assignment.id, actor)
    rubrics = assignment.rubrics
    if not rubrics:
        raise HTTPException(400, "Assignment must have at least one rubric")
    total_weight = sum(float(rubric.weight) for rubric in rubrics)
    if abs(total_weight - 100.0) > 0.001:
        raise HTTPException(400, f"Rubric weights must total 100%. Current total: {total_weight}%")
    if not any(test.status == TestStatus.APPROVED for test in assignment.test_cases):
        raise HTTPException(400, "Assignment must have at least one approved test case")
    return True


def create_assignment(
    db: Session, data: AssignmentCreate, *, actor: User,
) -> Assignment:
    creator = require_assignment_create(db, actor)
    assignment = Assignment(
        created_by=creator.id, title=data.title, description=data.description,
        language=data.language, constraints=data.constraints,
        input_format=data.input_format, output_format=data.output_format,
    )
    db.add(assignment)
    db.commit()
    db.refresh(assignment)
    return assignment


def get_assignment(
    db: Session, assignment_id: UUID, *, actor: User,
) -> Assignment:
    return require_assignment_read(db, assignment_id, actor)


def get_assignments(db: Session, *, actor: User) -> list[Assignment]:
    return list(db.scalars(
        visible_assignments_query(db, actor).order_by(Assignment.created_at.desc(), Assignment.id.asc())
    ))


def update_assignment(
    db: Session, assignment: Assignment, data: AssignmentUpdate, *, actor: User,
) -> Assignment:
    assignment = require_assignment_manage(db, assignment.id, actor)
    update_data = data.model_dump(exclude_unset=True)
    if update_data.get("status") == AssignmentStatus.PUBLISHED:
        validate_assignment_for_publish(db, assignment, actor=actor)
    for key, value in update_data.items():
        setattr(assignment, key, value)
    db.commit()
    db.refresh(assignment)
    return assignment


def delete_assignment(db: Session, assignment: Assignment, *, actor: User) -> None:
    assignment = require_assignment_manage(db, assignment.id, actor)
    db.delete(assignment)
    db.commit()
