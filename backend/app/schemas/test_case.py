from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.enums import (
    TestVisibility,
    TestSource,
    TestCategory,
    TestStatus
)


class TestCaseCreate(BaseModel):

    input: str = Field(
        ...,
        min_length=1
    )

    expected_output: str = Field(
        ...,
        min_length=1
    )

    visibility: TestVisibility = TestVisibility.HIDDEN

    source: TestSource = TestSource.INSTRUCTOR

    category: TestCategory = TestCategory.NORMAL


class TestCaseUpdate(BaseModel):

    input: str | None = Field(
        default=None,
        min_length=1
    )

    expected_output: str | None = Field(
        default=None,
        min_length=1
    )

    visibility: TestVisibility | None = None

    category: TestCategory | None = None


class TestCaseResponse(BaseModel):

    id: UUID
    assignment_id: UUID

    input: str
    expected_output: str

    visibility: TestVisibility
    source: TestSource
    category: TestCategory
    status: TestStatus

    created_at: datetime

    model_config = ConfigDict(
        from_attributes=True
    )