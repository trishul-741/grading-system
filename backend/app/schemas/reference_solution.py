from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.enums import ProgrammingLanguage


class ReferenceSolutionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    language: ProgrammingLanguage

    source_code: str = Field(
        ...,
        min_length=1
    )


class ReferenceSolutionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    language: ProgrammingLanguage | None = None

    source_code: str | None = Field(
        default=None,
        min_length=1
    )


class ReferenceSolutionResponse(BaseModel):

    id: UUID
    assignment_id: UUID
    language: ProgrammingLanguage
    source_code: str
    created_at: datetime

    model_config = ConfigDict(
        from_attributes=True
    )
