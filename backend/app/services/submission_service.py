from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.enums import AssignmentStatus

from app.models.assignment import Assignment
from app.models.submission import Submission

from app.schemas.submission import SubmissionCreate


def create_submission(
    db: Session,
    assignment: Assignment,
    student_id: UUID,
    data: SubmissionCreate
):

    if assignment.status != AssignmentStatus.PUBLISHED:
        raise HTTPException(
            status_code=400,
            detail="Assignment is not accepting submissions"
        )

    if assignment.language != data.language:
        raise HTTPException(
            status_code=400,
            detail="Submission language does not match assignment language"
        )

    last_number = (
        db.query(
            func.max(
                Submission.submission_number
            )
        )
        .filter(
            Submission.assignment_id == assignment.id,
            Submission.student_id == student_id
        )
        .scalar()
    )

    submission_number = (
        last_number + 1
        if last_number is not None
        else 1
    )

    submission = Submission(
        assignment_id=assignment.id,
        student_id=student_id,
        submission_number=submission_number,
        source_code=data.source_code,
        language=data.language
    )

    db.add(submission)
    db.commit()
    db.refresh(submission)

    return submission


def get_submission(
    db: Session,
    submission_id: UUID
):

    return (
        db.query(Submission)
        .filter(
            Submission.id == submission_id
        )
        .first()
    )


def get_student_submissions(
    db: Session,
    assignment_id: UUID,
    student_id: UUID
):

    return (
        db.query(Submission)
        .filter(
            Submission.assignment_id == assignment_id,
            Submission.student_id == student_id
        )
        .order_by(
            Submission.submitted_at.desc()
        )
        .all()
    )