from uuid import UUID

from sqlalchemy.orm import Session

from app.enums import TestStatus
from app.models.test_case import TestCase
from app.schemas.test_case import (
    TestCaseCreate,
    TestCaseUpdate
)


def create_test_case(
    db: Session,
    assignment_id: UUID,
    data: TestCaseCreate
):

    test_case = TestCase(
        assignment_id=assignment_id,
        input=data.input,
        expected_output=data.expected_output,
        visibility=data.visibility,
        source=data.source,
        category=data.category,
        status=TestStatus.DRAFT
    )

    db.add(test_case)
    db.commit()
    db.refresh(test_case)

    return test_case


def get_test_cases(
    db: Session,
    assignment_id: UUID
):

    return (
        db.query(TestCase)
        .filter(
            TestCase.assignment_id == assignment_id
        )
        .order_by(TestCase.created_at.asc())
        .all()
    )


def get_test_case(
    db: Session,
    test_case_id: UUID
):

    return (
        db.query(TestCase)
        .filter(
            TestCase.id == test_case_id
        )
        .first()
    )


def update_test_case(
    db: Session,
    test_case: TestCase,
    data: TestCaseUpdate
):

    update_data = data.model_dump(
        exclude_unset=True
    )

    for key, value in update_data.items():
        setattr(test_case, key, value)

    # Any modification makes the previous validation invalid.
    test_case.status = TestStatus.DRAFT

    db.commit()
    db.refresh(test_case)

    return test_case


def validate_test_case(
    db: Session,
    test_case: TestCase
):

    test_case.status = TestStatus.VALIDATED

    db.commit()
    db.refresh(test_case)

    return test_case


def approve_test_case(
    db: Session,
    test_case: TestCase
):

    test_case.status = TestStatus.APPROVED

    db.commit()
    db.refresh(test_case)

    return test_case


def reject_test_case(
    db: Session,
    test_case: TestCase
):

    test_case.status = TestStatus.REJECTED

    db.commit()
    db.refresh(test_case)

    return test_case


def delete_test_case(
    db: Session,
    test_case: TestCase
):

    db.delete(test_case)
    db.commit()