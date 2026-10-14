from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status
)

from sqlalchemy.orm import Session

from app.database import get_db

from app.schemas.assignment import (
    AssignmentCreate,
    AssignmentUpdate,
    AssignmentResponse
)

from app.services.assignment_service import (
    create_assignment,
    get_assignment,
    get_assignments,
    update_assignment,
    delete_assignment
)


router = APIRouter(
    prefix="/assignments",
    tags=["Assignments"]
)


# Temporary instructor ID
# We'll replace this with authentication later.
DEMO_USER_ID = UUID(
    "00000000-0000-0000-0000-000000000001"
)


@router.post(
    "/",
    response_model=AssignmentResponse,
    status_code=status.HTTP_201_CREATED
)
def create(
    data: AssignmentCreate,
    db: Session = Depends(get_db)
):

    assignment = create_assignment(
        db=db,
        data=data,
        user_id=DEMO_USER_ID
    )

    return assignment


@router.get(
    "/",
    response_model=list[AssignmentResponse]
)
def get_all(
    db: Session = Depends(get_db)
):

    return get_assignments(db)


@router.get(
    "/{assignment_id}",
    response_model=AssignmentResponse
)
def get_one(
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

    return assignment


@router.put(
    "/{assignment_id}",
    response_model=AssignmentResponse
)
def update(
    assignment_id: UUID,
    data: AssignmentUpdate,
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

    return update_assignment(
        db,
        assignment,
        data
    )


@router.delete(
    "/{assignment_id}",
    status_code=status.HTTP_204_NO_CONTENT
)
def delete(
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

    delete_assignment(
        db,
        assignment
    )

    return None