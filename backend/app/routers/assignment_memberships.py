from uuid import UUID

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models.user import User
from app.models.auth import AssignmentTAMembership
from app.schemas.authorization import AssignmentTAMembershipResponse
from app.services.assignment_membership_service import (
    grant_assignment_ta, list_assignment_tas, revoke_assignment_ta,
)


router = APIRouter(prefix="/assignments/{assignment_id}/tas", tags=["Assignment TAs"])


@router.get("", response_model=list[AssignmentTAMembershipResponse])
def list_tas(assignment_id: UUID, db: Session = Depends(get_db),
             actor: User = Depends(get_current_user)) -> list[AssignmentTAMembership]:
    return list_assignment_tas(db, assignment_id, actor=actor)


@router.put("/{ta_id}", response_model=AssignmentTAMembershipResponse)
def grant_ta(assignment_id: UUID, ta_id: UUID, db: Session = Depends(get_db),
             actor: User = Depends(get_current_user)):
    return grant_assignment_ta(db, assignment_id, ta_id, actor=actor)


@router.delete("/{ta_id}", status_code=204)
def revoke_ta(assignment_id: UUID, ta_id: UUID, db: Session = Depends(get_db),
              actor: User = Depends(get_current_user)) -> Response:
    revoke_assignment_ta(db, assignment_id, ta_id, actor=actor)
    return Response(status_code=204)
