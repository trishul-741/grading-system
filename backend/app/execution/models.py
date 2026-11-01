"""Public and sandbox transport contracts, independent of HTTP/database state."""

from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.enums import ProgrammingLanguage


class ExecutionStatus(str, Enum):
    SUCCESS = "SUCCESS"
    COMPILE_ERROR = "COMPILE_ERROR"
    RUNTIME_ERROR = "RUNTIME_ERROR"
    TIMEOUT = "TIMEOUT"
    MEMORY_LIMIT = "MEMORY_LIMIT"
    OUTPUT_LIMIT = "OUTPUT_LIMIT"
    SYSTEM_ERROR = "SYSTEM_ERROR"


class ExecutionErrorCategory(str, Enum):
    VALIDATION = "VALIDATION"
    UNSUPPORTED_LANGUAGE = "UNSUPPORTED_LANGUAGE"
    COMPILE = "COMPILE"
    RUNTIME = "RUNTIME"
    TIMEOUT = "TIMEOUT"
    MEMORY_LIMIT = "MEMORY_LIMIT"
    OUTPUT_LIMIT = "OUTPUT_LIMIT"
    CAPACITY = "CAPACITY"
    INFRASTRUCTURE = "INFRASTRUCTURE"
    CANCELLED = "CANCELLED"


class ExecutionPhase(str, Enum):
    COMPILE = "COMPILE"
    RUN = "RUN"


class ExecutionRequest(BaseModel):
    """Only code and stdin are user-controlled; limits use UTF-8 byte counts."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_code: str = Field(min_length=1)
    input_data: str = ""
    language: ProgrammingLanguage = ProgrammingLanguage.CPP
    submission_id: UUID | None = None


class ExecutionResult(BaseModel):
    """Bounded decoded stream prefixes and safe, non-sensitive failure messages.

    Durations measure each isolated phase and may be unavailable on OOM or
    infrastructure failure. SYSTEM_ERROR must never penalize a student.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: ExecutionStatus
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    execution_time_ms: int | None = Field(default=None, ge=0)
    compilation_time_ms: int | None = Field(default=None, ge=0)
    error_category: ExecutionErrorCategory | None = None
    error: str | None = Field(default=None, max_length=512)
    phase: ExecutionPhase | None = None
    job_id: UUID | None = None

    @model_validator(mode="after")
    def validate_success(self) -> "ExecutionResult":
        if self.status == ExecutionStatus.SUCCESS:
            if self.exit_code != 0:
                raise ValueError("Successful execution requires exit code zero")
            if self.error is not None or self.error_category is not None:
                raise ValueError("Successful execution cannot contain an error")
        return self


class PhaseReport(BaseModel):
    """Strict private wire envelope emitted by the container's PID1 supervisor.

    The host must additionally validate decoded byte sizes and phase identity;
    base64 decoding is strict. Student processes never inherit the report FD.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    version: Literal[1]
    phase: ExecutionPhase
    status: ExecutionStatus
    exit_code: int | None = Field(ge=-64, le=255)
    duration_ms: int = Field(ge=0, le=300_000)
    stdout_b64: str
    stderr_b64: str
    artifact_b64: str | None = None
    error_category: ExecutionErrorCategory | None = None
    error: str | None = Field(default=None, max_length=512)

    @field_validator("version", mode="before")
    @classmethod
    def validate_version_type(cls, value: object) -> object:
        # Literal[1] alone accepts JSON true/1.0 through Python equality.
        if type(value) is not int:
            raise ValueError("Protocol version must be an integer")
        return value

    @model_validator(mode="after")
    def validate_semantics(self) -> "PhaseReport":
        if self.status == ExecutionStatus.SUCCESS:
            if self.exit_code != 0 or self.error is not None or self.error_category is not None:
                raise ValueError("Invalid successful phase report")
            if self.phase == ExecutionPhase.COMPILE and not self.artifact_b64:
                raise ValueError("Successful compilation requires an artifact")
        if self.artifact_b64 is not None and not (
            self.phase == ExecutionPhase.COMPILE and self.status == ExecutionStatus.SUCCESS
        ):
            raise ValueError("Artifact is permitted only for successful compilation")
        if self.phase == ExecutionPhase.COMPILE and self.status == ExecutionStatus.RUNTIME_ERROR:
            raise ValueError("Compilation cannot report a runtime error")
        if self.phase == ExecutionPhase.RUN and self.status == ExecutionStatus.COMPILE_ERROR:
            raise ValueError("Runtime cannot report a compile error")
        if self.status != ExecutionStatus.SUCCESS:
            expected_category = {
                ExecutionStatus.COMPILE_ERROR: ExecutionErrorCategory.COMPILE,
                ExecutionStatus.RUNTIME_ERROR: ExecutionErrorCategory.RUNTIME,
                ExecutionStatus.TIMEOUT: ExecutionErrorCategory.TIMEOUT,
                ExecutionStatus.MEMORY_LIMIT: ExecutionErrorCategory.MEMORY_LIMIT,
                ExecutionStatus.OUTPUT_LIMIT: ExecutionErrorCategory.OUTPUT_LIMIT,
                ExecutionStatus.SYSTEM_ERROR: ExecutionErrorCategory.INFRASTRUCTURE,
            }[self.status]
            if self.error_category != expected_category or not self.error or not self.error.strip():
                raise ValueError("Failure requires a matching category and explanation")
            if self.status in (ExecutionStatus.COMPILE_ERROR, ExecutionStatus.RUNTIME_ERROR):
                if self.exit_code is None or self.exit_code == 0:
                    raise ValueError("Student errors require a nonzero process exit code")
        return self
