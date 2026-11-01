"""Fail-closed signing configuration, supplied by the execution environment."""

from functools import lru_cache

from fastapi import HTTPException
from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AuthSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AUTH_", extra="ignore", frozen=True,
                                     hide_input_in_errors=True)

    secret_key: SecretStr
    issuer: str = Field(default="grading-system", min_length=1, max_length=128)
    audience: str = Field(default="grading-api", min_length=1, max_length=128)
    access_token_minutes: int = Field(default=15, ge=1, le=60)

    @field_validator("secret_key")
    @classmethod
    def validate_signing_secret(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if len(raw) < 32 or len(raw) > 4096 or not raw.isascii() or not raw.isprintable():
            raise ValueError("Signing secret must contain at least 32 printable ASCII characters")
        return value


@lru_cache(maxsize=1)
def get_auth_settings() -> AuthSettings:
    try:
        return AuthSettings()
    except ValidationError:
        # Pydantic validation details can include supplied environment values.
        raise HTTPException(503, "Authentication is not configured") from None
