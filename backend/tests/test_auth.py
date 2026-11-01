"""Real signed-token, password and persisted-session authentication tests."""

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import jwt
import pytest
from sqlalchemy import select

from app.auth.security import create_access_token, decode_access_token, verify_password
from app.enums import UserRole
from app.models import AuthSession


PASSWORD = "a-test-password-with-32-characters!"


def bearer(token: str) -> dict:
    return {"Authorization": "Bearer " + token}


def test_login_issues_expiring_signed_session_and_safe_profile(raw_client, db_session, users, auth_settings) -> None:
    response = raw_client.post("/auth/login", json={"email": users["instructor"].email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["token_type"] == "bearer"
    assert 0 < payload["expires_in"] <= auth_settings.access_token_minutes * 60
    claims = decode_access_token(payload["access_token"], auth_settings)
    assert claims.sub == users["instructor"].id
    assert claims.type == "access"
    assert claims.exp > claims.iat
    session = db_session.get(AuthSession, claims.jti)
    assert session.user_id == users["instructor"].id and session.revoked_at is None
    profile = raw_client.get("/auth/me", headers=bearer(payload["access_token"]))
    assert profile.status_code == 200
    assert profile.json()["id"] == str(users["instructor"].id)
    assert profile.json()["role"] == "INSTRUCTOR"
    assert "password" not in profile.text and "hash" not in profile.text
    assert users["instructor"].password_hash.startswith("$argon2id$")
    assert verify_password(PASSWORD, users["instructor"].password_hash)
    assert not verify_password(PASSWORD, PASSWORD)


def test_failed_login_is_generic_for_unknown_wrong_password_and_inactive(raw_client, users) -> None:
    responses = [raw_client.post("/auth/login", json=payload) for payload in (
        {"email": users["instructor"].email, "password": "wrong-password"},
        {"email": "unknown@example.test", "password": PASSWORD},
        {"email": users["inactive"].email, "password": PASSWORD},
    )]
    assert [response.status_code for response in responses] == [401, 401, 401]
    assert responses[0].json() == responses[1].json() == responses[2].json()


def test_plaintext_legacy_password_is_not_accepted(raw_client, db_session, users) -> None:
    users["instructor"].password_hash = PASSWORD
    db_session.commit()
    assert raw_client.post("/auth/login", json={"email": users["instructor"].email, "password": PASSWORD}).status_code == 401


@pytest.mark.parametrize("header", [None, "Bearer invalid", "Basic abc", "Bearer a.b.c"])
def test_missing_or_malformed_tokens_are_401(raw_client, header) -> None:
    headers = {} if header is None else {"Authorization": header}
    assert raw_client.get("/auth/me", headers=headers).status_code == 401


def test_expired_access_token_is_rejected(client, users, auth_settings) -> None:
    original = decode_access_token(client.headers["Authorization"].split(" ", 1)[1], auth_settings)
    expired = create_access_token(users["instructor"].id, original.jti, auth_settings, now=datetime.now(timezone.utc) - timedelta(hours=2))
    assert client.get("/auth/me", headers=bearer(expired)).status_code == 401


@pytest.mark.parametrize("change", [
    {"iss": "untrusted-issuer"}, {"aud": "different-audience"}, {"type": "refresh"},
    {"sub": "not-a-uuid"}, {"jti": "not-a-uuid"}, {"exp": None},
    {"exp": 1}, {"nbf": 4102444800}, {"iat": 4102444800},
])
def test_signed_token_with_invalid_claims_is_rejected(client, auth_settings, change: dict) -> None:
    valid = client.headers["Authorization"].split(" ", 1)[1]
    claims = jwt.decode(valid, options={"verify_signature": False})
    claims.update(change)
    malformed = jwt.encode(claims, auth_settings.secret_key.get_secret_value(), algorithm="HS256")
    assert client.get("/auth/me", headers=bearer(malformed)).status_code == 401


def test_wrong_signature_and_disallowed_algorithm_are_rejected(client, auth_settings) -> None:
    valid = client.headers["Authorization"].split(" ", 1)[1]
    claims = jwt.decode(valid, options={"verify_signature": False})
    wrong_signature = jwt.encode(claims, "different-secret-for-invalid-token-at-least-32", algorithm="HS256")
    wrong_algorithm = jwt.encode(claims, auth_settings.secret_key.get_secret_value(), algorithm="HS384")
    assert client.get("/auth/me", headers=bearer(wrong_signature)).status_code == 401
    assert client.get("/auth/me", headers=bearer(wrong_algorithm)).status_code == 401


@pytest.mark.parametrize("missing", ["user", "session", "session_owner"])
def test_token_requires_existing_user_and_matching_session(client, users, auth_settings, missing: str) -> None:
    original = decode_access_token(client.headers["Authorization"].split(" ", 1)[1], auth_settings)
    user_id = uuid4() if missing == "user" else users["student"].id if missing == "session_owner" else users["instructor"].id
    session_id = uuid4() if missing == "session" else original.jti
    token = create_access_token(user_id, session_id, auth_settings)
    assert client.get("/auth/me", headers=bearer(token)).status_code == 401


def test_logout_revokes_the_persisted_session(client, db_session, auth_settings) -> None:
    token = client.headers["Authorization"].split(" ", 1)[1]
    claims = decode_access_token(token, auth_settings)
    assert client.post("/auth/logout").status_code == 204
    db_session.refresh(db_session.get(AuthSession, claims.jti))
    assert db_session.get(AuthSession, claims.jti).revoked_at is not None
    assert client.get("/auth/me", headers=bearer(token)).status_code == 401


@pytest.mark.parametrize("change", ["inactive", "expired_session"])
def test_current_account_and_session_status_are_checked_every_request(client, db_session, users, auth_settings, change: str) -> None:
    if change == "inactive":
        users["instructor"].is_active = False
    else:
        claims = decode_access_token(client.headers["Authorization"].split(" ", 1)[1], auth_settings)
        db_session.get(AuthSession, claims.jti).expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()
    assert client.get("/auth/me").status_code == 401


def test_role_is_loaded_from_database_instead_of_stale_token(client, db_session, users, objective_records) -> None:
    users["instructor"].role = UserRole.STUDENT
    db_session.commit()
    assert client.get("/auth/me").json()["role"] == "STUDENT"
    assert client.put(f"/assignments/{objective_records.assignment.id}", json={"title": "Spoofed staff"}).status_code == 403


@pytest.mark.parametrize("spoof", [{"role": "ADMIN"}, {"user_id": str(uuid4())}])
def test_login_payload_cannot_choose_identity_or_role(raw_client, users, spoof: dict) -> None:
    response = raw_client.post("/auth/login", json={"email": users["instructor"].email, "password": PASSWORD, **spoof})
    assert response.status_code == 422
