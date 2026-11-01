"""Queue/read operations only. No Docker execution or background API tasks."""

from datetime import datetime, timezone
import hashlib
import logging
from uuid import UUID
from fastapi import HTTPException

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from app.enums import AssignmentStatus, TestStatus
from app.execution.config import ExecutionSettings
from app.execution.languages import get_language_config, UnsupportedLanguageError
from app.grading.comparison import COMPARISON_POLICY_VERSION
from app.grading.config import ObjectiveGradingSettings
from app.grading.enums import RunStatus, TestOutcome
from app.grading.errors import GradingError
from app.models.assignment import Assignment
from app.models.submission import Submission
from app.models.test_case import TestCase
from app.models.objective_grading import ObjectiveGradingRun, ObjectiveTestResult
from app.models.user import User
from app.auth.authorization import (
    authenticated_actor, is_student, require_assignment_read, require_assignment_staff,
)
from app.schemas.objective_grading import ObjectiveGradingRunResponse


GRADING_ENGINE_VERSION = "objective-v1"
EXECUTION_ENGINE_VERSION = "docker-cpp-v1"
logger = logging.getLogger("grading.objective")
logger.addHandler(logging.NullHandler())


def _submission(db: Session, assignment_id: UUID, submission_id: UUID,
                *, actor: User, lock: bool = False) -> tuple[Assignment, Submission]:
    actor = _grading_actor(db, actor)
    try:
        assignment = (require_assignment_staff(db, assignment_id, actor) if lock
                      else require_assignment_read(db, assignment_id, actor))
    except HTTPException as exc:
        raise GradingError(exc.status_code, str(exc.detail)) from None
    query = select(Submission).where(Submission.id == submission_id, Submission.assignment_id == assignment_id)
    if is_student(actor):
        query = query.where(Submission.student_id == actor.id)
    if lock:
        query = query.with_for_update()
    submission = db.scalar(query.execution_options(populate_existing=True))
    if submission is None:
        raise GradingError(404, "Submission not found")
    return assignment, submission


def _grading_actor(db: Session, actor: User) -> User:
    try:
        return authenticated_actor(db, actor)
    except HTTPException as exc:
        raise GradingError(exc.status_code, str(exc.detail)) from None


def _bytes(value: object, limit: int, label: str) -> bytes:
    if not isinstance(value, str) or len(value) > limit or "\x00" in value:
        raise GradingError(422, f"{label} exceeds the configured byte limit or is invalid")
    try:
        encoded = value.encode("utf-8")
    except UnicodeError as exc:
        raise GradingError(422, f"{label} must be valid UTF-8") from exc
    if len(encoded) > limit:
        raise GradingError(422, f"{label} exceeds the configured byte limit")
    return encoded


def enqueue_objective_grading(db: Session, assignment_id: UUID, submission_id: UUID,
                              *, actor: User, settings: ObjectiveGradingSettings | None = None) -> ObjectiveGradingRun:
    settings = settings or ObjectiveGradingSettings()
    execution_settings = ExecutionSettings()
    try:
        assignment, submission = _submission(db, assignment_id, submission_id, actor=actor, lock=True)
        if assignment.status not in {AssignmentStatus.PUBLISHED, AssignmentStatus.CLOSED}:
            raise GradingError(409, "Only published or closed assignments can be graded")
        if submission.language != assignment.language:
            raise GradingError(422, "Submission language does not match assignment language")
        try:
            get_language_config(submission.language)
        except UnsupportedLanguageError as exc:
            raise GradingError(422, "Submission language is not supported by the execution worker") from exc
        if submission.status not in {"SUBMITTED", "FAILED"}:
            raise GradingError(409, "Submission already has pending grading or cannot be regraded in its current state")
        active = db.scalar(select(ObjectiveGradingRun.id).where(
            ObjectiveGradingRun.submission_id == submission_id,
            ObjectiveGradingRun.status.in_([RunStatus.QUEUED, RunStatus.RUNNING]),
        ))
        if active is not None:
            raise GradingError(409, "An objective grading run is already active for this submission")
        source = _bytes(submission.source_code, execution_settings.source_limit_bytes, "Source code")
        if not source:
            raise GradingError(422, "Submission source code is empty")
        tests = list(db.scalars(select(TestCase).where(
            TestCase.assignment_id == assignment_id, TestCase.status == TestStatus.APPROVED,
        ).order_by(TestCase.created_at.asc(), TestCase.id.asc()).limit(settings.max_tests + 1)))
        if len(tests) > settings.max_tests:
            raise GradingError(422, "Assignment exceeds the configured approved-test count")
        snapshot_bytes = 0
        for test in tests:
            snapshot_bytes += len(_bytes(test.input, execution_settings.input_limit_bytes, "Approved test input"))
            snapshot_bytes += len(_bytes(test.expected_output, execution_settings.stdout_limit_bytes, "Approved expected output"))
        if snapshot_bytes > settings.max_snapshot_bytes:
            raise GradingError(422, "Approved test snapshots exceed the configured total byte limit")
        now = datetime.now(timezone.utc)
        run = ObjectiveGradingRun(
            assignment_id=assignment_id, submission_id=submission_id,
            status=RunStatus.QUEUED if tests else RunStatus.NOT_GRADEABLE,
            total_tests=len(tests), passed_tests=0, failed_tests=0,
            correctness_percentage=None, queued_at=now,
            completed_at=None if tests else now,
            error_category=None if tests else "TEST_CONFIGURATION",
            error_message=None if tests else "No approved test cases exist for this assignment.",
            engine_version=GRADING_ENGINE_VERSION, comparison_policy_version=COMPARISON_POLICY_VERSION,
            execution_engine_version=EXECUTION_ENGINE_VERSION,
            source_sha256=hashlib.sha256(source).hexdigest(), language=submission.language.value,
            previous_submission_status=submission.status,
            execution_settings=execution_settings.model_dump(mode="json"),
        )
        run.results = [ObjectiveTestResult(
            test_case_id=test.id, test_case_id_snapshot=test.id, position=position,
            visibility=test.visibility, input_data=test.input, expected_output=test.expected_output,
            outcome=TestOutcome.PENDING,
        ) for position, test in enumerate(tests, start=1)]
        db.add(run)
        if tests:
            submission.status = "QUEUED"
        db.commit()
        db.refresh(run)
        # Eager load while in the controlled database error boundary.
        list(run.results)
        return run
    except GradingError:
        db.rollback()
        raise
    except IntegrityError as exc:
        db.rollback()
        raise GradingError(409, "A grading request conflicted with another database operation; retry later") from exc
    except SQLAlchemyError as exc:
        db.rollback()
        raise GradingError(503, "Grading persistence is unavailable; retry later") from exc


def get_objective_grading_run(db: Session, assignment_id: UUID, submission_id: UUID,
                              run_id: UUID, *, actor: User) -> ObjectiveGradingRun | ObjectiveGradingRunResponse:
    try:
        actor = _grading_actor(db, actor)
        _submission(db, assignment_id, submission_id, actor=actor)
        query = select(ObjectiveGradingRun).where(
            ObjectiveGradingRun.id == run_id,
            ObjectiveGradingRun.assignment_id == assignment_id,
            ObjectiveGradingRun.submission_id == submission_id,
        )
        if not is_student(actor):
            query = query.options(selectinload(ObjectiveGradingRun.results))
        run = db.scalar(query)
        if run is None:
            raise GradingError(404, "Objective grading run not found")
        return _run_view(run, actor)
    except SQLAlchemyError as exc:
        db.rollback()
        raise GradingError(503, "Grading persistence is unavailable; retry later") from exc


def get_latest_objective_grading_run(db: Session, assignment_id: UUID,
                                     submission_id: UUID, *, actor: User) -> ObjectiveGradingRun | ObjectiveGradingRunResponse:
    try:
        actor = _grading_actor(db, actor)
        _submission(db, assignment_id, submission_id, actor=actor)
        query = select(ObjectiveGradingRun).where(
            ObjectiveGradingRun.assignment_id == assignment_id,
            ObjectiveGradingRun.submission_id == submission_id,
        ).order_by(ObjectiveGradingRun.queued_at.desc(), ObjectiveGradingRun.id.desc()).limit(1)
        if not is_student(actor):
            query = query.options(selectinload(ObjectiveGradingRun.results))
        run = db.scalar(query)
        if run is None:
            raise GradingError(404, "Objective grading run not found")
        return _run_view(run, actor)
    except SQLAlchemyError as exc:
        db.rollback()
        raise GradingError(503, "Grading persistence is unavailable; retry later") from exc


def _run_view(run: ObjectiveGradingRun, actor: User) -> ObjectiveGradingRun | ObjectiveGradingRunResponse:
    if not is_student(actor):
        return run
    # Construct a summary without accessing the ORM evidence relationship.
    # There is no detailed student feedback policy yet, so default to no cases.
    fields = {name: getattr(run, name) for name in (
        "id", "assignment_id", "submission_id", "status", "total_tests", "passed_tests", "failed_tests",
        "correctness_percentage", "queued_at", "started_at", "completed_at", "engine_version",
        "comparison_policy_version", "execution_engine_version", "language",
    )}
    return ObjectiveGradingRunResponse(**fields, results=[], error_category=run.error_category,
        error_message="This grading attempt is unavailable or incomplete; contact assignment staff." if run.error_message else None)
