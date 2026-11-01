"""Configuration faults fail safely before database or execution work starts."""

import json
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.grading.config import ObjectiveGradingSettings, get_objective_grading_settings
from app.grading.errors import GradingError


SECRET = "private-objective-token-778864-at-least-thirty-two-characters"
PRIVATE_ENVIRONMENT = "C:/private/configuration/environment-985433"


@pytest.fixture
def invalid_configuration(monkeypatch):
    get_objective_grading_settings.cache_clear()
    monkeypatch.setenv("OBJECTIVE_GRADING_API_ENABLED", "true")
    monkeypatch.setenv("OBJECTIVE_GRADING_API_TOKEN", SECRET)
    monkeypatch.setenv("OBJECTIVE_GRADING_ENVIRONMENT", PRIVATE_ENVIRONMENT)
    yield
    get_objective_grading_settings.cache_clear()


def test_settings_validation_hides_raw_environment_inputs(invalid_configuration) -> None:
    with pytest.raises(ValidationError) as caught:
        ObjectiveGradingSettings()
    assert SECRET not in str(caught.value)
    assert PRIVATE_ENVIRONMENT not in str(caught.value)


def test_cached_settings_translate_invalid_configuration_to_safe_domain_error(invalid_configuration) -> None:
    with pytest.raises(GradingError) as caught:
        get_objective_grading_settings()
    assert caught.value.status_code == 503
    assert caught.value.detail == "Objective grading configuration is unavailable"
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__ is True
    assert SECRET not in str(caught.value)
    assert PRIVATE_ENVIRONMENT not in str(caught.value)


def test_api_dependency_configuration_failure_returns_safe_503(client, invalid_configuration, caplog) -> None:
    response = client.post(
        f"/assignments/{uuid4()}/submissions/{uuid4()}/grade/objective",
        json={},
    )
    assert response.status_code == 503
    assert response.json() == {"detail": "Objective grading configuration is unavailable"}
    for sensitive in (SECRET, PRIVATE_ENVIRONMENT, "Traceback", "ValidationError"):
        assert sensitive not in response.text
        assert sensitive not in caplog.text


def test_invalid_worker_configuration_exits_before_logging_or_execution(invalid_configuration, monkeypatch, capsys, tmp_path) -> None:
    from app.grading import worker

    private_log = tmp_path / "private-worker-path-12377.log"
    monkeypatch.setattr("sys.argv", ["objective-worker", "--once", "--log-file", str(private_log)])
    def forbidden_setup(*_args, **_kwargs):
        raise AssertionError("Invalid worker configuration must fail before setup")
    monkeypatch.setattr(worker, "RotatingFileHandler", forbidden_setup)
    monkeypatch.setattr("app.execution.service.get_execution_service", forbidden_setup)
    assert worker.main() == 2
    output = capsys.readouterr()
    assert json.loads(output.out) == {
        "status": "SYSTEM_ERROR", "error": "Invalid objective worker configuration.",
    }
    assert output.err == ""
    assert not private_log.exists()
    assert all(sensitive not in output.out for sensitive in (SECRET, PRIVATE_ENVIRONMENT, str(private_log)))
