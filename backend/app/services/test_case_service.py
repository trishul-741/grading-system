"""Hidden and unapproved tests never enter a student's ORM result set."""

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.authorization import (
    authenticated_actor, is_student, require_assignment_manage, require_assignment_read,
    visible_assignments_query,
)
from app.enums import TestStatus, TestVisibility
from app.models.assignment import Assignment
from app.models.test_case import TestCase
from app.models.user import User
from app.schemas.test_case import TestCaseCreate, TestCaseUpdate


def create_test_case(
    db: Session, assignment_id: UUID, data: TestCaseCreate, *, actor: User,
) -> TestCase:
    require_assignment_manage(db, assignment_id, actor)
    test_case = TestCase(assignment_id=assignment_id, **data.model_dump(), status=TestStatus.DRAFT)
    db.add(test_case)
    db.commit()
    db.refresh(test_case)
    return test_case


def get_test_cases(db: Session, assignment_id: UUID, *, actor: User) -> list[TestCase]:
    actor = authenticated_actor(db, actor)
    require_assignment_read(db, assignment_id, actor)
    query = select(TestCase).where(TestCase.assignment_id == assignment_id)
    if is_student(actor):
        query = query.where(
            TestCase.visibility == TestVisibility.PUBLIC, TestCase.status == TestStatus.APPROVED,
        )
    return list(db.scalars(query.order_by(TestCase.created_at.asc(), TestCase.id.asc())
                           .execution_options(autoflush=False, populate_existing=True)))


def get_test_case(
    db: Session, test_case_id: UUID, *, actor: User, assignment_id: UUID | None = None,
) -> TestCase:
    actor = authenticated_actor(db, actor)
    if assignment_id is not None:
        require_assignment_read(db, assignment_id, actor)
    visible = visible_assignments_query(db, actor).with_only_columns(Assignment.id).order_by(None)
    query = select(TestCase).where(TestCase.id == test_case_id, TestCase.assignment_id.in_(visible))
    if assignment_id is not None:
        query = query.where(TestCase.assignment_id == assignment_id)
    if is_student(actor):
        query = query.where(
            TestCase.visibility == TestVisibility.PUBLIC, TestCase.status == TestStatus.APPROVED,
        )
    test_case = db.scalar(query.execution_options(autoflush=False, populate_existing=True))
    if test_case is None:
        raise HTTPException(404, "Test case not found")
    return test_case


def _managed_test(db: Session, test_case: TestCase, *, actor: User) -> TestCase:
    require_assignment_manage(db, test_case.assignment_id, actor)
    return get_test_case(db, test_case.id, actor=actor, assignment_id=test_case.assignment_id)


def update_test_case(
    db: Session, test_case: TestCase, data: TestCaseUpdate, *, actor: User,
) -> TestCase:
    test_case = _managed_test(db, test_case, actor=actor)
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(test_case, key, value)
    # Editing content invalidates approval independently of the caller's route.
    test_case.status = TestStatus.DRAFT
    db.commit()
    db.refresh(test_case)
    return test_case


def validate_test_case(db: Session, test_case: TestCase, *, actor: User) -> TestCase:
    test_case = _managed_test(db, test_case, actor=actor)
    test_case.status = TestStatus.VALIDATED
    db.commit()
    db.refresh(test_case)
    return test_case


def approve_test_case(db: Session, test_case: TestCase, *, actor: User) -> TestCase:
    test_case = _managed_test(db, test_case, actor=actor)
    if test_case.status != TestStatus.VALIDATED:
        raise HTTPException(400, "Only validated test cases can be approved")
    test_case.status = TestStatus.APPROVED
    db.commit()
    db.refresh(test_case)
    return test_case


def reject_test_case(db: Session, test_case: TestCase, *, actor: User) -> TestCase:
    test_case = _managed_test(db, test_case, actor=actor)
    test_case.status = TestStatus.REJECTED
    db.commit()
    db.refresh(test_case)
    return test_case


def delete_test_case(db: Session, test_case: TestCase, *, actor: User) -> None:
    test_case = _managed_test(db, test_case, actor=actor)
    db.delete(test_case)
    db.commit()
