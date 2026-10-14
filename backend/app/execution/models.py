from pydantic import BaseModel


class ExecutionResult(BaseModel):
    status: str

    stdout: str = ""
    stderr: str = ""

    exit_code: int | None = None

    execution_time_ms: int | None = None

    error: str | None = None