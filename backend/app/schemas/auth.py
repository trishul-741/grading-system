"""Authentication payloads never serialize password hashes or signing secrets."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from app.enums import UserRole


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=255)
    password: SecretStr = Field(min_length=1, max_length=1024)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        value = value.strip().lower()
        if "@" not in value or any(char.isspace() for char in value) or "\x00" in value:
            raise ValueError("A valid email address is required")
        return value

    @field_validator("password")
    @classmethod
    def validate_password_encoding(cls, value: SecretStr) -> SecretStr:
        try:
            value.get_secret_value().encode("utf-8", errors="strict")
        except UnicodeError:
            raise ValueError("Password must contain valid Unicode") from None
        return value


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(gt=0)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    email: str
    role: UserRole
    is_active: bool
