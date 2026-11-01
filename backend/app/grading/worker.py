"""Durable objective-job consumer. Launch as a separate process from FastAPI."""

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import signal
import threading
from typing import Protocol
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from app.enums import AssignmentStatus, ProgrammingLanguage
from app.execution.config import ExecutionSettings
from app.execution.languages import get_language_config
from app.execution.models import ExecutionRequest, ExecutionResult, ExecutionStatus, ExecutionErrorCategory
from app.grading.comparison import outputs_match, COMPARISON_POLICY_VERSION
from app.grading.config import ObjectiveGradingSettings
from app.grading.enums import RunStatus, TestOutcome
from app.grading.errors import GradingError
from app.models import Assignment, Submission
from app.models.objective_grading import ObjectiveGradingRun, ObjectiveTestResult
from app.services.objective_grading_service import GRADING_ENGINE_VERSION, EXECUTION_ENGINE_VERSION


logger = logging.getLogger("grading.objective")
_STUDENT_FAILURES = {
    TestOutcome.WRONG_ANSWER, TestOutcome.COMPILE_ERROR, TestOutcome.RUNTIME_ERROR,
    TestOutcome.TIMEOUT, TestOutcome.MEMORY_LIMIT, TestOutcome.OUTPUT_LIMIT,
}


class ExecutionExecutor(Protocol):
    def execute(self, request: ExecutionRequest, *,
                cancel_event: threading.Event | None = None) -> ExecutionResult: ...


class ConfigurationError(Exception):
    pass


@dataclass(frozen=True)
class TestSnapshot:
    id: UUID
    test_case_id: UUID
    input_data: str
    expected_output: str


@dataclass(frozen=True)
class JobSnapshot:
    id: UUID
    submission_id: UUID
    source_code: str
    language: ProgrammingLanguage
    execution_settings: ExecutionSettings
    tests: tuple[TestSnapshot, ...]


def _evidence(value: str, limit: int) -> tuple[str, bool]:
    # PostgreSQL Text cannot store NUL. Remove terminal control characters from
    # persisted evidence, without changing the raw string used for comparison.
    safe = "".join("\ufffd" if (ord(char) < 32 and char not in "\r\n\t") or ord(char) == 127
                   else char for char in value)
    encoded = safe.encode("utf-8")
    return encoded[:limit].decode("utf-8", errors="ignore"), len(encoded) > limit


class ObjectiveGradingWorker:
    def __init__(self, db: Session, executor: ExecutionExecutor | None = None,
                 settings: ObjectiveGradingSettings | None = None) -> None:
        self.db = db
        self.settings = settings or ObjectiveGradingSettings()
        self._owned_run_id: UUID | None = None
        if executor is None:
            # Only the external worker imports/constructs the execution service.
            # Keep one instance across jobs so its cleanup fault latch persists.
            from app.execution.service import get_execution_service
            executor = get_execution_service()
        self.executor = executor

    def _claim(self, run_id: UUID | None) -> UUID | None:
        self._owned_run_id = None
        query = select(ObjectiveGradingRun.id, ObjectiveGradingRun.submission_id).where(
            ObjectiveGradingRun.status == RunStatus.QUEUED,
        )
        if run_id is not None:
            query = query.where(ObjectiveGradingRun.id == run_id)
        selected = self.db.execute(query.order_by(ObjectiveGradingRun.queued_at, ObjectiveGradingRun.id)
                                   .limit(1).with_for_update(skip_locked=True)).first()
        if selected is None:
            self.db.rollback()
            return None
        claimed = self.db.execute(update(ObjectiveGradingRun).where(
            ObjectiveGradingRun.id == selected.id, ObjectiveGradingRun.status == RunStatus.QUEUED,
        ).values(status=RunStatus.RUNNING, started_at=datetime.now(timezone.utc))
            .execution_options(synchronize_session=False))
        if claimed.rowcount != 1:
            self.db.rollback()
            return None
        transitioned = self.db.execute(update(Submission).where(
            Submission.id == selected.submission_id, Submission.status == "QUEUED",
        ).values(status="GRADING").execution_options(synchronize_session=False))
        # Never hold the claim transaction open while invoking Docker.
        self.db.commit()
        if transitioned.rowcount == 1:
            self._owned_run_id = selected.id
        return selected.id

    def _snapshot(self, run_id: UUID) -> JobSnapshot:
        if self._owned_run_id != run_id:
            raise ConfigurationError()
        run = self.db.scalar(select(ObjectiveGradingRun).where(ObjectiveGradingRun.id == run_id)
                             .options(selectinload(ObjectiveGradingRun.results))
                             .execution_options(populate_existing=True))
        if run is None or run.status != RunStatus.RUNNING:
            raise ConfigurationError()
        submission = self.db.get(Submission, run.submission_id, populate_existing=True)
        assignment = self.db.get(Assignment, run.assignment_id, populate_existing=True)
        if (submission is None or assignment is None or submission.status != "GRADING"
                or submission.assignment_id != run.assignment_id
                or assignment.status not in {AssignmentStatus.PUBLISHED, AssignmentStatus.CLOSED}
                or submission.language != assignment.language or submission.language.value != run.language
                or run.engine_version != GRADING_ENGINE_VERSION
                or run.comparison_policy_version != COMPARISON_POLICY_VERSION
                or run.execution_engine_version != EXECUTION_ENGINE_VERSION):
            raise ConfigurationError()
        source = submission.source_code.encode("utf-8")
        if hashlib.sha256(source).hexdigest() != run.source_sha256:
            raise ConfigurationError()
        language = ProgrammingLanguage(run.language)
        get_language_config(language)
        limits = ExecutionSettings.model_validate(run.execution_settings)
        actual_limits = getattr(self.executor, "settings", None)
        if isinstance(actual_limits, ExecutionSettings) and actual_limits.model_dump() != limits.model_dump():
            raise ConfigurationError()
        if not source or len(source) > limits.source_limit_bytes:
            raise ConfigurationError()
        if (not run.results or len(run.results) != run.total_tests or len(run.results) > self.settings.max_tests
                or [row.position for row in run.results] != list(range(1, run.total_tests + 1))):
            raise ConfigurationError()
        snapshot_size = 0
        tests = []
        for row in run.results:
            if (row.outcome != TestOutcome.PENDING or len(row.input_data.encode("utf-8")) > limits.input_limit_bytes
                    or len(row.expected_output.encode("utf-8")) > limits.stdout_limit_bytes):
                raise ConfigurationError()
            snapshot_size += len(row.input_data.encode("utf-8")) + len(row.expected_output.encode("utf-8"))
            tests.append(TestSnapshot(row.id, row.test_case_id_snapshot, row.input_data, row.expected_output))
        if snapshot_size > self.settings.max_snapshot_bytes:
            raise ConfigurationError()
        snapshot = JobSnapshot(run.id, run.submission_id, submission.source_code, language, limits, tuple(tests))
        self.db.commit()  # Release the snapshot's read transaction before Docker.
        return snapshot

    def _execute(self, job: JobSnapshot, test: TestSnapshot,
                 cancellation: threading.Event) -> ExecutionResult:
        try:
            raw = self.executor.execute(ExecutionRequest(
                source_code=job.source_code, input_data=test.input_data,
                language=job.language, submission_id=job.submission_id,
            ), cancel_event=cancellation)
            if not isinstance(raw, ExecutionResult):
                raise ValueError("Invalid result type")
            result = ExecutionResult.model_validate(raw.model_dump(warnings="error"))
            if (len(result.stdout.encode("utf-8")) > 3 * job.execution_settings.stdout_limit_bytes
                    or len(result.stderr.encode("utf-8")) > 3 * job.execution_settings.stderr_limit_bytes):
                raise ValueError("Oversized execution evidence")
            if result.status in {ExecutionStatus.COMPILE_ERROR, ExecutionStatus.RUNTIME_ERROR} and result.exit_code in {None, 0}:
                raise ValueError("Invalid student exit status")
            return result
        except Exception:
            # Raw exceptions/results can contain secrets, paths or hidden input.
            return ExecutionResult(status=ExecutionStatus.SYSTEM_ERROR,
                error_category=ExecutionErrorCategory.INFRASTRUCTURE,
                error="Execution infrastructure failed; retry later.")

    def _save_result(self, job: JobSnapshot, test: TestSnapshot,
                     result: ExecutionResult) -> bool:
        run = self.db.scalar(select(ObjectiveGradingRun).where(ObjectiveGradingRun.id == job.id)
                             .with_for_update().execution_options(populate_existing=True))
        if run is None or run.status != RunStatus.RUNNING:
            self.db.rollback()
            return False  # Recovery/deletion revoked this worker's ownership.
        row = self.db.get(ObjectiveTestResult, test.id, populate_existing=True)
        if row is None or row.outcome != TestOutcome.PENDING:
            raise ConfigurationError()
        if result.status == ExecutionStatus.SUCCESS:
            outcome = TestOutcome.PASSED if outputs_match(result.stdout, test.expected_output) else TestOutcome.WRONG_ANSWER
        elif result.status == ExecutionStatus.SYSTEM_ERROR:
            outcome = (TestOutcome.CANCELLED if result.error_category == ExecutionErrorCategory.CANCELLED
                       else TestOutcome.SYSTEM_ERROR)
        else:
            outcome = TestOutcome(result.status.value)
        row.outcome = outcome
        row.execution_status = result.status.value
        row.execution_job_id = result.job_id
        row.exit_code = result.exit_code if result.status != ExecutionStatus.SYSTEM_ERROR else None
        row.execution_time_ms = result.execution_time_ms
        row.compilation_time_ms = result.compilation_time_ms
        # Do not persist infra error streams or arbitrary error strings. They are
        # not student evidence and may contain internal infrastructure details.
        stdout = "" if result.status == ExecutionStatus.SYSTEM_ERROR else result.stdout
        stderr = "" if result.status == ExecutionStatus.SYSTEM_ERROR else result.stderr
        row.actual_output, row.actual_output_truncated = _evidence(stdout, self.settings.evidence_limit_bytes)
        row.stderr, row.stderr_truncated = _evidence(stderr, self.settings.evidence_limit_bytes)
        row.actual_output_sha256 = hashlib.sha256(stdout.encode("utf-8")).hexdigest()
        row.error_category = (result.error_category.value if result.error_category else
                              ("WRONG_ANSWER" if outcome == TestOutcome.WRONG_ANSWER else None))
        row.error_message = (None if outcome == TestOutcome.PASSED else
                             "Output did not match the expected output." if outcome == TestOutcome.WRONG_ANSWER
                             else "Execution infrastructure could not complete this test." if result.status == ExecutionStatus.SYSTEM_ERROR
                             else "The program failed this test during execution.")
        if outcome == TestOutcome.PASSED:
            run.passed_tests += 1
        elif outcome in _STUDENT_FAILURES:
            run.failed_tests += 1
        self.db.commit()
        logger.info(json.dumps({"event": "objective_test_finished", "run_id": str(job.id),
            "submission_id": str(job.submission_id), "test_case_id": str(test.test_case_id),
            "execution_job_id": str(result.job_id) if result.job_id else None,
            "outcome": outcome.value, "execution_status": result.status.value,
            "error_category": result.error_category.value if result.error_category else None,
            "execution_time_ms": result.execution_time_ms, "compilation_time_ms": result.compilation_time_ms}, separators=(",", ":")))
        return True

    def _load(self, run_id: UUID) -> ObjectiveGradingRun | None:
        return self.db.scalar(select(ObjectiveGradingRun).where(ObjectiveGradingRun.id == run_id)
                              .options(selectinload(ObjectiveGradingRun.results))
                              .execution_options(populate_existing=True))

    def _finish(self, run_id: UUID, status: RunStatus, category: str | None = None,
                message: str | None = None) -> ObjectiveGradingRun | None:
        try:
            run = self.db.scalar(select(ObjectiveGradingRun).where(ObjectiveGradingRun.id == run_id)
                                 .with_for_update().options(selectinload(ObjectiveGradingRun.results))
                                 .execution_options(populate_existing=True))
            if run is None or run.status != RunStatus.RUNNING:
                self.db.rollback()
                return self._load(run_id)
            passed = sum(row.outcome == TestOutcome.PASSED for row in run.results)
            failed = sum(row.outcome in _STUDENT_FAILURES for row in run.results)
            if status == RunStatus.COMPLETED and (passed + failed != run.total_tests
                                                  or len(run.results) != run.total_tests or not run.total_tests):
                status, category, message = RunStatus.INCOMPLETE, "INTERNAL_ERROR", "The test suite did not finish completely."
            for row in run.results:
                if row.outcome == TestOutcome.PENDING:
                    row.outcome = TestOutcome.SKIPPED
                    row.error_category = category
                    row.error_message = "No committed result is available because this grading run did not complete."
            run.status = status
            run.passed_tests, run.failed_tests = passed, failed
            run.correctness_percentage = ((Decimal(passed) / Decimal(run.total_tests) * 100)
                .quantize(Decimal("0.001"), rounding=ROUND_HALF_UP) if status == RunStatus.COMPLETED else None)
            run.error_category, run.error_message = category, message
            run.completed_at = datetime.now(timezone.utc)
            submission_id = run.submission_id
            # Status is conditional: never overwrite GRADED/ESCALATED or another
            # coordinator's manual transition. Partial unique ownership is still
            # held by this transaction while releasing its submission state.
            if self._owned_run_id == run_id:
                self.db.execute(update(Submission).where(Submission.id == submission_id,
                    Submission.status == "GRADING").values(status="SUBMITTED" if status == RunStatus.COMPLETED else "FAILED")
                    .execution_options(synchronize_session=False))
            self.db.commit()
            result = self._load(run_id)
            if result is not None:
                logger.info(json.dumps({"event": "objective_run_finished", "run_id": str(run_id),
                    "submission_id": str(submission_id), "status": result.status.value,
                    "total_tests": result.total_tests, "passed_tests": result.passed_tests,
                    "failed_tests": result.failed_tests,
                    "correctness_percentage": str(result.correctness_percentage) if result.correctness_percentage is not None else None,
                    "error_category": result.error_category, "engine_version": result.engine_version,
                    "execution_engine_version": result.execution_engine_version,
                    "comparison_policy_version": result.comparison_policy_version}, separators=(",", ":")))
            return result
        except SQLAlchemyError as exc:
            self.db.rollback()
            raise GradingError(503, "Grading results could not be persisted; the run needs recovery") from exc

    def process_run(self, run_id: UUID | None = None, *,
                    cancel_event: threading.Event | None = None) -> ObjectiveGradingRun | None:
        cancellation = cancel_event if cancel_event is not None else threading.Event()
        try:
            claimed_id = self._claim(run_id)
        except SQLAlchemyError as exc:
            self.db.rollback()
            raise GradingError(503, "Grading persistence is unavailable; retry later") from exc
        if claimed_id is None:
            return None
        try:
            job = self._snapshot(claimed_id)
            for test in job.tests:
                if cancellation.is_set():
                    return self._finish(job.id, RunStatus.CANCELLED, "CANCELLED", "Grading was cancelled; no score was assigned.")
                result = self._execute(job, test, cancellation)
                if not self._save_result(job, test, result):
                    return self._load(job.id)
                if result.status == ExecutionStatus.SYSTEM_ERROR:
                    cancelled = result.error_category == ExecutionErrorCategory.CANCELLED or cancellation.is_set()
                    return self._finish(job.id, RunStatus.CANCELLED if cancelled else RunStatus.INCOMPLETE,
                        "CANCELLED" if cancelled else result.error_category.value if result.error_category else "INFRASTRUCTURE",
                        "Grading was cancelled; no score was assigned." if cancelled else "Execution infrastructure could not complete the test suite; retry later.")
            if cancellation.is_set():
                return self._finish(job.id, RunStatus.CANCELLED, "CANCELLED", "Grading was cancelled; no score was assigned.")
            return self._finish(job.id, RunStatus.COMPLETED)
        except KeyboardInterrupt:
            self.db.rollback()
            return self._finish(claimed_id, RunStatus.CANCELLED, "CANCELLED", "Grading was interrupted; no score was assigned.")
        except SQLAlchemyError:
            self.db.rollback()
            return self._finish(claimed_id, RunStatus.INCOMPLETE, "PERSISTENCE", "Grading persistence failed; no score was assigned.")
        except ConfigurationError:
            self.db.rollback()
            return self._finish(claimed_id, RunStatus.INCOMPLETE, "CONFIGURATION", "Grading inputs or worker configuration changed; enqueue a new run.")
        except Exception:
            self.db.rollback()
            return self._finish(claimed_id, RunStatus.INCOMPLETE, "INTERNAL_ERROR", "The grading worker could not finish the test suite.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Process at most one queued run")
    parser.add_argument("--run-id", type=UUID, help="Process this queued run once")
    parser.add_argument("--log-file", default="objective.execution.log")
    arguments = parser.parse_args()
    try:
        settings = ObjectiveGradingSettings()
    except ValueError:
        print(json.dumps({"status": "SYSTEM_ERROR", "error": "Invalid objective worker configuration."}))
        return 2
    handler = RotatingFileHandler(arguments.log_file, maxBytes=1024 * 1024, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    parent_logger = logging.getLogger("grading")
    parent_logger.addHandler(handler)
    parent_logger.setLevel(logging.INFO)
    parent_logger.propagate = False
    cancellation = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: cancellation.set())
    signal.signal(signal.SIGTERM, lambda *_: cancellation.set())
    from app.database import SessionLocal
    from app.execution.service import get_execution_service
    executor = get_execution_service()
    try:
        while not cancellation.is_set():
            with SessionLocal() as db:
                worker = ObjectiveGradingWorker(db, executor=executor, settings=settings)
                try:
                    run = worker.process_run(arguments.run_id, cancel_event=cancellation)
                except GradingError:
                    print(json.dumps({"status": "SYSTEM_ERROR", "error": "The grading worker requires database recovery."}))
                    return 2
                if run is not None:
                    print(json.dumps({"run_id": str(run.id), "status": run.status.value,
                        "correctness_percentage": str(run.correctness_percentage) if run.correctness_percentage is not None else None}))
                if arguments.once or arguments.run_id:
                    return 0 if run is None or run.status == RunStatus.COMPLETED else 2
            cancellation.wait(settings.worker_poll_seconds)
    finally:
        handler.close()
        parent_logger.removeHandler(handler)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
