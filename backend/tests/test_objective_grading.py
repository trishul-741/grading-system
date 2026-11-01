"""Objective grading tests use the real ORM and a fake isolated executor.

No student program is run by these tests. Real sandbox behavior remains covered
by the separately opted-in Docker execution integration tests.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import logging
import threading
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.enums import AssignmentStatus, ProgrammingLanguage, TestStatus as CaseStatus, TestVisibility as Visibility
from app.execution.models import ExecutionErrorCategory as ErrorCategory, ExecutionResult, ExecutionStatus
from app.grading.comparison import normalize_output, outputs_match
from app.grading.config import ObjectiveGradingSettings
from app.grading.enums import RunStatus, TestOutcome as CaseOutcome
from app.grading.errors import GradingError
from app.grading.worker import ObjectiveGradingWorker
from app.models import Assignment, ObjectiveGradingRun, ObjectiveTestResult, Submission, User
from app.services.objective_grading_service import (
    enqueue_objective_grading,
    get_latest_objective_grading_run,
    get_objective_grading_run,
)


class FakeExecutor:
    def __init__(self, *results: object) -> None:
        self.results = list(results)
        self.calls = []

    def execute(self, request, *, cancel_event=None):
        self.calls.append((request, cancel_event))
        if not self.results:
            raise AssertionError("Unexpected additional execution")
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def succeeded(stdout: str) -> ExecutionResult:
    return ExecutionResult(
        status=ExecutionStatus.SUCCESS, exit_code=0, stdout=stdout,
        execution_time_ms=12, compilation_time_ms=30, job_id=uuid4(),
    )


def failed(status: ExecutionStatus) -> ExecutionResult:
    category = {
        ExecutionStatus.COMPILE_ERROR: ErrorCategory.COMPILE,
        ExecutionStatus.RUNTIME_ERROR: ErrorCategory.RUNTIME,
        ExecutionStatus.TIMEOUT: ErrorCategory.TIMEOUT,
        ExecutionStatus.MEMORY_LIMIT: ErrorCategory.MEMORY_LIMIT,
        ExecutionStatus.OUTPUT_LIMIT: ErrorCategory.OUTPUT_LIMIT,
        ExecutionStatus.SYSTEM_ERROR: ErrorCategory.INFRASTRUCTURE,
    }[status]
    return ExecutionResult(
        status=status, error_category=category, exit_code=1,
        error="Safe execution failure", stderr="bounded diagnostic", job_id=uuid4(),
    )


def enqueue(db_session, records, **kwargs):
    return enqueue_objective_grading(
        db_session, records.assignment.id, records.submission.id,
        actor=db_session.get(User, records.assignment.created_by), **kwargs,
    )


def process(db_session, queued, executor, **kwargs):
    return ObjectiveGradingWorker(db_session, executor=executor, **kwargs).process_run(queued.id)


@pytest.mark.parametrize(
    "actual,expected,equal",
    [
        ("hello\r\nworld\r", "hello\nworld\n", True),
        ("hello\n\n \t", "hello", True),
        ("", "\r\n\t ", True),
        ("hello  world", "hello world", False),
        (" hello", "hello", False),
        ("a \nb", "a\nb", False),
        ("a\t b", "a b", False),
        ("a\n\nb", "a\nb", False),
        ("Hello", "hello", False),
        ("42.", "42", False),
        ("1.0", "1", False),
        ("é", "e", False),
    ],
)
def test_text_v1_output_policy(actual: str, expected: str, equal: bool) -> None:
    assert outputs_match(actual, expected) is equal
    assert normalize_output(normalize_output(actual)) == normalize_output(actual)


@pytest.mark.parametrize(
    "actual,passed,failed_count,percentage",
    [
        (["one", "two", "three"], 3, 0, Decimal("100")),
        (["wrong", "wrong", "wrong"], 0, 3, Decimal("0")),
        (["one\r\n", "wrong", "three\n"], 2, 1, Decimal("66.6667")),
    ],
)
def test_score_and_persisted_results(db_session, objective_records, actual, passed, failed_count, percentage) -> None:
    for value in ("one", "two", "three"):
        objective_records.add_test(value + " input", value)
    queued = enqueue(db_session, objective_records)
    assert queued.status == RunStatus.QUEUED
    assert objective_records.submission.status == "QUEUED"
    executor = FakeExecutor(*(succeeded(value) for value in actual))
    completed = process(db_session, queued, executor)
    assert completed.status == RunStatus.COMPLETED
    assert completed.total_tests == 3
    assert completed.passed_tests == passed
    assert completed.failed_tests == failed_count
    assert abs(Decimal(str(completed.correctness_percentage)) - percentage) < Decimal("0.001")
    assert len(executor.calls) == 3
    assert {call[0].source_code for call in executor.calls} == {objective_records.submission.source_code}
    assert {call[0].submission_id for call in executor.calls} == {objective_records.submission.id}
    assert len(completed.results) == 3
    assert completed.completed_at is not None and completed.started_at is not None
    assert completed.comparison_policy_version == "text-v1"
    assert completed.engine_version and completed.execution_engine_version
    assert completed.source_sha256 and len(completed.source_sha256) == 64
    assert objective_records.submission.status == "SUBMITTED"
    db_session.expire_all()
    fetched = get_objective_grading_run(
        db_session, objective_records.assignment.id, objective_records.submission.id, queued.id,
        actor=db_session.get(User, objective_records.assignment.created_by),
    )
    assert fetched.status == RunStatus.COMPLETED
    assert len(fetched.results) == 3
    assert get_latest_objective_grading_run(
        db_session, objective_records.assignment.id, objective_records.submission.id,
        actor=db_session.get(User, objective_records.assignment.created_by),
    ).id == queued.id


def test_no_approved_tests_is_not_gradeable(db_session, objective_records) -> None:
    for status in (CaseStatus.DRAFT, CaseStatus.VALIDATED, CaseStatus.REJECTED):
        objective_records.add_test(status.value, "unused", status=status)
    queued = enqueue(db_session, objective_records)
    assert queued.status == RunStatus.NOT_GRADEABLE
    assert queued.total_tests == 0
    assert queued.correctness_percentage is None
    assert queued.results == []
    assert objective_records.submission.status == "SUBMITTED"
    executor = FakeExecutor()
    assert process(db_session, queued, executor) is None
    assert executor.calls == []


def test_only_approved_tests_both_visibilities_and_deterministic_order(db_session, objective_records) -> None:
    tied = datetime(2026, 1, 1, tzinfo=timezone.utc)
    second = objective_records.add_test("public-input", "public", id=UUID(int=21), created_at=tied)
    first = objective_records.add_test("hidden-input", "hidden", id=UUID(int=20), created_at=tied, visibility=Visibility.HIDDEN)
    third = objective_records.add_test("last-input", "last", id=UUID(int=1), created_at=tied + timedelta(seconds=1))
    for status in (CaseStatus.DRAFT, CaseStatus.VALIDATED, CaseStatus.REJECTED):
        objective_records.add_test("excluded-" + status.value, "excluded", status=status)
    queued = enqueue(db_session, objective_records)
    executor = FakeExecutor(succeeded("hidden"), succeeded("public"), succeeded("last"))
    completed = process(db_session, queued, executor)
    assert completed.total_tests == 3
    assert completed.passed_tests == 3
    assert [item[0].input_data for item in executor.calls] == ["hidden-input", "public-input", "last-input"]
    assert [item.test_case_id_snapshot for item in completed.results] == [first.id, second.id, third.id]


def test_enqueued_run_uses_immutable_test_snapshots(db_session, objective_records) -> None:
    original = objective_records.add_test("old-input", "old-output")
    queued = enqueue(db_session, objective_records)
    original.input = "new-input"
    original.expected_output = "new-output"
    original.status = CaseStatus.REJECTED
    db_session.commit()
    executor = FakeExecutor(succeeded("old-output"))
    completed = process(db_session, queued, executor)
    assert completed.status == RunStatus.COMPLETED
    assert completed.passed_tests == 1
    assert executor.calls[0][0].input_data == "old-input"
    assert executor.calls[0][0].source_code == objective_records.submission.source_code


def test_changed_submission_source_does_not_grade_against_stale_digest(db_session, objective_records) -> None:
    objective_records.add_test("input", "expected")
    queued = enqueue(db_session, objective_records)
    objective_records.submission.source_code = "different source after enqueue"
    db_session.commit()
    executor = FakeExecutor()
    completed = process(db_session, queued, executor)
    assert completed.status == RunStatus.INCOMPLETE
    assert completed.correctness_percentage is None
    assert executor.calls == []


@pytest.mark.parametrize(
    "status,outcome",
    [
        (ExecutionStatus.COMPILE_ERROR, CaseOutcome.COMPILE_ERROR),
        (ExecutionStatus.RUNTIME_ERROR, CaseOutcome.RUNTIME_ERROR),
        (ExecutionStatus.TIMEOUT, CaseOutcome.TIMEOUT),
        (ExecutionStatus.MEMORY_LIMIT, CaseOutcome.MEMORY_LIMIT),
        (ExecutionStatus.OUTPUT_LIMIT, CaseOutcome.OUTPUT_LIMIT),
    ],
)
def test_student_execution_failures_are_complete_zero_scores(db_session, objective_records, status, outcome) -> None:
    objective_records.add_test("input", "expected")
    completed = process(db_session, enqueue(db_session, objective_records), FakeExecutor(failed(status)))
    assert completed.status == RunStatus.COMPLETED
    assert completed.failed_tests == 1 and completed.passed_tests == 0
    assert completed.correctness_percentage == 0
    assert completed.results[0].outcome == outcome
    assert objective_records.submission.status != "GRADED"


@pytest.mark.parametrize("infrastructure", [failed(ExecutionStatus.SYSTEM_ERROR), RuntimeError("secret-infrastructure-path")])
def test_partial_infrastructure_failure_has_no_score(db_session, objective_records, infrastructure) -> None:
    for value in ("one", "two", "three"):
        objective_records.add_test(value, value)
    executor = FakeExecutor(succeeded("one"), infrastructure)
    completed = process(db_session, enqueue(db_session, objective_records), executor)
    assert completed.status == RunStatus.INCOMPLETE
    assert completed.correctness_percentage is None
    assert completed.passed_tests == 1
    assert completed.failed_tests == 0
    assert len(executor.calls) == 2
    assert len(completed.results) == 3
    assert completed.results[0].outcome == CaseOutcome.PASSED
    assert completed.results[1].outcome == CaseOutcome.SYSTEM_ERROR
    assert completed.results[2].outcome == CaseOutcome.SKIPPED
    assert objective_records.submission.status == "FAILED"


def test_duplicate_enqueues_are_conflicts_and_do_not_change_first_run(db_session, objective_records) -> None:
    objective_records.add_test("x", "x")
    first = enqueue(db_session, objective_records)
    with pytest.raises(GradingError) as caught:
        enqueue(db_session, objective_records)
    assert caught.value.status_code == 409
    assert db_session.scalars(select(ObjectiveGradingRun)).all() == [first]
    assert first.status == RunStatus.QUEUED


def test_database_unique_index_rejects_a_second_active_run(db_session, objective_records) -> None:
    objective_records.add_test("x", "x")
    first = enqueue(db_session, objective_records)
    duplicate = ObjectiveGradingRun(
        assignment_id=first.assignment_id, submission_id=first.submission_id,
        status=RunStatus.QUEUED, total_tests=1, passed_tests=0, failed_tests=0,
        engine_version=first.engine_version,
        comparison_policy_version=first.comparison_policy_version,
        execution_engine_version=first.execution_engine_version,
        source_sha256=first.source_sha256, language=first.language,
        previous_submission_status="SUBMITTED", execution_settings=first.execution_settings,
    )
    db_session.add(duplicate)
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()
    assert db_session.scalars(select(ObjectiveGradingRun.id)).all() == [first.id]


@pytest.mark.parametrize("actual,expected,outcome", [("same-A", "same-A", CaseOutcome.PASSED), ("same-B", "same-A", CaseOutcome.WRONG_ANSWER)])
def test_full_output_is_compared_before_bounded_evidence_is_stored(db_session, objective_records, actual: str, expected: str, outcome) -> None:
    objective_records.add_test("x", expected)
    settings = ObjectiveGradingSettings(evidence_limit_bytes=4)
    executor = FakeExecutor(succeeded(actual).model_copy(update={"stderr": "many diagnostics"}))
    completed = process(db_session, enqueue(db_session, objective_records, settings=settings), executor, settings=settings)
    row = completed.results[0]
    assert row.outcome == outcome
    assert row.actual_output == "same"
    assert row.actual_output_truncated is True
    assert row.stderr_truncated is True
    assert len(row.stderr.encode("utf-8")) <= 4
    assert row.actual_output_sha256 == hashlib.sha256(actual.encode()).hexdigest()


@pytest.mark.parametrize("invalid", ["source", "input", "test_count"])
def test_oversized_configuration_is_rejected_before_enqueue(db_session, objective_records, monkeypatch, invalid: str) -> None:
    objective_records.add_test("input-long", "x")
    settings = ObjectiveGradingSettings()
    if invalid == "source":
        monkeypatch.setenv("EXECUTION_SOURCE_LIMIT_BYTES", "1")
    elif invalid == "input":
        monkeypatch.setenv("EXECUTION_INPUT_LIMIT_BYTES", "1")
    else:
        objective_records.add_test("two", "two")
        settings = ObjectiveGradingSettings(max_tests=1)
    with pytest.raises(GradingError) as caught:
        enqueue(db_session, objective_records, settings=settings)
    assert caught.value.status_code == 422
    assert db_session.scalars(select(ObjectiveGradingRun)).all() == []


def test_prior_success_is_preserved_when_retry_fails(db_session, objective_records) -> None:
    objective_records.add_test("x", "x")
    first = process(db_session, enqueue(db_session, objective_records), FakeExecutor(succeeded("x")))
    second = process(db_session, enqueue(db_session, objective_records), FakeExecutor(failed(ExecutionStatus.SYSTEM_ERROR)))
    assert second.id != first.id and second.status == RunStatus.INCOMPLETE
    assert second.correctness_percentage is None
    db_session.refresh(first)
    assert first.status == RunStatus.COMPLETED and first.correctness_percentage == 100
    assert first.results[0].outcome == CaseOutcome.PASSED


def test_worker_only_claims_queued_runs(db_session, objective_records) -> None:
    objective_records.add_test("x", "x")
    queued = enqueue(db_session, objective_records)
    executor = FakeExecutor(succeeded("x"))
    worker = ObjectiveGradingWorker(db_session, executor=executor)
    assert worker.process_run(queued.id).status == RunStatus.COMPLETED
    assert worker.process_run(queued.id) is None
    assert worker.process_run(uuid4()) is None
    assert len(executor.calls) == 1


def test_worker_cancellation_is_retryable_and_has_no_score(db_session, objective_records) -> None:
    objective_records.add_test("x", "x")
    queued = enqueue(db_session, objective_records)
    event = threading.Event()
    event.set()
    executor = FakeExecutor()
    result = ObjectiveGradingWorker(db_session, executor=executor).process_run(queued.id, cancel_event=event)
    assert result.status == RunStatus.CANCELLED
    assert result.correctness_percentage is None
    assert executor.calls == []
    assert objective_records.submission.status == "FAILED"


@pytest.mark.parametrize("status", [AssignmentStatus.DRAFT, AssignmentStatus.ARCHIVED])
def test_non_gradeable_assignment_state_is_rejected(db_session, objective_records, status) -> None:
    objective_records.assignment.status = status
    db_session.commit()
    with pytest.raises(GradingError) as caught:
        enqueue(db_session, objective_records)
    assert caught.value.status_code in {400, 409}


def test_unsupported_language_rejected_before_persistence(db_session, objective_records) -> None:
    objective_records.assignment.language = ProgrammingLanguage.PYTHON
    objective_records.submission.language = ProgrammingLanguage.PYTHON
    db_session.commit()
    with pytest.raises(GradingError) as caught:
        enqueue(db_session, objective_records)
    assert caught.value.status_code == 422
    assert db_session.scalars(select(ObjectiveGradingRun)).all() == []


def test_enqueue_database_failure_rolls_back_without_execution(db_session, objective_records, monkeypatch) -> None:
    objective_records.add_test("x", "x")
    original_commit = db_session.commit
    def fail_commit():
        raise SQLAlchemyError("database-private-credentials")
    monkeypatch.setattr(db_session, "commit", fail_commit)
    with pytest.raises(GradingError) as caught:
        enqueue(db_session, objective_records)
    assert caught.value.status_code == 503
    assert "database-private-credentials" not in caught.value.detail
    monkeypatch.setattr(db_session, "commit", original_commit)
    db_session.expire_all()
    assert db_session.scalars(select(ObjectiveGradingRun)).all() == []
    assert db_session.get(Submission, objective_records.submission.id).status == "SUBMITTED"


@pytest.mark.parametrize("manual_status", ["GRADED", "ESCALATED"])
def test_worker_never_overwrites_manual_submission_transition(db_session, objective_records, manual_status: str) -> None:
    objective_records.add_test("x", "x")
    queued = enqueue(db_session, objective_records)

    class Executor:
        def execute(self, request, *, cancel_event=None):
            submission = db_session.get(Submission, request.submission_id)
            submission.status = manual_status
            db_session.commit()
            return succeeded("x")

    completed = process(db_session, queued, Executor())
    assert completed.status == RunStatus.COMPLETED
    db_session.expire_all()
    assert db_session.get(Submission, objective_records.submission.id).status == manual_status


@pytest.mark.parametrize("foreign_status", ["GRADING", "GRADED", "ESCALATED"])
def test_worker_does_not_assume_ownership_of_changed_queued_submission(db_session, objective_records, foreign_status: str) -> None:
    objective_records.add_test("x", "x")
    queued = enqueue(db_session, objective_records)
    objective_records.submission.status = foreign_status
    db_session.commit()
    executor = FakeExecutor()
    completed = process(db_session, queued, executor)
    assert completed.status == RunStatus.INCOMPLETE
    assert completed.correctness_percentage is None
    assert executor.calls == []
    db_session.expire_all()
    assert db_session.get(Submission, objective_records.submission.id).status == foreign_status


def test_worker_commits_claim_and_closes_read_transaction_before_execution(db_session, objective_records) -> None:
    objective_records.add_test("x", "x")
    queued = enqueue(db_session, objective_records)
    observations = []

    class Executor:
        def execute(self, request, *, cancel_event=None):
            observations.append(db_session.in_transaction())
            return succeeded("x")

    assert process(db_session, queued, Executor()).status == RunStatus.COMPLETED
    assert observations == [False]


@pytest.mark.parametrize("malformed", [None, "secret-execution-raw-text", {"status": "SUCCESS"}, ExecutionResult.model_construct(status=ExecutionStatus.SUCCESS, exit_code=8)])
def test_malformed_executor_results_cannot_create_a_score(db_session, objective_records, malformed) -> None:
    objective_records.add_test("private-input", "expected")
    completed = process(db_session, enqueue(db_session, objective_records), FakeExecutor(malformed))
    assert completed.status == RunStatus.INCOMPLETE
    assert completed.correctness_percentage is None
    assert completed.failed_tests == 0
    assert completed.results[0].outcome == CaseOutcome.SYSTEM_ERROR
    assert completed.results[0].actual_output == ""
    assert completed.results[0].stderr == ""


def test_worker_persistence_failure_cannot_publish_partial_score(db_session, objective_records, monkeypatch) -> None:
    objective_records.add_test("one", "one")
    objective_records.add_test("two", "two")
    queued = enqueue(db_session, objective_records)
    original_commit = db_session.commit
    failures_remaining = 1

    class Executor:
        def execute(self, request, *, cancel_event=None):
            def fail_one_commit():
                nonlocal failures_remaining
                if failures_remaining:
                    failures_remaining -= 1
                    raise SQLAlchemyError("private-persistence-credential")
                return original_commit()
            monkeypatch.setattr(db_session, "commit", fail_one_commit)
            return succeeded("one")

    completed = process(db_session, queued, Executor())
    assert completed.status == RunStatus.INCOMPLETE
    assert completed.correctness_percentage is None
    assert completed.passed_tests == 0 and completed.failed_tests == 0
    assert "private-persistence-credential" not in (completed.error_message or "")
    assert objective_records.submission.status == "FAILED"


def test_worker_logs_ids_and_outcomes_without_source_input_or_hidden_output(db_session, objective_records, caplog) -> None:
    objective_records.add_test("hidden-input-log-secret", "hidden-output-log-secret", visibility=Visibility.HIDDEN)
    with caplog.at_level(logging.INFO, logger="grading.objective"):
        completed = process(db_session, enqueue(db_session, objective_records), FakeExecutor(succeeded("hidden-output-log-secret")))
    assert completed.status == RunStatus.COMPLETED
    assert "hidden-input-log-secret" not in caplog.text
    assert "hidden-output-log-secret" not in caplog.text
    assert "objective-private-source-298345" not in caplog.text
    records = [json.loads(record.getMessage()) for record in caplog.records if record.name == "grading.objective"]
    assert records
    assert any(record.get("run_id") == str(completed.id) for record in records)
    assert any(record.get("outcome") == "PASSED" for record in records)
