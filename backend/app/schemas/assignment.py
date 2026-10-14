from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.enums import (
    ProgrammingLanguage,
    AssignmentStatus
)


class AssignmentCreate(BaseModel):

    title: str = Field(
        ...,
        min_length=1,
        max_length=255
    )

    description: str = Field(
        ...,
        min_length=1
    )

    language: ProgrammingLanguage

    constraints: str | None = None

    input_format: str | None = None

    output_format: str | None = None


class AssignmentUpdate(BaseModel):

    title: str | None = Field(
        default=None,
        min_length=1,
        max_length=255
    )

    description: str | None = None

    language: ProgrammingLanguage | None = None

    constraints: str | None = None

    input_format: str | None = None

    output_format: str | None = None

    status: AssignmentStatus | None = None


class AssignmentResponse(BaseModel):

    id: UUID
    created_by: UUID
    title: str
    description: str
    language: ProgrammingLanguage
    constraints: str | None
    input_format: str | None
    output_format: str | None
    status: AssignmentStatus
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(
        from_attributes=True
    )