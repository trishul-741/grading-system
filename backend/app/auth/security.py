"""Established Argon2id password hashing and fixed-algorithm signed access tokens."""

import json
from datetime import datetime, timezone
from functools import lru_cache
from typing import Literal
from uuid import UUID

import jwt
from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from app.auth.config import AuthSettings


_passwords = PasswordHash.recommended()


class InvalidAccessToken(ValueError):
    pass


class AccessClaims(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    sub: UUID
    jti: UUID
    iss: str
    aud: str
    iat: int
    nbf: int
    exp: int
    type: Literal["access"]

    @model_validator(mode="after")
    def validate_times(self) -> "AccessClaims":
        if self.iat < 0 or self.nbf != self.iat or not self.iat < self.exp:
            raise ValueError("Invalid access token times")
        return self


def hash_password(password: str) -> str:
    if not password or len(password) > 1024:
        raise ValueError("Password must contain between 1 and 1024 characters")
    return _passwords.hash(password)


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    # This is a timing equalizer, never a usable account password or credential.
    return _passwords.hash("non-account timing equalizer")


# Prewarm once at application startup; an unknown email's first request must not
# pay an additional hash operation compared with other failed logins.
_dummy_hash()


def verify_password(password: str, stored_hash: str) -> bool:
    if not password or len(password) > 1024:
        return False
    try:
        return _passwords.verify(password, stored_hash)
    except (UnknownHashError, ValueError, TypeError):
        # Legacy/invalid hashes are not silently upgraded to real credentials.
        _passwords.verify(password, _dummy_hash())
        return False


def verify_dummy_password(password: str) -> None:
    _passwords.verify(password, _dummy_hash())


def create_access_token(user_id: UUID, session_id: UUID, settings: AuthSettings,
                        now: datetime | None = None) -> str:
    instant = now or datetime.now(timezone.utc)
    issued = int(instant.timestamp())
    claims = {
        "sub": str(user_id), "jti": str(session_id), "iss": settings.issuer,
        "aud": settings.audience, "iat": issued, "nbf": issued,
        "exp": issued + settings.access_token_minutes * 60, "type": "access",
    }
    return jwt.encode(claims, settings.secret_key.get_secret_value(), algorithm="HS256")


def decode_access_token(token: str, settings: AuthSettings) -> AccessClaims:
    if not token or len(token) > 4096 or not token.isascii():
        raise InvalidAccessToken("Invalid access token")
    try:
        header = jwt.get_unverified_header(token)
        if header.get("typ") != "JWT" or set(header) != {"alg", "typ"}:
            raise InvalidAccessToken("Invalid access token")
        payload = jwt.decode(
            token, settings.secret_key.get_secret_value(), algorithms=["HS256"],
            issuer=settings.issuer, audience=settings.audience,
            options={"require": ["sub", "jti", "iss", "aud", "iat", "nbf", "exp", "type"], "strict_aud": True},
        )
        claims = AccessClaims.model_validate_json(json.dumps(payload))
        if claims.exp - claims.iat > settings.access_token_minutes * 60:
            raise InvalidAccessToken("Invalid access token")
        return claims
    except (jwt.InvalidTokenError, ValidationError, ValueError, TypeError, OverflowError):
        raise InvalidAccessToken("Invalid access token") from None
