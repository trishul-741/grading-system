"""Authentication session persistence, without HTTP ownership policy."""

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import func, update
from sqlalchemy.exc import MultipleResultsFound
from sqlalchemy.orm import Session

from app.auth.config import AuthSettings
from app.auth.security import create_access_token, verify_dummy_password, verify_password
from app.enums import UserRole
from app.models.auth import AuthSession
from app.models.user import User
from app.schemas.auth import TokenResponse


def authenticate_user(db: Session, email: str, password: str) -> User | None:
    try:
        user = (db.query(User).filter(func.lower(User.email) == email.strip().lower())
                .populate_existing().with_for_update().one_or_none())
    except (MultipleResultsFound, LookupError):
        verify_dummy_password(password)
        return None
    if user is None:
        verify_dummy_password(password)
        return None
    valid = verify_password(password, user.password_hash)
    return user if valid and user.is_active and user.role in frozenset(UserRole) else None


def issue_session(db: Session, user: User, settings: AuthSettings) -> TokenResponse:
    now = datetime.fromtimestamp(int(datetime.now(timezone.utc).timestamp()), timezone.utc)
    session_id = uuid4()
    session = AuthSession(id=session_id, user_id=user.id, created_at=now,
                          expires_at=now + timedelta(minutes=settings.access_token_minutes))
    token = create_access_token(user.id, session_id, settings, now=now)
    db.add(session)
    db.commit()
    return TokenResponse(access_token=token, expires_in=settings.access_token_minutes * 60)


def revoke_session(db: Session, session_id: UUID, user_id: UUID) -> None:
    db.execute(update(AuthSession).where(AuthSession.id == session_id,
               AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
               .values(revoked_at=datetime.now(timezone.utc)))
    db.commit()


def revoke_user_sessions(db: Session, user_id: UUID) -> None:
    """The caller commits together with password/account changes."""
    db.execute(update(AuthSession).where(AuthSession.user_id == user_id,
               AuthSession.revoked_at.is_(None)).values(revoked_at=datetime.now(timezone.utc)))
