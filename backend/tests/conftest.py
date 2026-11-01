"""Database regression fixtures never connect to a configured PostgreSQL server."""

import os
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool


# Set this before importing app.database, whose engine is created at import time.
os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"

INSTRUCTOR_ID = UUID("00000000-0000-0000-0000-000000000001")
STUDENT_ID = UUID("00000000-0000-0000-0000-000000000002")
OTHER_STUDENT_ID = UUID("00000000-0000-0000-0000-000000000003")
OTHER_INSTRUCTOR_ID = UUID("00000000-0000-0000-0000-000000000004")
ADMIN_ID = UUID("00000000-0000-0000-0000-000000000005")
TA_ID = UUID("00000000-0000-0000-0000-000000000006")
OTHER_TA_ID = UUID("00000000-0000-0000-0000-000000000007")
INACTIVE_ID = UUID("00000000-0000-0000-0000-000000000008")
TEST_PASSWORD = "a-test-password-with-32-characters!"
AUTH_SECRET = "test-only-auth-signing-secret-with-64-characters-do-not-deploy-88552"


@compiles(PostgreSQLUUID, "sqlite")
def compile_sqlite_uuid(_type, _compiler, **_kwargs) -> str:
    # SQLite assigns NUMERIC affinity to the name UUID, which corrupts the
    # all-digit demo IDs (000...001 becomes integer 1). Preserve text storage
    # while retaining SQLAlchemy's real UUID bind/result conversion.
    return "CHAR(32)"


@pytest.fixture(scope="session")
def password_hash() -> str:
    from app.auth.security import hash_password
    return hash_password(TEST_PASSWORD)


@pytest.fixture
def auth_settings():
    from app.auth.config import AuthSettings
    return AuthSettings(secret_key=AUTH_SECRET)


@pytest.fixture
def db_session(password_hash: str) -> Iterator[Session]:
    from app.database import Base
    from app.enums import UserRole
    from app.models import User

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record) -> None:
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(
            [
                User(
                    id=INSTRUCTOR_ID,
                    name="Demo Instructor",
                    email="instructor@example.test",
                    password_hash=password_hash,
                    role=UserRole.INSTRUCTOR,
                ),
                User(
                    id=STUDENT_ID,
                    name="Demo Student",
                    email="student@example.test",
                    password_hash=password_hash,
                    role=UserRole.STUDENT,
                ),
                User(
                    id=OTHER_STUDENT_ID,
                    name="Other Student",
                    email="other@example.test",
                    password_hash=password_hash,
                    role=UserRole.STUDENT,
                ),
                User(id=OTHER_INSTRUCTOR_ID, name="Other Instructor", email="other-instructor@example.test", password_hash=password_hash, role=UserRole.INSTRUCTOR),
                User(id=ADMIN_ID, name="Administrator", email="admin@example.test", password_hash=password_hash, role=UserRole.ADMIN),
                User(id=TA_ID, name="Assigned TA", email="ta@example.test", password_hash=password_hash, role=UserRole.TA),
                User(id=OTHER_TA_ID, name="Unassigned TA", email="other-ta@example.test", password_hash=password_hash, role=UserRole.TA),
                User(id=INACTIVE_ID, name="Inactive User", email="inactive@example.test", password_hash=password_hash, role=UserRole.STUDENT, is_active=False),
            ]
        )
        session.commit()
        yield session
    engine.dispose()


@pytest.fixture
def users(db_session: Session) -> dict:
    from app.models import User
    return {name: db_session.get(User, identity) for name, identity in {
        "instructor": INSTRUCTOR_ID, "other_instructor": OTHER_INSTRUCTOR_ID,
        "student": STUDENT_ID, "other_student": OTHER_STUDENT_ID,
        "admin": ADMIN_ID, "ta": TA_ID, "other_ta": OTHER_TA_ID, "inactive": INACTIVE_ID,
    }.items()}


@pytest.fixture
def raw_client(db_session: Session, auth_settings) -> Iterator[TestClient]:
    from app.database import get_db
    from app.auth.config import get_auth_settings
    from app.main import app

    def override_db() -> Iterator[Session]:
        yield db_session

    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_auth_settings] = lambda: auth_settings
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


@pytest.fixture
def client(raw_client: TestClient, db_session: Session, auth_settings) -> TestClient:
    from app.auth.service import issue_session
    from app.models import User
    # Fixture issuance uses the real signed-token/session service. Login/password
    # behavior has dedicated HTTP tests; protected requests still verify every
    # JWT and persisted session without overriding the authenticated actor.
    response = issue_session(db_session, db_session.get(User, INSTRUCTOR_ID), auth_settings)
    raw_client.headers["Authorization"] = "Bearer " + response.access_token
    return raw_client


@pytest.fixture
def token_for(client: TestClient, db_session: Session, users: dict, auth_settings):
    from app.auth.service import issue_session
    from app.models import User
    tokens = {INSTRUCTOR_ID: client.headers["Authorization"].split(" ", 1)[1]}

    def issue(user):
        if isinstance(user, str) and user in users:
            user = users[user]
        if not isinstance(user, User):
            user = db_session.get(User, user)
        if user.id not in tokens:
            tokens[user.id] = issue_session(db_session, user, auth_settings).access_token
        return tokens[user.id]
    return issue


@pytest.fixture
def objective_records(db_session: Session) -> SimpleNamespace:
    """Persist a published C++ submission with a caller-controlled test suite."""
    from app.enums import AssignmentStatus, ProgrammingLanguage, TestStatus, TestVisibility
    from app.models import Assignment, Submission, TestCase

    assignment = Assignment(
        created_by=INSTRUCTOR_ID,
        title="Objective grading fixture",
        description="Print the requested value",
        language=ProgrammingLanguage.CPP,
        status=AssignmentStatus.PUBLISHED,
    )
    db_session.add(assignment)
    db_session.flush()
    submission = Submission(
        assignment_id=assignment.id,
        student_id=STUDENT_ID,
        submission_number=1,
        source_code="int main() { /* objective-private-source-298345 */ }",
        language=ProgrammingLanguage.CPP,
        status="SUBMITTED",
    )
    db_session.add(submission)
    db_session.commit()

    test_sequence = 0

    def add_test(input_data: str, expected_output: str, **overrides):
        nonlocal test_sequence
        test_sequence += 1
        case = TestCase(**{
            "assignment_id": assignment.id,
            "input": input_data,
            "expected_output": expected_output,
            "status": TestStatus.APPROVED,
            "visibility": TestVisibility.PUBLIC,
            "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=test_sequence),
            **overrides,
        })
        db_session.add(case)
        db_session.commit()
        return case

    return SimpleNamespace(assignment=assignment, submission=submission, add_test=add_test)
