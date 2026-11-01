"""Login and revocation; account provisioning is an operator CLI operation."""

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.auth.config import AuthSettings, get_auth_settings
from app.auth.dependencies import AuthContext, get_auth_context, get_current_user
from app.auth.service import authenticate_user, issue_session, revoke_session
from app.database import get_db
from app.models.user import User
from app.schemas.auth import LoginRequest, TokenResponse, UserResponse


router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/login", response_model=TokenResponse)
def login(data: LoginRequest, response: Response, db: Session = Depends(get_db),
          settings: AuthSettings = Depends(get_auth_settings)) -> TokenResponse:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    try:
        user = authenticate_user(db, data.email, data.password.get_secret_value())
        if user is None:
            raise HTTPException(401, "Invalid email or password",
                                headers={"WWW-Authenticate": "Bearer", "Cache-Control": "no-store"})
        return issue_session(db, user, settings)
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(503, "Authentication is temporarily unavailable") from None


@router.get("/me", response_model=UserResponse)
def me(response: Response, user: User = Depends(get_current_user)) -> User:
    response.headers["Cache-Control"] = "no-store"
    return user


@router.post("/logout", status_code=204)
def logout(context: AuthContext = Depends(get_auth_context),
           db: Session = Depends(get_db)) -> Response:
    try:
        revoke_session(db, context.session.id, context.user.id)
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(503, "Authentication is temporarily unavailable") from None
    return Response(status_code=204, headers={"Cache-Control": "no-store"})
