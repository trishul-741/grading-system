"""Public objective-run contracts; hidden evidence always remains private."""

from datetime import datetime, timezone
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.enums import ProgrammingLanguage, TestVisibility
from app.execution.models import ExecutionStatus
from app.grading.enums import RunStatus, TestOutcome


class ObjectiveGradingRequest(BaseModel):
    """No request-supplied compiler, score, test selection or limit overrides."""

    model_config = ConfigDict(extra="forbid")


class ObjectiveTestResultResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    test_case_id: UUID | None
    test_case_id_snapshot: UUID
    position: int = Field(ge=0)
    visibility: TestVisibility
    outcome: TestOutcome
    input_data: str | None = None
    expected_output: str | None = None
    actual_output: str | None = None
    stderr: str | None = None
    actual_output_truncated: bool = False
    stderr_truncated: bool = False
    execution_status: ExecutionStatus | None = None
    exit_code: int | None = None
    execution_time_ms: int | None = Field(default=None, ge=0)
    compilation_time_ms: int | None = Field(default=None, ge=0)
    error_category: str | None = Field(default=None, max_length=64)
    error_message: str | None = Field(default=None, max_length=256)
    details_redacted: bool = False

    @model_validator(mode="after")
    def redact_hidden_evidence(self) -> "ObjectiveTestResultResponse":
        # Defense in depth: from_attributes serialization must never expose a
        # hidden program's echoed input, expected output or compiler diagnostic.
        if self.visibility == TestVisibility.HIDDEN:
            self.input_data = None
            self.expected_output = None
            self.actual_output = None
            self.stderr = None
            self.error_message = None
            self.details_redacted = True
        return self


class ObjectiveGradingRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    assignment_id: UUID
    submission_id: UUID
    status: RunStatus
    total_tests: int = Field(ge=0)
    passed_tests: int = Field(ge=0)
    failed_tests: int = Field(ge=0)
    correctness_percentage: float | None = Field(default=None, ge=0, le=100)
    error_category: str | None = Field(default=None, max_length=64)
    error_message: str | None = Field(default=None, max_length=256)
    queued_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    engine_version: str
    comparison_policy_version: str
    execution_engine_version: str
    language: ProgrammingLanguage
    results: list[ObjectiveTestResultResponse] = Field(default_factory=list)

    @field_validator("queued_at", "started_at", "completed_at")
    @classmethod
    def normalize_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        # SQLite test timestamps omit the timezone; PostgreSQL stores timestamptz.
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def validate_score_state(self) -> "ObjectiveGradingRunResponse":
        if self.passed_tests + self.failed_tests > self.total_tests:
            raise ValueError("Grading counts exceed total tests")
        if self.status == RunStatus.COMPLETED:
            if (self.correctness_percentage is None or self.total_tests == 0
                    or self.passed_tests + self.failed_tests != self.total_tests):
                raise ValueError("A completed run requires a complete valid score")
        elif self.correctness_percentage is not None:
            raise ValueError("An unfinished run cannot publish a correctness score")
        return self


RunResponse = ObjectiveGradingRunResponse
PerTestResponse = ObjectiveTestResultResponse
