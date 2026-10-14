from uuid import UUID

from sqlalchemy.orm import Session

from app.models.rubric import Rubric
from app.schemas.rubric import (
    RubricCreate,
    RubricUpdate
)


def create_rubric(
    db: Session,
    assignment_id: UUID,
    data: RubricCreate
):

    rubric = Rubric(
        assignment_id=assignment_id,
        criterion=data.criterion,
        description=data.description,
        max_marks=data.max_marks,
        weight=data.weight,
        evaluation_type=data.evaluation_type
    )

    db.add(rubric)
    db.commit()
    db.refresh(rubric)

    return rubric


def get_rubrics(
    db: Session,
    assignment_id: UUID
):

    return (
        db.query(Rubric)
        .filter(
            Rubric.assignment_id == assignment_id
        )
        .all()
    )


def get_rubric(
    db: Session,
    rubric_id: UUID
):

    return (
        db.query(Rubric)
        .filter(Rubric.id == rubric_id)
        .first()
    )


def update_rubric(
    db: Session,
    rubric: Rubric,
    data: RubricUpdate
):

    update_data = data.model_dump(
        exclude_unset=True
    )

    for key, value in update_data.items():
        setattr(rubric, key, value)

    db.commit()
    db.refresh(rubric)

    return rubric


def delete_rubric(
    db: Session,
    rubric: Rubric
):

    db.delete(rubric)
    db.commit()