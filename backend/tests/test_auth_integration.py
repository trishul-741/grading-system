"""Opt-in real PostgreSQL authentication/membership regression tests.

The development database must already be at Alembic head. Only fresh test-owned
rows are inserted/deleted; no create_all, migration, schema reset or Docker call.
"""

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from uuid import uuid4

from alembic.config import Config
from alembic.script import ScriptDirectory
from dotenv import dotenv_values
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, delete, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.auth.security import decode_access_token
from app.enums import AssignmentStatus, ProgrammingLanguage, UserRole
from app.models import Assignment, AssignmentTAMembership, AuthSession, User


pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    os.getenv("AUTH_INTEGRATION_TESTS") != "1",
    reason="Not run: set AUTH_INTEGRATION_TESTS=1 with migrated PostgreSQL",
)]
PASSWORD = "a-test-password-with-32-characters!"


@pytest.fixture(scope="module")
def auth_postgres_engine():
    backend = Path(__file__).resolve().parents[1]
    database_url = os.getenv("AUTH_TEST_DATABASE_URL") or dotenv_values(backend.parent / ".env").get("DATABASE_URL")
    if not database_url or make_url(database_url).get_backend_name() != "postgresql":
        pytest.fail("Authentication integration requires a configured PostgreSQL database", pytrace=False)
    engine = create_engine(database_url, pool_pre_ping=True, connect_args={"connect_timeout": 5, "options": "-c statement_timeout=15000 -c lock_timeout=10000"})
    try:
        with engine.connect() as connection:
            metadata = inspect(connection)
            assert metadata.has_table("auth_sessions"), "Run Alembic upgrade head first"
            assert metadata.has_table("assignment_ta_memberships"), "Run Alembic upgrade head first"
            assert "is_active" in {column["name"] for column in metadata.get_columns("users")}
            revisions = set(connection.execute(text("SELECT version_num FROM alembic_version")).scalars())
            assert revisions == set(ScriptDirectory.from_config(Config(str(backend / "alembic.ini"))).get_heads())
    except SQLAlchemyError:
        engine.dispose()
        pytest.fail("Configured PostgreSQL authentication database is unavailable", pytrace=False)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def real_auth(auth_postgres_engine, password_hash, auth_settings):
    from app.auth.config import get_auth_settings
    from app.database import get_db
    from app.main import app

    owner_id, ta_id, assignment_id = uuid4(), uuid4(), uuid4()
    owner_email, ta_email = f"auth-owner-{owner_id}@example.test", f"auth-ta-{ta_id}@example.test"
    with Session(auth_postgres_engine) as db:
        db.add_all([
            User(id=owner_id, name="Integration owner", email=owner_email, password_hash=password_hash, role=UserRole.INSTRUCTOR, is_active=True),
            User(id=ta_id, name="Integration TA", email=ta_email, password_hash=password_hash, role=UserRole.TA, is_active=True),
        ])
        db.flush()
        db.add(Assignment(id=assignment_id, created_by=owner_id, title="Auth integration only", description="Test-owned row", language=ProgrammingLanguage.CPP, status=AssignmentStatus.PUBLISHED))
        db.commit()

    def database():
        with Session(auth_postgres_engine) as db:
            yield db
    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_auth_settings] = lambda: auth_settings
    try:
        with TestClient(app) as client:
            yield client, owner_id, ta_id, assignment_id, owner_email, ta_email
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
        with Session(auth_postgres_engine) as db:
            db.execute(delete(AssignmentTAMembership).where(AssignmentTAMembership.assignment_id == assignment_id))
            db.execute(delete(Assignment).where(Assignment.id == assignment_id))
            db.execute(delete(AuthSession).where(AuthSession.user_id.in_([owner_id, ta_id])))
            db.execute(delete(User).where(User.id.in_([owner_id, ta_id])))
            db.commit()


def login(client, email: str) -> str:
    response = client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def test_postgres_login_session_and_logout_are_persisted(real_auth, auth_postgres_engine, auth_settings) -> None:
    client, owner_id, _, _, owner_email, _ = real_auth
    token = login(client, owner_email)
    claims = decode_access_token(token, auth_settings)
    with Session(auth_postgres_engine) as db:
        session = db.get(AuthSession, claims.jti)
        assert session.user_id == owner_id and session.revoked_at is None
    headers = {"Authorization": "Bearer " + token}
    assert client.get("/auth/me", headers=headers).status_code == 200
    assert client.post("/auth/logout", headers=headers).status_code == 204
    with Session(auth_postgres_engine) as db:
        assert db.get(AuthSession, claims.jti).revoked_at is not None
    assert client.get("/auth/me", headers=headers).status_code == 401


def test_postgres_membership_grants_and_revokes_ta_access(real_auth, auth_postgres_engine) -> None:
    client, owner_id, ta_id, assignment_id, owner_email, ta_email = real_auth
    owner_headers = {"Authorization": "Bearer " + login(client, owner_email)}
    ta_headers = {"Authorization": "Bearer " + login(client, ta_email)}
    base = f"/assignments/{assignment_id}"
    assert client.get(base, headers=ta_headers).status_code == 404
    assert client.put(base + "/tas/" + str(ta_id), headers=owner_headers).status_code in {200, 201, 204}
    with Session(auth_postgres_engine) as db:
        membership = db.get(AssignmentTAMembership, (assignment_id, ta_id))
        assert membership.granted_by == owner_id
    assert client.get(base, headers=ta_headers).status_code == 200
    assert client.delete(base + "/tas/" + str(ta_id), headers=owner_headers).status_code == 204
    assert client.get(base, headers=ta_headers).status_code == 404


def test_postgres_account_deactivation_invalidates_existing_access(real_auth, auth_postgres_engine) -> None:
    client, owner_id, _, _, owner_email, _ = real_auth
    token = login(client, owner_email)
    with Session(auth_postgres_engine) as db:
        db.get(User, owner_id).is_active = False
        db.commit()
    assert client.get("/auth/me", headers={"Authorization": "Bearer " + token}).status_code == 401
