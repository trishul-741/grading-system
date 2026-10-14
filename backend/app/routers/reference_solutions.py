from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status
)

from sqlalchemy.orm import Session

from app.database import get_db

from app.schemas.reference_solution import (
    ReferenceSolutionCreate,
    ReferenceSolutionUpdate,
    ReferenceSolutionResponse
)

from app.services.assignment_service import get_assignment

from app.services.reference_solution_service import (
    get_reference_solution,
    create_reference_solution,
    update_reference_solution,
    delete_reference_solution
)


router = APIRouter(
    prefix="/assignments/{assignment_id}/reference-solution",
    tags=["Reference Solution"]
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


@router.post(
    "/",
    response_model=ReferenceSolutionResponse,
    status_code=status.HTTP_201_CREATED
)
def create(
    assignment_id: UUID,
    data: ReferenceSolutionCreate,
    db: Session = Depends(get_db)
):
    check_assignment(
        db,
        assignment_id
    )

    existing = get_reference_solution(
        db,
        assignment_id
    )

    if existing:
        raise HTTPException(
            status_code=409,
            detail="Reference solution already exists"
        )

    return create_reference_solution(
        db,
        assignment_id,
        data
    )


@router.get(
    "/",
    response_model=ReferenceSolutionResponse
)
def get(
    assignment_id: UUID,
    db: Session = Depends(get_db)
):
    check_assignment(
        db,
        assignment_id
    )

    solution = get_reference_solution(
        db,
        assignment_id
    )

    if not solution:
        raise HTTPException(
            status_code=404,
            detail="Reference solution not found"
        )

    return solution


@router.put(
    "/",
    response_model=ReferenceSolutionResponse
)
def update(
    assignment_id: UUID,
    data: ReferenceSolutionUpdate,
    db: Session = Depends(get_db)
):
    check_assignment(
        db,
        assignment_id
    )

    solution = get_reference_solution(
        db,
        assignment_id
    )

    if not solution:
        raise HTTPException(
            status_code=404,
            detail="Reference solution not found"
        )

    return update_reference_solution(
        db,
        solution,
        data
    )


@router.delete(
    "/",
    status_code=status.HTTP_204_NO_CONTENT
)
def delete(
    assignment_id: UUID,
    db: Session = Depends(get_db)
):
    check_assignment(
        db,
        assignment_id
    )

    solution = get_reference_solution(
        db,
        assignment_id
    )

    if not solution:
        raise HTTPException(
            status_code=404,
            detail="Reference solution not found"
        )

    delete_reference_solution(
        db,
        solution
    )

    return None