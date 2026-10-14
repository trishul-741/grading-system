from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status
)

from sqlalchemy.orm import Session

from app.database import get_db

from app.schemas.rubric import (
    RubricCreate,
    RubricUpdate,
    RubricResponse
)

from app.services.rubric_service import (
    create_rubric,
    get_rubrics,
    get_rubric,
    update_rubric,
    delete_rubric
)

from app.services.assignment_service import get_assignment


router = APIRouter(
    prefix="/assignments/{assignment_id}/rubrics",
    tags=["Rubrics"]
)


@router.post(
    "/",
    response_model=RubricResponse,
    status_code=status.HTTP_201_CREATED
)
def create(
    assignment_id: UUID,
    data: RubricCreate,
    db: Session = Depends(get_db)
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

    return create_rubric(
        db,
        assignment_id,
        data
    )


@router.get(
    "/",
    response_model=list[RubricResponse]
)
def get_all(
    assignment_id: UUID,
    db: Session = Depends(get_db)
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

    return get_rubrics(
        db,
        assignment_id
    )


@router.put(
    "/{rubric_id}",
    response_model=RubricResponse
)
def update(
    assignment_id: UUID,
    rubric_id: UUID,
    data: RubricUpdate,
    db: Session = Depends(get_db)
):

    rubric = get_rubric(
        db,
        rubric_id
    )

    if not rubric:
        raise HTTPException(
            status_code=404,
            detail="Rubric not found"
        )

    if rubric.assignment_id != assignment_id:
        raise HTTPException(
            status_code=400,
            detail="Rubric does not belong to this assignment"
        )

    return update_rubric(
        db,
        rubric,
        data
    )


@router.delete(
    "/{rubric_id}",
    status_code=status.HTTP_204_NO_CONTENT
)
def delete(
    assignment_id: UUID,
    rubric_id: UUID,
    db: Session = Depends(get_db)
):

    rubric = get_rubric(
        db,
        rubric_id
    )

    if not rubric:
        raise HTTPException(
            status_code=404,
            detail="Rubric not found"
        )

    if rubric.assignment_id != assignment_id:
        raise HTTPException(
            status_code=400,
            detail="Rubric does not belong to this assignment"
        )

    delete_rubric(
        db,
        rubric
    )

    return None