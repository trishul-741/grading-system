"""Load live account/session state on every protected request."""

from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.auth.config import AuthSettings, get_auth_settings
from app.auth.security import InvalidAccessToken, decode_access_token
from app.database import get_db
from app.enums import UserRole
from app.models.auth import AuthSession
from app.models.user import User


bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class AuthContext:
    user: User
    session: AuthSession


def _unauthorized() -> HTTPException:
    return HTTPException(401, "Valid authentication is required",
                         headers={"WWW-Authenticate": "Bearer", "Cache-Control": "no-store"})


def get_auth_context(
    db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    settings: AuthSettings = Depends(get_auth_settings),
) -> AuthContext:
    if credentials is None:
        raise _unauthorized()
    try:
        claims = decode_access_token(credentials.credentials, settings)
    except InvalidAccessToken:
        raise _unauthorized() from None
    try:
        session = db.get(AuthSession, claims.jti, populate_existing=True)
        user = db.get(User, claims.sub, populate_existing=True)
        now = datetime.now(timezone.utc)
        expires = session.expires_at if session is not None else None
        if expires is not None and expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if (session is None or user is None or session.user_id != user.id
                or session.revoked_at is not None or not user.is_active
                or user.role not in frozenset(UserRole)
                or expires is None or expires <= now or int(expires.timestamp()) != claims.exp):
            raise _unauthorized()
        return AuthContext(user=user, session=session)
    except LookupError:
        # Includes SQLAlchemy's Enum decoding error for an unexpected DB role.
        raise _unauthorized() from None
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(503, "Authentication is temporarily unavailable") from None


def get_current_user(context: AuthContext = Depends(get_auth_context)) -> User:
    return context.user
