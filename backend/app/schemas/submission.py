from datetime import datetime
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field
)

from app.enums import ProgrammingLanguage


class SubmissionCreate(BaseModel):

    source_code: str = Field(
        ...,
        min_length=1
    )

    language: ProgrammingLanguage


class SubmissionResponse(BaseModel):

    id: UUID
    assignment_id: UUID
    student_id: UUID

    submission_number: int

    source_code: str
    language: ProgrammingLanguage

    status: str

    submitted_at: datetime

    model_config = ConfigDict(
        from_attributes=True
    )