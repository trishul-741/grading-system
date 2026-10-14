from uuid import UUID

from sqlalchemy.orm import Session

from app.models.assignment import Assignment
from app.schemas.assignment import (
    AssignmentCreate,
    AssignmentUpdate
)

from fastapi import HTTPException

from app.enums import (
    AssignmentStatus,
    TestStatus
)

def validate_assignment_for_publish(
    db: Session,
    assignment: Assignment
):
    rubrics = assignment.rubrics

    if not rubrics:
        raise HTTPException(
            status_code=400,
            detail="Assignment must have at least one rubric"
        )

    total_weight = sum(
        float(r.weight)
        for r in rubrics
    )

    if abs(total_weight - 100.0) > 0.001:
        raise HTTPException(
            status_code=400,
            detail=f"Rubric weights must total 100%. Current total: {total_weight}%"
        )

    approved_tests = [
        test
        for test in assignment.test_cases
        if test.status == TestStatus.APPROVED
    ]

    if not approved_tests:
        raise HTTPException(
            status_code=400,
            detail="Assignment must have at least one approved test case"
        )

    return True

def create_assignment(
    db: Session,
    data: AssignmentCreate,
    user_id: UUID
):
    assignment = Assignment(
        created_by=user_id,
        title=data.title,
        description=data.description,
        language=data.language,
        constraints=data.constraints,
        input_format=data.input_format,
        output_format=data.output_format
    )

    db.add(assignment)
    db.commit()
    db.refresh(assignment)

    return assignment


def get_assignment(
    db: Session,
    assignment_id: UUID
):
    return (
        db.query(Assignment)
        .filter(Assignment.id == assignment_id)
        .first()
    )


def get_assignments(
    db: Session
):
    return (
        db.query(Assignment)
        .order_by(Assignment.created_at.desc())
        .all()
    )


def update_assignment(
    db: Session,
    assignment: Assignment,
    data: AssignmentUpdate
):
    update_data = data.model_dump(
    exclude_unset=True
    )

    if (
        "status" in update_data
        and update_data["status"] == AssignmentStatus.PUBLISHED
    ):
        validate_assignment_for_publish(
            db,
            assignment
        )

    for key, value in update_data.items():
        setattr(assignment, key, value)

    db.commit()
    db.refresh(assignment)

    return assignment


def delete_assignment(
    db: Session,
    assignment: Assignment
):
    db.delete(assignment)
    db.commit()