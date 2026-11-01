"""Authenticated development API tests; HTTP only queues jobs, never executes."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import SecretStr, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.enums import ProgrammingLanguage, TestVisibility as Visibility
from app.execution.models import ExecutionResult, ExecutionStatus
from app.execution.service import ExecutionService
from app.grading.config import ObjectiveGradingSettings, get_objective_grading_settings
from app.grading.worker import ObjectiveGradingWorker
from app.models import Assignment, ObjectiveGradingRun, Submission


TOKEN = "test-objective-grading-token-at-least-32-characters"


@pytest.fixture
def enabled_api(client):
    from app.main import app
    settings = ObjectiveGradingSettings(api_enabled=True, api_token=SecretStr(TOKEN))
    app.dependency_overrides[get_objective_grading_settings] = lambda: settings
    yield client


def prefix(records) -> str:
    return f"/assignments/{records.assignment.id}/submissions/{records.submission.id}"


def test_objective_api_is_disabled_by_default(client, objective_records) -> None:
    from app.main import app
    app.dependency_overrides[get_objective_grading_settings] = lambda: ObjectiveGradingSettings(api_enabled=False)
    response = client.post(prefix(objective_records) + "/grade/objective", json={})
    assert response.status_code == 404


@pytest.mark.parametrize("token", [None, "wrong-token", "Basic not-bearer"])
def test_enabled_api_requires_its_explicit_bearer_credential(enabled_api, objective_records, token) -> None:
    enabled_api.headers.pop("Authorization", None)
    headers = {} if token is None else {"Authorization": token if token.startswith("Basic") else "Bearer " + token}
    response = enabled_api.post(prefix(objective_records) + "/grade/objective", json={}, headers=headers)
    assert response.status_code == 401


@pytest.mark.parametrize(
    "kwargs",
    [
        {"api_enabled": True, "api_token": TOKEN, "environment": "production"},
    ],
)
def test_api_cannot_enable_with_unsafe_configuration(kwargs: dict) -> None:
    with pytest.raises(ValidationError):
        ObjectiveGradingSettings(**kwargs)


def test_trigger_only_queues_and_existing_reads_retrieve_persisted_run(enabled_api, db_session, objective_records, monkeypatch) -> None:
    objective_records.add_test("public input", "public expected")
    def forbidden_execution(*_args, **_kwargs):
        raise AssertionError("An API request must not execute student code")
    monkeypatch.setattr(ExecutionService, "execute", forbidden_execution)
    base = prefix(objective_records)
    response = enabled_api.post(base + "/grade/objective", json={})
    assert response.status_code == 202, response.text
    queued = response.json()
    assert queued["status"] == "QUEUED"
    assert queued["correctness_percentage"] is None
    assert queued["total_tests"] == 1
    assert queued["results"][0]["outcome"] == "PENDING"
    assert queued["results"][0]["input_data"] == "public input"
    assert db_session.get(ObjectiveGradingRun, uuid4()) is None
    assert len(db_session.scalars(select(ObjectiveGradingRun)).all()) == 1
    assert enabled_api.get(base + "/grading-runs/" + queued["id"]).json() == queued
    assert enabled_api.get(base + "/grading-runs/latest").json() == queued
    assert db_session.get(Submission, objective_records.submission.id).status == "QUEUED"


def test_hidden_tests_are_graded_but_evidence_is_always_redacted(enabled_api, db_session, objective_records) -> None:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    objective_records.add_test("visible-input", "visible-output", created_at=start)
    hidden = objective_records.add_test(
        "hidden-input-SECRET", "hidden-expected-SECRET",
        visibility=Visibility.HIDDEN, created_at=start + timedelta(seconds=1),
    )
    base = prefix(objective_records)
    queued_response = enabled_api.post(base + "/grade/objective", json={})
    assert queued_response.status_code == 202
    queued = queued_response.json()
    hidden_response = next(item for item in queued["results"] if item["visibility"] == "HIDDEN")
    for field in ("input_data", "expected_output", "actual_output", "stderr", "error_message"):
        assert hidden_response[field] is None
    assert hidden_response["details_redacted"] is True
    assert "actual_output_sha256" not in hidden_response
    assert "hidden-input-SECRET" not in queued_response.text
    assert "hidden-expected-SECRET" not in queued_response.text

    class Executor:
        def __init__(self):
            self.calls = []
        def execute(self, request, *, cancel_event=None):
            self.calls.append(request)
            return ExecutionResult(
                status=ExecutionStatus.SUCCESS, exit_code=0,
                stdout="visible-output" if request.input_data == "visible-input" else "hidden-expected-SECRET",
                stderr="hidden-stderr-SECRET" if request.input_data != "visible-input" else "visible note",
                job_id=uuid4(),
            )
    executor = Executor()
    ObjectiveGradingWorker(db_session, executor=executor).process_run(
        db_session.scalars(select(ObjectiveGradingRun.id)).one()
    )
    completed = enabled_api.get(base + "/grading-runs/" + queued["id"])
    assert completed.status_code == 200
    body = completed.json()
    assert body["status"] == "COMPLETED" and body["correctness_percentage"] == 100
    assert len(executor.calls) == 2
    assert {item.input_data for item in executor.calls} == {"visible-input", "hidden-input-SECRET"}
    hidden_response = next(item for item in body["results"] if item["visibility"] == "HIDDEN")
    assert hidden_response["outcome"] == "PASSED"
    assert hidden_response["details_redacted"] is True
    assert "actual_output_sha256" not in hidden_response
    assert all(hidden_response[field] is None for field in ("input_data", "expected_output", "actual_output", "stderr", "error_message"))
    assert not any(secret in completed.text for secret in ("hidden-input-SECRET", "hidden-expected-SECRET", "hidden-stderr-SECRET"))
    persisted_hidden = next(item for item in db_session.scalars(select(ObjectiveGradingRun)).one().results if item.test_case_id_snapshot == hidden.id)
    assert persisted_hidden.expected_output == "hidden-expected-SECRET"
    assert persisted_hidden.actual_output == "hidden-expected-SECRET"


def test_no_approved_tests_has_null_score_in_api(enabled_api, objective_records) -> None:
    response = enabled_api.post(prefix(objective_records) + "/grade/objective", json={})
    assert response.status_code == 202
    assert response.json()["status"] == "NOT_GRADEABLE"
    assert response.json()["correctness_percentage"] is None
    assert response.json()["total_tests"] == 0


def test_duplicate_api_request_returns_conflict(enabled_api, objective_records) -> None:
    objective_records.add_test("x", "x")
    url = prefix(objective_records) + "/grade/objective"
    assert enabled_api.post(url, json={}).status_code == 202
    assert enabled_api.post(url, json={}).status_code == 409


@pytest.mark.parametrize("body", [{"image": "arbitrary/image"}, {"score": 100}, {"test_ids": []}])
def test_api_request_forbids_grading_overrides(enabled_api, objective_records, body: dict) -> None:
    response = enabled_api.post(prefix(objective_records) + "/grade/objective", json=body)
    assert response.status_code == 422


@pytest.mark.parametrize("missing", ["assignment", "submission"])
def test_missing_assignment_or_submission_is_404(enabled_api, objective_records, missing: str) -> None:
    assignment_id = uuid4() if missing == "assignment" else objective_records.assignment.id
    submission_id = uuid4() if missing == "submission" else objective_records.submission.id
    response = enabled_api.post(f"/assignments/{assignment_id}/submissions/{submission_id}/grade/objective", json={})
    assert response.status_code == 404


def test_assignment_submission_mismatch_is_404(enabled_api, db_session, objective_records) -> None:
    other = Assignment(
        created_by=objective_records.assignment.created_by, title="Other", description="Other",
        language=ProgrammingLanguage.CPP,
    )
    db_session.add(other)
    db_session.commit()
    response = enabled_api.post(f"/assignments/{other.id}/submissions/{objective_records.submission.id}/grade/objective", json={})
    assert response.status_code == 404


def test_unsupported_language_is_422(enabled_api, db_session, objective_records) -> None:
    objective_records.assignment.language = ProgrammingLanguage.PYTHON
    objective_records.submission.language = ProgrammingLanguage.PYTHON
    db_session.commit()
    response = enabled_api.post(prefix(objective_records) + "/grade/objective", json={})
    assert response.status_code == 422


def test_missing_run_and_latest_are_404(enabled_api, objective_records) -> None:
    base = prefix(objective_records)
    assert enabled_api.get(base + "/grading-runs/latest").status_code == 404
    assert enabled_api.get(base + "/grading-runs/" + str(uuid4())).status_code == 404


def test_run_cannot_be_read_under_another_submission(enabled_api, db_session, objective_records) -> None:
    objective_records.add_test("x", "x")
    first = enabled_api.post(prefix(objective_records) + "/grade/objective", json={}).json()
    other = Submission(
        assignment_id=objective_records.assignment.id, student_id=objective_records.submission.student_id,
        submission_number=2, source_code="int main() {}", language=ProgrammingLanguage.CPP,
    )
    db_session.add(other)
    db_session.commit()
    response = enabled_api.get(f"/assignments/{objective_records.assignment.id}/submissions/{other.id}/grading-runs/{first['id']}")
    assert response.status_code == 404


def test_database_failure_returns_safe_retryable_response(enabled_api, db_session, objective_records, monkeypatch) -> None:
    objective_records.add_test("x", "x")
    def fail_commit():
        raise SQLAlchemyError("postgres-password-host-private-detail")
    monkeypatch.setattr(db_session, "commit", fail_commit)
    response = enabled_api.post(prefix(objective_records) + "/grade/objective", json={})
    assert response.status_code == 503
    assert "postgres-password-host-private-detail" not in response.text
    assert db_session.scalars(select(ObjectiveGradingRun)).all() == []
