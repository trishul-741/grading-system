from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status
)

from sqlalchemy.orm import Session

from app.database import get_db

from app.schemas.submission import (
    SubmissionCreate,
    SubmissionResponse
)

from app.services.assignment_service import (
    get_assignment
)

from app.services.submission_service import (
    create_submission,
    get_submission,
    get_student_submissions
)


router = APIRouter(
    prefix="/assignments/{assignment_id}/submissions",
    tags=["Submissions"]
)


DEMO_STUDENT_ID = UUID(
    "00000000-0000-0000-0000-000000000002"
)


@router.post(
    "/",
    response_model=SubmissionResponse,
    status_code=status.HTTP_201_CREATED
)
def submit_code(
    assignment_id: UUID,
    data: SubmissionCreate,
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

    submission = create_submission(
        db=db,
        assignment=assignment,
        student_id=DEMO_STUDENT_ID,
        data=data
    )

    return submission


@router.get(
    "/",
    response_model=list[SubmissionResponse]
)
def get_my_submissions(
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

    return get_student_submissions(
        db,
        assignment_id,
        DEMO_STUDENT_ID
    )


@router.get(
    "/{submission_id}",
    response_model=SubmissionResponse
)
def get_one_submission(
    assignment_id: UUID,
    submission_id: UUID,
    db: Session = Depends(get_db)
):

    submission = get_submission(
        db,
        submission_id
    )

    if not submission:
        raise HTTPException(
            status_code=404,
            detail="Submission not found"
        )

    if submission.assignment_id != assignment_id:
        raise HTTPException(
            status_code=400,
            detail="Submission does not belong to this assignment"
        )

    if submission.student_id != DEMO_STUDENT_ID:
        raise HTTPException(
            status_code=403,
            detail="You cannot access this submission"
        )

    return submission