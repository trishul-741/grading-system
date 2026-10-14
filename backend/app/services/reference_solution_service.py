from uuid import UUID

from sqlalchemy.orm import Session

from app.models.reference_solution import ReferenceSolution
from app.schemas.reference_solution import (
    ReferenceSolutionCreate,
    ReferenceSolutionUpdate
)


def get_reference_solution(
    db: Session,
    assignment_id: UUID
):
    return (
        db.query(ReferenceSolution)
        .filter(
            ReferenceSolution.assignment_id == assignment_id
        )
        .first()
    )


def create_reference_solution(
    db: Session,
    assignment_id: UUID,
    data: ReferenceSolutionCreate
):
    solution = ReferenceSolution(
        assignment_id=assignment_id,
        language=data.language,
        source_code=data.source_code
    )

    db.add(solution)
    db.commit()
    db.refresh(solution)

    return solution


def update_reference_solution(
    db: Session,
    solution: ReferenceSolution,
    data: ReferenceSolutionUpdate
):
    update_data = data.model_dump(
        exclude_unset=True
    )

    for key, value in update_data.items():
        setattr(solution, key, value)

    db.commit()
    db.refresh(solution)

    return solution


def delete_reference_solution(
    db: Session,
    solution: ReferenceSolution
):
    db.delete(solution)
    db.commit()