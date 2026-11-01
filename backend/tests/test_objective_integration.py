"""Opt-in PostgreSQL migrations/concurrency tests and real Docker grading.

The configured development database must already be migrated. Only rows with
fresh test-owned UUIDs are inserted/deleted; these tests never create_all,
truncate tables, replace schemas, or alter existing assignment data.
"""

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import os
from pathlib import Path
import threading
from uuid import UUID, uuid4

from alembic.config import Config
from alembic.script import ScriptDirectory
from dotenv import dotenv_values
import pytest
from sqlalchemy import create_engine, delete, inspect, select, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.enums import (
    AssignmentStatus, ProgrammingLanguage, TestCategory as CaseCategory,
    TestSource as CaseSource, TestStatus as CaseStatus,
    TestVisibility as CaseVisibility, UserRole,
)
from app.execution.config import ExecutionSettings
from app.execution.models import ExecutionResult, ExecutionStatus


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("OBJECTIVE_GRADING_INTEGRATION_TESTS") != "1",
        reason="Not run: set OBJECTIVE_GRADING_INTEGRATION_TESTS=1 with migrated PostgreSQL",
    ),
]

real_docker = pytest.mark.skipif(
    os.getenv("EXECUTION_DOCKER_TESTS") != "1",
    reason="Not run: real objective execution additionally requires EXECUTION_DOCKER_TESTS=1",
)


@dataclass(frozen=True)
class Dataset:
    engine: Engine
    instructor_id: UUID
    student_id: UUID
    assignment_id: UUID
    submission_id: UUID
    approved_ids: tuple[UUID, UUID]


@pytest.fixture(scope="module")
def postgres_engine() -> Iterator[Engine]:
    # conftest deliberately sets DATABASE_URL to SQLite for unit tests. Read
    # an explicit integration URL or the original project .env, never that
    # overridden variable. Credentials are never included in assertion text.
    backend = Path(__file__).resolve().parents[1]
    database_url = os.getenv("OBJECTIVE_GRADING_TEST_DATABASE_URL") or dotenv_values(
        backend.parent / ".env",
    ).get("DATABASE_URL")
    if not database_url:
        pytest.fail("A configured PostgreSQL integration database is required", pytrace=False)
    if make_url(database_url).get_backend_name() != "postgresql":
        pytest.fail("Objective integration requires PostgreSQL, never SQLite", pytrace=False)
    engine = create_engine(
        database_url, pool_pre_ping=True,
        connect_args={
            "connect_timeout": 5,
            "options": "-c statement_timeout=15000 -c lock_timeout=10000",
        },
    )
    try:
        with engine.connect() as connection:
            inspector = inspect(connection)
            assert inspector.has_table("objective_grading_runs"), "Run Alembic upgrade head first"
            assert inspector.has_table("objective_test_results"), "Run Alembic upgrade head first"
            revisions = set(connection.execute(text("SELECT version_num FROM alembic_version")).scalars())
            expected = set(ScriptDirectory.from_config(Config(str(backend / "alembic.ini"))).get_heads())
            assert revisions == expected, "Integration database must be at the migration head"
            indexes = inspector.get_indexes("objective_grading_runs")
            assert any(
                index["name"] == "uq_objective_active_submission" and index["unique"]
                for index in indexes
            ), "The actual PostgreSQL active-run unique index must exist"
    except SQLAlchemyError:
        engine.dispose()
        pytest.fail("Configured PostgreSQL integration database is unavailable", pytrace=False)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def dataset(postgres_engine: Engine) -> Iterator[Dataset]:
    from app.models import Assignment, Submission, TestCase, User
    from app.models.objective_grading import ObjectiveGradingRun, ObjectiveTestResult

    instructor_id, student_id, assignment_id, submission_id = (uuid4() for _ in range(4))
    approved_ids = (uuid4(), uuid4())
    marker = uuid4().hex
    data = Dataset(
        postgres_engine, instructor_id, student_id, assignment_id, submission_id, approved_ids,
    )
    try:
        with Session(postgres_engine) as db:
            db.add_all([
                User(id=instructor_id, name="Objective Integration Instructor",
                     email=f"instructor-{marker}@objective.example.test",
                     password_hash="integration-test-only", role=UserRole.INSTRUCTOR),
                User(id=student_id, name="Objective Integration Student",
                     email=f"student-{marker}@objective.example.test",
                     password_hash="integration-test-only", role=UserRole.STUDENT),
            ])
            db.flush()
            db.add(Assignment(
                id=assignment_id, created_by=instructor_id, title=f"Objective integration {marker}",
                description="Test-owned rows; safe to remove by their UUIDs only.",
                language=ProgrammingLanguage.CPP, status=AssignmentStatus.PUBLISHED,
            ))
            db.flush()
            db.add(Submission(
                id=submission_id, assignment_id=assignment_id, student_id=student_id,
                submission_number=1, source_code=(
                    "#include <iostream>\nint main(){int a,b;std::cin>>a>>b;std::cout<<a+b;}"
                ), language=ProgrammingLanguage.CPP, status="SUBMITTED",
            ))
            created = datetime.now(timezone.utc)
            cases = [
                (approved_ids[0], "1 2\n", "3\n", CaseVisibility.PUBLIC, CaseStatus.APPROVED),
                (approved_ids[1], "10 20\n", "30\n", CaseVisibility.HIDDEN, CaseStatus.APPROVED),
                (uuid4(), "0 0\n", "incorrect draft", CaseVisibility.PUBLIC, CaseStatus.DRAFT),
                (uuid4(), "0 0\n", "incorrect validated", CaseVisibility.PUBLIC, CaseStatus.VALIDATED),
                (uuid4(), "0 0\n", "incorrect rejected", CaseVisibility.HIDDEN, CaseStatus.REJECTED),
            ]
            for position, (case_id, stdin, expected, visibility, status) in enumerate(cases):
                db.add(TestCase(
                    id=case_id, assignment_id=assignment_id, input=stdin, expected_output=expected,
                    visibility=visibility, source=CaseSource.INSTRUCTOR,
                    category=CaseCategory.NORMAL, status=status,
                    created_at=created + timedelta(microseconds=position),
                ))
            db.commit()
        yield data
    finally:
        # Explicitly delete only our UUIDs, including dependent evidence. No
        # production session, test schema, or unrelated seed row is reused.
        with Session(postgres_engine) as db:
            runs = select(ObjectiveGradingRun.id).where(
                ObjectiveGradingRun.submission_id == submission_id,
            )
            db.execute(delete(ObjectiveTestResult).where(ObjectiveTestResult.grading_run_id.in_(runs)))
            db.execute(delete(ObjectiveGradingRun).where(ObjectiveGradingRun.submission_id == submission_id))
            db.execute(delete(TestCase).where(TestCase.assignment_id == assignment_id))
            db.execute(delete(Submission).where(Submission.id == submission_id))
            db.execute(delete(Assignment).where(Assignment.id == assignment_id))
            db.execute(delete(User).where(User.id.in_([instructor_id, student_id])))
            db.commit()


def enqueue(data: Dataset) -> UUID:
    from app.services.objective_grading_service import enqueue_objective_grading
    from app.models import User

    with Session(data.engine) as db:
        run = enqueue_objective_grading(db, data.assignment_id, data.submission_id, actor=db.get(User, data.instructor_id))
        return run.id


def load_run(db: Session, data: Dataset, run_id: UUID):
    from app.services.objective_grading_service import get_objective_grading_run
    from app.models import User

    return get_objective_grading_run(db, data.assignment_id, data.submission_id, run_id, actor=db.get(User, data.instructor_id))


class RecordingExecutor:
    """Trusted deterministic fake: DB-only cases make no sandbox claims."""

    settings = ExecutionSettings()

    def __init__(self, started=None, release=None) -> None:
        self.requests = []
        self.started = started
        self.release = release

    def execute(self, request, *, cancel_event=None) -> ExecutionResult:
        self.requests.append(request)
        if self.started is not None:
            self.started.set()
            assert self.release.wait(15), "Concurrent worker was not released"
        output = str(sum(int(value) for value in request.input_data.split()))
        return ExecutionResult(
            status=ExecutionStatus.SUCCESS, stdout=output, exit_code=0,
            execution_time_ms=1, compilation_time_ms=1, job_id=uuid4(),
        )


def test_postgres_concurrent_enqueue_preserves_one_active_run(dataset) -> None:
    from app.grading.errors import GradingError
    from app.grading.enums import RunStatus
    from app.models.objective_grading import ObjectiveGradingRun
    from app.models import User
    from app.services.objective_grading_service import enqueue_objective_grading

    barrier = threading.Barrier(2, timeout=10)

    def queue_once():
        with Session(dataset.engine) as db:
            barrier.wait()
            try:
                return "accepted", enqueue_objective_grading(
                    db, dataset.assignment_id, dataset.submission_id,
                    actor=db.get(User, dataset.instructor_id),
                ).id
            except GradingError as error:
                db.rollback()
                return "rejected", error.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(queue_once) for _ in range(2)]
        responses = [future.result(timeout=25) for future in futures]
    assert sorted(response[0] for response in responses) == ["accepted", "rejected"]
    assert next(value for outcome, value in responses if outcome == "rejected") == 409
    with Session(dataset.engine) as db:
        active = db.scalars(select(ObjectiveGradingRun).where(
            ObjectiveGradingRun.submission_id == dataset.submission_id,
            ObjectiveGradingRun.status.in_([RunStatus.QUEUED, RunStatus.RUNNING]),
        )).all()
        assert len(active) == 1


@pytest.mark.parametrize("active_status", ["QUEUED", "RUNNING"])
def test_postgres_unique_index_blocks_direct_overlapping_run(dataset, active_status) -> None:
    from app.grading.enums import RunStatus
    from app.models.objective_grading import ObjectiveGradingRun

    run_id = enqueue(dataset)
    with Session(dataset.engine) as db:
        original = db.get(ObjectiveGradingRun, run_id)
        original.status = RunStatus(active_status)
        db.commit()
        duplicate = ObjectiveGradingRun(
            assignment_id=original.assignment_id, submission_id=original.submission_id,
            status=RunStatus.QUEUED, total_tests=original.total_tests,
            passed_tests=0, failed_tests=0, correctness_percentage=None,
            engine_version=original.engine_version,
            comparison_policy_version=original.comparison_policy_version,
            execution_engine_version=original.execution_engine_version,
            source_sha256=original.source_sha256, language=original.language,
            previous_submission_status="SUBMITTED", execution_settings=original.execution_settings,
        )
        db.add(duplicate)
        # This bypasses the service's row lock intentionally: the actual database
        # invariant must reject overlap for QUEUED and RUNNING independently.
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()
        assert db.get(ObjectiveGradingRun, run_id).status == RunStatus(active_status)


def test_postgres_workers_claim_a_run_only_once(dataset) -> None:
    from app.grading.enums import RunStatus
    from app.grading.worker import ObjectiveGradingWorker

    run_id = enqueue(dataset)
    started, release = threading.Event(), threading.Event()
    first_executor = RecordingExecutor(started, release)
    second_executor = RecordingExecutor()

    def first_worker():
        with Session(dataset.engine) as db:
            result = ObjectiveGradingWorker(db, executor=first_executor).process_run(run_id)
            return result.id if result is not None else None

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(first_worker)
        try:
            assert started.wait(15), "First worker never reached execution"
            with Session(dataset.engine) as db:
                duplicate = ObjectiveGradingWorker(db, executor=second_executor).process_run(run_id)
                assert duplicate is None
        finally:
            release.set()
        assert future.result(timeout=25) == run_id
    assert len(first_executor.requests) == 2
    assert not second_executor.requests
    with Session(dataset.engine) as db:
        run = load_run(db, dataset, run_id)
        assert run.status == RunStatus.COMPLETED
        assert run.correctness_percentage == Decimal("100.000")


def test_postgres_changed_source_is_incomplete_without_execution(dataset) -> None:
    from app.grading.enums import RunStatus
    from app.grading.worker import ObjectiveGradingWorker
    from app.models import Submission

    run_id = enqueue(dataset)
    with Session(dataset.engine) as db:
        db.get(Submission, dataset.submission_id).source_code = "int main(){return 7;}"
        db.commit()
    recorder = RecordingExecutor()
    with Session(dataset.engine) as db:
        ObjectiveGradingWorker(db, executor=recorder).process_run(run_id)
    assert not recorder.requests
    with Session(dataset.engine) as db:
        run = load_run(db, dataset, run_id)
        assert run.status == RunStatus.INCOMPLETE
        assert run.correctness_percentage is None
        assert run.failed_tests == 0


@pytest.mark.docker
@real_docker
def test_real_docker_grading_persists_only_approved_tests(dataset) -> None:
    from app.grading.enums import RunStatus, TestOutcome
    from app.grading.worker import ObjectiveGradingWorker
    from app.models import Submission

    run_id = enqueue(dataset)
    with Session(dataset.engine) as db:
        assert db.get(Submission, dataset.submission_id).status == "QUEUED"
        assert ObjectiveGradingWorker(db).process_run(run_id).id == run_id
    with Session(dataset.engine) as db:
        run = load_run(db, dataset, run_id)
        assert run.status == RunStatus.COMPLETED
        assert run.total_tests == run.passed_tests == 2
        assert run.failed_tests == 0
        assert run.correctness_percentage == Decimal("100.000")
        assert [result.test_case_id_snapshot for result in run.results] == list(dataset.approved_ids)
        assert all(result.outcome == TestOutcome.PASSED for result in run.results)
        assert all(result.execution_status == ExecutionStatus.SUCCESS.value for result in run.results)
        assert db.get(Submission, dataset.submission_id).status == "SUBMITTED"


@pytest.mark.docker
@real_docker
def test_real_docker_uses_approved_snapshots_after_edit_and_delete(dataset) -> None:
    from app.grading.enums import RunStatus
    from app.grading.worker import ObjectiveGradingWorker
    from app.models import TestCase

    run_id = enqueue(dataset)
    with Session(dataset.engine) as db:
        public = db.get(TestCase, dataset.approved_ids[0])
        public.input, public.expected_output = "500 500\n", "deliberately changed"
        hidden = db.get(TestCase, dataset.approved_ids[1])
        db.delete(hidden)
        db.commit()
    with Session(dataset.engine) as db:
        ObjectiveGradingWorker(db).process_run(run_id)
    with Session(dataset.engine) as db:
        run = load_run(db, dataset, run_id)
        assert run.status == RunStatus.COMPLETED
        assert run.correctness_percentage == Decimal("100.000")
        assert [result.input_data for result in run.results] == ["1 2\n", "10 20\n"]
        assert [result.expected_output for result in run.results] == ["3\n", "30\n"]
        assert run.results[1].test_case_id is None
        assert run.results[1].visibility == CaseVisibility.HIDDEN


@pytest.mark.docker
@real_docker
def test_real_docker_compile_error_produces_zero_objective_score(dataset) -> None:
    from app.grading.enums import RunStatus, TestOutcome
    from app.grading.worker import ObjectiveGradingWorker
    from app.models import Submission

    with Session(dataset.engine) as db:
        db.get(Submission, dataset.submission_id).source_code = "int main(){undefined_identifier;}"
        db.commit()
    run_id = enqueue(dataset)
    with Session(dataset.engine) as db:
        ObjectiveGradingWorker(db).process_run(run_id)
    with Session(dataset.engine) as db:
        run = load_run(db, dataset, run_id)
        assert run.status == RunStatus.COMPLETED
        assert run.passed_tests == 0
        assert run.failed_tests == run.total_tests == 2
        assert run.correctness_percentage == Decimal("0.000")
        assert all(result.outcome == TestOutcome.COMPILE_ERROR for result in run.results)
