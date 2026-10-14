from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status
)

from sqlalchemy.orm import Session

from app.database import get_db

from app.schemas.test_case import (
    TestCaseCreate,
    TestCaseUpdate,
    TestCaseResponse
)

from app.enums import TestStatus

from app.services.assignment_service import (
    get_assignment
)

from app.services.test_case_service import (
    create_test_case,
    get_test_cases,
    get_test_case,
    update_test_case,
    validate_test_case,
    approve_test_case,
    reject_test_case,
    delete_test_case
)


router = APIRouter(
    prefix="/assignments/{assignment_id}/tests",
    tags=["Test Cases"]
)


def check_assignment(
    db: Session,
    assignment_id: UUID
):

    assignment = get_assignment(
        db,
        assignment_id
    )

    if not assignment:
        raise HTTPException(
            status_code=404,
            detail="Assignment not found"
        )

    return assignment


def check_test_case(
    db: Session,
    assignment_id: UUID,
    test_case_id: UUID
):

    test_case = get_test_case(
        db,
        test_case_id
    )

    if not test_case:
        raise HTTPException(
            status_code=404,
            detail="Test case not found"
        )

    if test_case.assignment_id != assignment_id:
        raise HTTPException(
            status_code=400,
            detail="Test case does not belong to this assignment"
        )

    return test_case


@router.post(
    "/",
    response_model=TestCaseResponse,
    status_code=status.HTTP_201_CREATED
)
def create(
    assignment_id: UUID,
    data: TestCaseCreate,
    db: Session = Depends(get_db)
):

    check_assignment(
        db,
        assignment_id
    )

    return create_test_case(
        db,
        assignment_id,
        data
    )


@router.get(
    "/",
    response_model=list[TestCaseResponse]
)
def get_all(
    assignment_id: UUID,
    db: Session = Depends(get_db)
):

    check_assignment(
        db,
        assignment_id
    )

    return get_test_cases(
        db,
        assignment_id
    )


@router.get(
    "/{test_case_id}",
    response_model=TestCaseResponse
)
def get_one(
    assignment_id: UUID,
    test_case_id: UUID,
    db: Session = Depends(get_db)
):

    check_assignment(
        db,
        assignment_id
    )

    return check_test_case(
        db,
        assignment_id,
        test_case_id
    )


@router.put(
    "/{test_case_id}",
    response_model=TestCaseResponse
)
def update(
    assignment_id: UUID,
    test_case_id: UUID,
    data: TestCaseUpdate,
    db: Session = Depends(get_db)
):

    check_assignment(
        db,
        assignment_id
    )

    test_case = check_test_case(
        db,
        assignment_id,
        test_case_id
    )

    return update_test_case(
        db,
        test_case,
        data
    )


@router.post(
    "/{test_case_id}/validate",
    response_model=TestCaseResponse
)
def validate(
    assignment_id: UUID,
    test_case_id: UUID,
    db: Session = Depends(get_db)
):

    check_assignment(
        db,
        assignment_id
    )

    test_case = check_test_case(
        db,
        assignment_id,
        test_case_id
    )

    return validate_test_case(
        db,
        test_case
    )


@router.post(
    "/{test_case_id}/approve",
    response_model=TestCaseResponse
)
def approve(
    assignment_id: UUID,
    test_case_id: UUID,
    db: Session = Depends(get_db)
):

    check_assignment(
        db,
        assignment_id
    )

    test_case = check_test_case(
        db,
        assignment_id,
        test_case_id
    )

    if test_case.status != TestStatus.VALIDATED:
        raise HTTPException(
            status_code=400,
            detail="Only validated test cases can be approved"
        )

    return approve_test_case(
        db,
        test_case
    )


@router.post(
    "/{test_case_id}/reject",
    response_model=TestCaseResponse
)
def reject(
    assignment_id: UUID,
    test_case_id: UUID,
    db: Session = Depends(get_db)
):

    check_assignment(
        db,
        assignment_id
    )

    test_case = check_test_case(
        db,
        assignment_id,
        test_case_id
    )

    return reject_test_case(
        db,
        test_case
    )


@router.delete(
    "/{test_case_id}",
    status_code=status.HTTP_204_NO_CONTENT
)
def delete(
    assignment_id: UUID,
    test_case_id: UUID,
    db: Session = Depends(get_db)
):

    check_assignment(
        db,
        assignment_id
    )

    test_case = check_test_case(
        db,
        assignment_id,
        test_case_id
    )

    delete_test_case(
        db,
        test_case
    )

    return None