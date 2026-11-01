"""Submission identity and read scopes come exclusively from the authenticated actor."""

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.authorization import authenticated_actor, require_assignment_read, visible_assignments_query, is_student
from app.enums import AssignmentStatus, UserRole
from app.models.assignment import Assignment
from app.models.submission import Submission
from app.models.user import User
from app.schemas.submission import SubmissionCreate


def create_submission(db: Session, assignment: Assignment, data: SubmissionCreate,
                      *, actor: User) -> Submission:
    actor = authenticated_actor(db, actor)
    assignment = require_assignment_read(db, assignment.id, actor)
    if actor.role != UserRole.STUDENT:
        raise HTTPException(403, "Only students may create submissions")
    if assignment.status != AssignmentStatus.PUBLISHED:
        raise HTTPException(400, "Assignment is not accepting submissions")
    if assignment.language != data.language:
        raise HTTPException(400, "Submission language does not match assignment language")
    last_number = db.scalar(select(func.max(Submission.submission_number)).where(
        Submission.assignment_id == assignment.id, Submission.student_id == actor.id,
    ))
    submission = Submission(
        assignment_id=assignment.id, student_id=actor.id,
        submission_number=(last_number + 1 if last_number is not None else 1),
        source_code=data.source_code, language=data.language,
    )
    db.add(submission)
    db.commit()
    db.refresh(submission)
    return submission


def get_submission(db: Session, submission_id: UUID, *, actor: User,
                   assignment_id: UUID | None = None) -> Submission:
    actor = authenticated_actor(db, actor)
    if assignment_id is not None:
        require_assignment_read(db, assignment_id, actor)
    visible = visible_assignments_query(db, actor).with_only_columns(Assignment.id).order_by(None)
    query = select(Submission).where(Submission.id == submission_id, Submission.assignment_id.in_(visible))
    if assignment_id is not None:
        query = query.where(Submission.assignment_id == assignment_id)
    if is_student(actor):
        query = query.where(Submission.student_id == actor.id)
    submission = db.scalar(query)
    if submission is None:
        raise HTTPException(404, "Submission not found")
    return submission


def get_student_submissions(db: Session, assignment_id: UUID, *, actor: User) -> list[Submission]:
    actor = authenticated_actor(db, actor)
    require_assignment_read(db, assignment_id, actor)
    query = select(Submission).where(Submission.assignment_id == assignment_id)
    if is_student(actor):
        query = query.where(Submission.student_id == actor.id)
    return list(db.scalars(query.order_by(Submission.submitted_at.desc(), Submission.id.desc())))
