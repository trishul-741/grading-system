from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.enums import EvaluationType


class RubricCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    criterion: str = Field(
        ...,
        min_length=1,
        max_length=100
    )

    description: str | None = None

    max_marks: float = Field(
        ...,
        gt=0
    )

    weight: float = Field(
        ...,
        gt=0,
        le=100
    )

    evaluation_type: EvaluationType


class RubricUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    criterion: str | None = Field(
        default=None,
        min_length=1,
        max_length=100
    )

    description: str | None = None

    max_marks: float | None = Field(
        default=None,
        gt=0
    )

    weight: float | None = Field(
        default=None,
        gt=0,
        le=100
    )

    evaluation_type: EvaluationType | None = None


class RubricResponse(BaseModel):

    id: UUID
    assignment_id: UUID
    criterion: str
    description: str | None
    max_marks: float
    weight: float
    evaluation_type: EvaluationType
    created_at: datetime

    model_config = ConfigDict(
        from_attributes=True
    )
