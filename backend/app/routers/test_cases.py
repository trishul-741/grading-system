from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.auth.authorization import require_assignment_manage
from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models.test_case import TestCase
from app.models.user import User
from app.schemas.test_case import TestCaseCreate, TestCaseUpdate, TestCaseResponse
from app.services.test_case_service import (
    create_test_case, get_test_cases, get_test_case, update_test_case,
    validate_test_case, approve_test_case, reject_test_case, delete_test_case,
)


router = APIRouter(prefix="/assignments/{assignment_id}/tests", tags=["Test Cases"])


def _managed_case(
    db: Session, assignment_id: UUID, test_case_id: UUID, *, actor: User,
) -> TestCase:
    require_assignment_manage(db, assignment_id, actor)
    return get_test_case(db, test_case_id, actor=actor, assignment_id=assignment_id)


@router.post("/", response_model=TestCaseResponse, status_code=status.HTTP_201_CREATED)
def create(
    assignment_id: UUID, data: TestCaseCreate, db: Session = Depends(get_db),
    actor: User = Depends(get_current_user),
):
    return create_test_case(db, assignment_id, data, actor=actor)


@router.get("/", response_model=list[TestCaseResponse])
def get_all(
    assignment_id: UUID, db: Session = Depends(get_db),
    actor: User = Depends(get_current_user),
):
    return get_test_cases(db, assignment_id, actor=actor)


@router.get("/{test_case_id}", response_model=TestCaseResponse)
def get_one(
    assignment_id: UUID, test_case_id: UUID, db: Session = Depends(get_db),
    actor: User = Depends(get_current_user),
):
    return get_test_case(db, test_case_id, actor=actor, assignment_id=assignment_id)


@router.put("/{test_case_id}", response_model=TestCaseResponse)
def update(
    assignment_id: UUID, test_case_id: UUID, data: TestCaseUpdate,
    db: Session = Depends(get_db), actor: User = Depends(get_current_user),
):
    test_case = _managed_case(db, assignment_id, test_case_id, actor=actor)
    return update_test_case(db, test_case, data, actor=actor)


@router.post("/{test_case_id}/validate", response_model=TestCaseResponse)
def validate(
    assignment_id: UUID, test_case_id: UUID, db: Session = Depends(get_db),
    actor: User = Depends(get_current_user),
):
    test_case = _managed_case(db, assignment_id, test_case_id, actor=actor)
    return validate_test_case(db, test_case, actor=actor)


@router.post("/{test_case_id}/approve", response_model=TestCaseResponse)
def approve(
    assignment_id: UUID, test_case_id: UUID, db: Session = Depends(get_db),
    actor: User = Depends(get_current_user),
):
    test_case = _managed_case(db, assignment_id, test_case_id, actor=actor)
    return approve_test_case(db, test_case, actor=actor)


@router.post("/{test_case_id}/reject", response_model=TestCaseResponse)
def reject(
    assignment_id: UUID, test_case_id: UUID, db: Session = Depends(get_db),
    actor: User = Depends(get_current_user),
):
    test_case = _managed_case(db, assignment_id, test_case_id, actor=actor)
    return reject_test_case(db, test_case, actor=actor)


@router.delete("/{test_case_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete(
    assignment_id: UUID, test_case_id: UUID, db: Session = Depends(get_db),
    actor: User = Depends(get_current_user),
):
    test_case = _managed_case(db, assignment_id, test_case_id, actor=actor)
    delete_test_case(db, test_case, actor=actor)
    return None
