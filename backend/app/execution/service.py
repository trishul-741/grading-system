"""Database- and HTTP-independent worker entry point with bounded admission."""

from functools import lru_cache
import json
import logging
from logging.handlers import RotatingFileHandler
import threading
import time
from typing import Protocol
from uuid import UUID, uuid4

from app.execution.config import ExecutionSettings
from app.execution.languages import get_language_config, UnsupportedLanguageError
from app.execution.models import (
    ExecutionRequest, ExecutionResult, ExecutionStatus,
    ExecutionErrorCategory as Category,
)
from app.execution.sandbox import DockerSandbox, failure


logger = logging.getLogger("grading.execution")
logger.addHandler(logging.NullHandler())


class Sandbox(Protocol):
    def run(self, request: ExecutionRequest, job_id: UUID,
            cancel_event: threading.Event) -> ExecutionResult: ...


def configure_worker_logging(path: str) -> None:
    """CLI-only logging; embedding workers must configure bounded sinks too."""
    handler = RotatingFileHandler(path, maxBytes=1024 * 1024, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


class ExecutionService:
    def __init__(self, settings: ExecutionSettings | None = None,
                 sandbox: Sandbox | None = None) -> None:
        self.settings = settings or ExecutionSettings()
        self.sandbox = sandbox if sandbox is not None else DockerSandbox(self.settings)
        self._slots = threading.BoundedSemaphore(self.settings.max_concurrent_jobs)

    def execute(self, request: ExecutionRequest, *,
                cancel_event: threading.Event | None = None) -> ExecutionResult:
        job_id = uuid4()
        started = time.monotonic()
        cancellation = cancel_event if cancel_event is not None else threading.Event()
        try:
            try:
                get_language_config(request.language)
            except UnsupportedLanguageError:
                return self._record(request, failure("This programming language is not supported.",
                    category=Category.UNSUPPORTED_LANGUAGE, job_id=job_id), started)
            try:
                oversized = (len(request.source_code) > self.settings.source_limit_bytes
                    or len(request.input_data) > self.settings.input_limit_bytes
                    or len(request.source_code.encode("utf-8")) > self.settings.source_limit_bytes
                    or len(request.input_data.encode("utf-8")) > self.settings.input_limit_bytes)
            except UnicodeEncodeError:
                oversized = True
            if oversized:
                return self._record(request, failure("Source code or standard input exceeds the UTF-8 byte limit.",
                    category=Category.VALIDATION, job_id=job_id), started)
            if cancellation.is_set():
                return self._record(request, failure("Execution was cancelled.", category=Category.CANCELLED,
                                                    job_id=job_id), started)
            if not self._slots.acquire(blocking=False):
                return self._record(request, failure("The execution worker is at capacity; retry later.",
                    category=Category.CAPACITY, job_id=job_id), started)
            try:
                raw_result = self.sandbox.run(request, job_id=job_id, cancel_event=cancellation)
                if not isinstance(raw_result, ExecutionResult):
                    raise ValueError("Invalid sandbox result type")
                result = ExecutionResult.model_validate(raw_result.model_dump(warnings="error"))
                if (result.job_id != job_id
                        or len(result.stdout.encode("utf-8")) > 3 * self.settings.stdout_limit_bytes
                        or len(result.stderr.encode("utf-8")) > 3 * self.settings.stderr_limit_bytes):
                    raise ValueError("Invalid sandbox result identity/size")
            finally:
                self._slots.release()
        except Exception:
            # Docker exceptions can contain host paths or credentials. Never log
            # their text/traceback or assign a student penalty for such failures.
            result = failure("Execution infrastructure failed; retry later.", job_id=job_id)
        return self._record(request, result, started)

    @staticmethod
    def _record(request: ExecutionRequest, result: ExecutionResult,
                started: float) -> ExecutionResult:
        logger.info(json.dumps({
            "event": "execution_finished", "job_id": str(result.job_id),
            "submission_id": str(request.submission_id) if request.submission_id else None,
            "language": request.language.value, "status": result.status.value,
            "duration_ms": int((time.monotonic() - started) * 1000),
            "compilation_time_ms": result.compilation_time_ms,
            "execution_time_ms": result.execution_time_ms,
            "resource_limit": result.status.value if result.status in {
                ExecutionStatus.TIMEOUT, ExecutionStatus.MEMORY_LIMIT, ExecutionStatus.OUTPUT_LIMIT,
            } else None,
            "error_category": result.error_category.value if result.error_category else None,
            "infrastructure_failure_category": result.error_category.value if
                result.status == ExecutionStatus.SYSTEM_ERROR and result.error_category else None,
        }, separators=(",", ":")))
        return result


@lru_cache(maxsize=1)
def get_execution_service() -> ExecutionService:
    return ExecutionService()
