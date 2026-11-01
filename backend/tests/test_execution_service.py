"""Worker-service unit tests use fakes; they make no Docker isolation claims."""

from concurrent.futures import ThreadPoolExecutor
import json
import logging
import threading
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.enums import ProgrammingLanguage
from app.execution.config import ExecutionSettings
from app.execution.models import (
    ExecutionErrorCategory,
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
)
from app.execution.service import ExecutionService


class FakeSandbox:
    def __init__(self, result: object = None, error: Exception | None = None) -> None:
        self.result = result if result is not None else ExecutionResult(
            status=ExecutionStatus.SUCCESS, exit_code=0, stdout="3\n"
        )
        self.error = error
        self.calls: list[tuple[ExecutionRequest, UUID, threading.Event]] = []

    def run(
        self,
        request: ExecutionRequest,
        job_id: UUID,
        cancel_event: threading.Event,
    ) -> ExecutionResult:
        self.calls.append((request, job_id, cancel_event))
        if self.error:
            raise self.error
        if isinstance(self.result, ExecutionResult) and self.result.job_id is None:
            return self.result.model_copy(update={"job_id": job_id})
        return self.result


def request(**overrides: object) -> ExecutionRequest:
    return ExecutionRequest(
        **{"source_code": "int main() {}", "input_data": "1 2\n", **overrides}
    )


def test_service_preserves_request_and_associates_job() -> None:
    sandbox = FakeSandbox()
    service = ExecutionService(sandbox=sandbox)
    submitted = request(submission_id=uuid4())
    result = service.execute(submitted)
    assert result.status == ExecutionStatus.SUCCESS
    assert result.stdout == "3\n"
    assert len(sandbox.calls) == 1
    seen_request, job_id, cancel_event = sandbox.calls[0]
    assert seen_request == submitted
    assert isinstance(job_id, UUID)
    assert result.job_id == job_id
    assert isinstance(cancel_event, threading.Event)


@pytest.mark.parametrize(
    "overrides,setting",
    [
        ({"source_code": "é" * 9}, {"source_limit_bytes": 17}),
        ({"input_data": "🙂" * 5}, {"input_limit_bytes": 19}),
    ],
)
def test_service_rejects_oversized_utf8_before_sandbox(
    overrides: dict, setting: dict
) -> None:
    sandbox = FakeSandbox()
    service = ExecutionService(settings=ExecutionSettings(**setting), sandbox=sandbox)
    result = service.execute(request(**overrides))
    assert result.status == ExecutionStatus.SYSTEM_ERROR
    assert result.error_category == ExecutionErrorCategory.VALIDATION
    assert sandbox.calls == []


def test_service_allows_exact_byte_limits() -> None:
    sandbox = FakeSandbox()
    service = ExecutionService(
        settings=ExecutionSettings(source_limit_bytes=4, input_limit_bytes=4),
        sandbox=sandbox,
    )
    assert service.execute(request(source_code="éé", input_data="🙂")).status == ExecutionStatus.SUCCESS
    assert len(sandbox.calls) == 1


@pytest.mark.parametrize("field", ["source_code", "input_data"])
def test_service_rejects_invalid_unicode_before_sandbox(field: str) -> None:
    sandbox = FakeSandbox()
    # Deliberately bypass request validation to exercise defense at the worker
    # interface as well; source min_length validation already rejects this.
    malformed = request().model_copy(update={field: "\ud800"})
    result = ExecutionService(sandbox=sandbox).execute(malformed)
    assert result.status == ExecutionStatus.SYSTEM_ERROR
    assert result.error_category == ExecutionErrorCategory.VALIDATION
    assert sandbox.calls == []


@pytest.mark.parametrize(
    "language",
    [ProgrammingLanguage.PYTHON, ProgrammingLanguage.JAVA, ProgrammingLanguage.C,
     ProgrammingLanguage.JAVASCRIPT],
)
def test_service_rejects_unimplemented_languages(language: ProgrammingLanguage) -> None:
    sandbox = FakeSandbox()
    result = ExecutionService(sandbox=sandbox).execute(request(language=language))
    assert result.status == ExecutionStatus.SYSTEM_ERROR
    assert result.error_category == ExecutionErrorCategory.UNSUPPORTED_LANGUAGE
    assert sandbox.calls == []


def test_request_forbids_user_controlled_image_options() -> None:
    with pytest.raises(ValidationError):
        ExecutionRequest(source_code="int main() {}", image="attacker/image")


@pytest.mark.parametrize(
    "status,category,exit_code",
    [
        (ExecutionStatus.COMPILE_ERROR, ExecutionErrorCategory.COMPILE, 1),
        (ExecutionStatus.RUNTIME_ERROR, ExecutionErrorCategory.RUNTIME, 7),
        (ExecutionStatus.TIMEOUT, ExecutionErrorCategory.TIMEOUT, None),
        (ExecutionStatus.MEMORY_LIMIT, ExecutionErrorCategory.MEMORY_LIMIT, None),
        (ExecutionStatus.OUTPUT_LIMIT, ExecutionErrorCategory.OUTPUT_LIMIT, None),
    ],
)
def test_service_preserves_student_failure_classification(
    status: ExecutionStatus, category: ExecutionErrorCategory, exit_code: int | None
) -> None:
    sandbox = FakeSandbox(
        ExecutionResult(
            status=status,
            error_category=category,
            exit_code=exit_code,
            stderr="bounded compiler or runtime diagnostic",
            error="Execution did not succeed",
        )
    )
    result = ExecutionService(sandbox=sandbox).execute(request())
    assert result.status == status
    assert result.error_category == category
    assert result.exit_code == exit_code
    assert result.stderr == "bounded compiler or runtime diagnostic"


def test_service_sanitizes_unexpected_infrastructure_exception() -> None:
    secret = "C:/host/private/credentials token=do-not-expose"
    sandbox = FakeSandbox(error=RuntimeError(secret))
    service = ExecutionService(sandbox=sandbox, settings=ExecutionSettings(max_concurrent_jobs=1))
    result = service.execute(request())
    assert result.status == ExecutionStatus.SYSTEM_ERROR
    assert result.error_category == ExecutionErrorCategory.INFRASTRUCTURE
    assert secret not in result.model_dump_json()
    assert result.error
    # Exceptions must release capacity so later student submissions can proceed.
    sandbox.error = None
    assert service.execute(request()).status == ExecutionStatus.SUCCESS


@pytest.mark.parametrize(
    "malformed",
    [
        "unexpected text /host/private/path",
        {"status": "UNRECOGNIZED", "stderr": "sensitive infrastructure details"},
        ExecutionResult.model_construct(status="UNRECOGNIZED", exit_code=0),
        ExecutionResult.model_construct(status=ExecutionStatus.SUCCESS, exit_code=9),
    ],
)
def test_service_rejects_malformed_sandbox_results(malformed: object) -> None:
    sandbox = FakeSandbox(malformed)
    result = ExecutionService(sandbox=sandbox).execute(request())
    assert result.status == ExecutionStatus.SYSTEM_ERROR
    assert result.error_category == ExecutionErrorCategory.INFRASTRUCTURE
    assert "sensitive infrastructure details" not in result.model_dump_json()
    assert "/host/private/path" not in result.model_dump_json()


def test_service_rejects_result_from_another_job() -> None:
    sandbox = FakeSandbox(
        ExecutionResult(status=ExecutionStatus.SUCCESS, exit_code=0, job_id=uuid4())
    )
    result = ExecutionService(sandbox=sandbox).execute(request())
    assert result.status == ExecutionStatus.SYSTEM_ERROR
    assert result.error_category == ExecutionErrorCategory.INFRASTRUCTURE
    assert result.job_id == sandbox.calls[0][1]


def test_service_cancellation_before_admission() -> None:
    cancel_event = threading.Event()
    cancel_event.set()
    sandbox = FakeSandbox()
    result = ExecutionService(sandbox=sandbox).execute(request(), cancel_event=cancel_event)
    assert result.status == ExecutionStatus.SYSTEM_ERROR
    assert result.error_category == ExecutionErrorCategory.CANCELLED
    assert sandbox.calls == []


def test_service_forwards_cancellation_event() -> None:
    cancel_event = threading.Event()
    sandbox = FakeSandbox()
    result = ExecutionService(sandbox=sandbox).execute(request(), cancel_event=cancel_event)
    assert result.status == ExecutionStatus.SUCCESS
    assert sandbox.calls[0][2] is cancel_event


def test_service_concurrent_capacity_is_bounded_and_released() -> None:
    started = threading.Event()
    release = threading.Event()

    class BlockingSandbox(FakeSandbox):
        def run(self, request, job_id, cancel_event):
            started.set()
            assert release.wait(3), "test failed to release the fake sandbox"
            return super().run(request, job_id, cancel_event)

    sandbox = BlockingSandbox()
    service = ExecutionService(
        settings=ExecutionSettings(max_concurrent_jobs=1), sandbox=sandbox
    )
    with ThreadPoolExecutor(max_workers=1) as executor:
        first = executor.submit(service.execute, request())
        try:
            assert started.wait(2), "first job did not enter fake sandbox"
            rejected = service.execute(request())
            assert rejected.status == ExecutionStatus.SYSTEM_ERROR
            assert rejected.error_category == ExecutionErrorCategory.CAPACITY
            assert sandbox.calls == []
        finally:
            release.set()
        assert first.result(timeout=2).status == ExecutionStatus.SUCCESS
    assert service.execute(request()).status == ExecutionStatus.SUCCESS
    assert len(sandbox.calls) == 2


def test_service_logs_do_not_include_source_or_input(caplog: pytest.LogCaptureFixture) -> None:
    secret_source = "int main() { /* source-private-marker-328462 */ }"
    secret_input = "stdin-private-marker-664832"
    submission_id = uuid4()
    with caplog.at_level(logging.INFO):
        result = ExecutionService(sandbox=FakeSandbox()).execute(
            request(source_code=secret_source, input_data=secret_input, submission_id=submission_id)
        )
    assert result.status == ExecutionStatus.SUCCESS
    assert caplog.records
    assert secret_source not in caplog.text
    assert secret_input not in caplog.text
    record = json.loads(caplog.records[-1].getMessage())
    assert record["job_id"] == str(result.job_id)
    assert record["submission_id"] == str(submission_id)
    assert record["language"] == "CPP"
    assert record["status"] == "SUCCESS"
    assert record["duration_ms"] >= 0
    assert record["resource_limit"] is None
    assert record["infrastructure_failure_category"] is None
