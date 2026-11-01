from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.auth.authorization import require_assignment_read
from app.database import get_db
from app.models.user import User
from app.schemas.submission import SubmissionCreate, SubmissionResponse
from app.services.submission_service import create_submission, get_submission, get_student_submissions


router = APIRouter(prefix="/assignments/{assignment_id}/submissions", tags=["Submissions"])


@router.post("/", response_model=SubmissionResponse, status_code=201)
def submit_code(assignment_id: UUID, data: SubmissionCreate, db: Session = Depends(get_db),
                actor: User = Depends(get_current_user)):
    assignment = require_assignment_read(db, assignment_id, actor)
    return create_submission(db, assignment, data, actor=actor)


@router.get("/", response_model=list[SubmissionResponse])
def get_my_submissions(assignment_id: UUID, db: Session = Depends(get_db),
                       actor: User = Depends(get_current_user)):
    return get_student_submissions(db, assignment_id, actor=actor)


@router.get("/{submission_id}", response_model=SubmissionResponse)
def get_one_submission(assignment_id: UUID, submission_id: UUID, db: Session = Depends(get_db),
                        actor: User = Depends(get_current_user)):
    return get_submission(db, submission_id, actor=actor, assignment_id=assignment_id)
