"""Shared assignment scopes. An out-of-scope identifier is always a 404."""

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import Select, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.enums import AssignmentStatus, UserRole
from app.models.assignment import Assignment
from app.models.user import User
from app.models.auth import AssignmentTAMembership


def authenticated_actor(db: Session, actor: User) -> User:
    """Services recheck database identity/status, rather than trusting a role claim."""
    if not isinstance(actor, User) or actor.id is None:
        raise HTTPException(401, "Authentication required", headers={"WWW-Authenticate": "Bearer"})
    try:
        with db.no_autoflush:
            current = db.get(User, actor.id, populate_existing=True)
    except LookupError:
        raise HTTPException(401, "Authentication required", headers={"WWW-Authenticate": "Bearer"}) from None
    except SQLAlchemyError:
        raise HTTPException(503, "Authorization storage is unavailable") from None
    if current is None or not current.is_active or current.role not in set(UserRole):
        raise HTTPException(401, "Authentication required", headers={"WWW-Authenticate": "Bearer"})
    return current


def is_student(actor: User) -> bool:
    return actor.role == UserRole.STUDENT


def visible_assignments_query(db: Session, actor: User) -> Select:
    actor = authenticated_actor(db, actor)
    query = select(Assignment).execution_options(autoflush=False, populate_existing=True)
    if actor.role == UserRole.ADMIN:
        return query
    if actor.role == UserRole.INSTRUCTOR:
        return query.where(Assignment.created_by == actor.id)
    if actor.role == UserRole.TA:
        return query.where(Assignment.id.in_(select(AssignmentTAMembership.assignment_id).where(
            AssignmentTAMembership.ta_id == actor.id,
        )))
    return query.where(Assignment.status.in_([AssignmentStatus.PUBLISHED, AssignmentStatus.CLOSED]))


def require_assignment_read(db: Session, assignment_id: UUID, actor: User) -> Assignment:
    with db.no_autoflush:
        assignment = db.scalar(visible_assignments_query(db, actor).where(Assignment.id == assignment_id))
    if assignment is None:
        raise HTTPException(404, "Assignment not found")
    return assignment


def require_assignment_create(db: Session, actor: User) -> User:
    actor = authenticated_actor(db, actor)
    if actor.role not in {UserRole.INSTRUCTOR, UserRole.ADMIN}:
        raise HTTPException(403, "Instructor permission is required")
    return actor


def require_assignment_manage(db: Session, assignment_id: UUID, actor: User) -> Assignment:
    actor = authenticated_actor(db, actor)
    assignment = require_assignment_read(db, assignment_id, actor)
    if actor.role not in {UserRole.INSTRUCTOR, UserRole.ADMIN}:
        raise HTTPException(403, "Instructor permission is required")
    # Read scope already enforces ownership for instructors; admins are explicit
    # global administrators, never inferred from user-supplied request fields.
    return assignment


def require_assignment_staff(db: Session, assignment_id: UUID, actor: User) -> Assignment:
    actor = authenticated_actor(db, actor)
    assignment = require_assignment_read(db, assignment_id, actor)
    if actor.role == UserRole.STUDENT:
        raise HTTPException(403, "Assignment staff permission is required")
    return assignment
