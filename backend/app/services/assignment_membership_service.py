from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.auth.authorization import require_assignment_manage
from app.enums import UserRole
from app.models.auth import AssignmentTAMembership
from app.models.user import User


def list_assignment_tas(db: Session, assignment_id: UUID, *, actor: User) -> list[AssignmentTAMembership]:
    require_assignment_manage(db, assignment_id, actor)
    return list(db.scalars(select(AssignmentTAMembership).where(
        AssignmentTAMembership.assignment_id == assignment_id,
    ).order_by(AssignmentTAMembership.created_at, AssignmentTAMembership.ta_id)))


def grant_assignment_ta(db: Session, assignment_id: UUID, ta_id: UUID,
                       *, actor: User) -> AssignmentTAMembership:
    require_assignment_manage(db, assignment_id, actor)
    ta = db.get(User, ta_id, populate_existing=True)
    if ta is None or ta.role != UserRole.TA or not ta.is_active:
        raise HTTPException(404, "Active teaching assistant not found")
    try:
        membership = db.get(AssignmentTAMembership, (assignment_id, ta_id))
        if membership is None:
            membership = AssignmentTAMembership(assignment_id=assignment_id, ta_id=ta_id, granted_by=actor.id)
            db.add(membership)
            db.commit()
            db.refresh(membership)
        return membership
    except IntegrityError:
        db.rollback()
        membership = db.get(AssignmentTAMembership, (assignment_id, ta_id))
        if membership is not None:
            return membership
        raise HTTPException(409, "Membership changed concurrently; retry later") from None
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(503, "Membership storage is unavailable") from None


def revoke_assignment_ta(db: Session, assignment_id: UUID, ta_id: UUID, *, actor: User) -> None:
    require_assignment_manage(db, assignment_id, actor)
    try:
        membership = db.get(AssignmentTAMembership, (assignment_id, ta_id))
        if membership is not None:
            db.delete(membership)
            db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(503, "Membership storage is unavailable") from None
