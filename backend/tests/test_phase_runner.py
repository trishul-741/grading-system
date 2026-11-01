"""Transport/parser checks, without executing student code or namespace cleanup.

These unit checks do not demonstrate Docker isolation. The separately marked
integration suite must exercise real PID1, resource and filesystem safeguards.
"""

import importlib.util
import io
import json
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.execution.models import PhaseReport


@pytest.fixture
def runner():
    source = Path(__file__).parents[1] / "execution" / "cpp" / "phase_runner.py"
    spec = importlib.util.spec_from_file_location("execution_phase_runner_test", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_host_invocation_fails_closed_without_process_or_kill(runner, monkeypatch, capsys):
    monkeypatch.setattr(runner.sys, "platform", "test-host")
    monkeypatch.setattr(runner.sys, "argv", ["phase_runner.py", "run"])

    def forbidden(*_args, **_kwargs):
        pytest.fail("A supervisor outside its namespace must not launch or kill processes")

    monkeypatch.setattr(runner.os, "kill", forbidden)
    monkeypatch.setattr(runner.subprocess, "Popen", forbidden)
    assert runner.main() == 0
    report = PhaseReport.model_validate_json(capsys.readouterr().out)
    assert report.status == "SYSTEM_ERROR"
    assert report.error_category == "INFRASTRUCTURE"


def test_cleanup_refuses_host_without_sending_signal(runner, monkeypatch):
    monkeypatch.setattr(runner.sys, "platform", "test-host")
    called = []
    monkeypatch.setattr(runner.os, "kill", lambda *args: called.append(args))
    with pytest.raises(runner.BoundaryError):
        runner._kill_children()
    assert called == []


def test_binary_payload_read_preserves_bytes_and_trailing_input(runner):
    stream = io.BytesIO(b"\x00\xffELFremaining")
    assert runner._read_exact(stream, 5) == b"\x00\xffELF"
    assert stream.read() == b"remaining"


@pytest.mark.parametrize("data,length", [(b"", 8), (b"123", 4)])
def test_truncated_payload_is_rejected(runner, data, length):
    with pytest.raises(runner.BoundaryError):
        runner._read_exact(io.BytesIO(data), length)


@pytest.mark.parametrize("value", ["0", "-1", "200", "not-an-int"])
def test_invalid_integer_limits_are_rejected(runner, monkeypatch, value):
    monkeypatch.setenv("EXECUTION_TEST_LIMIT", value)
    with pytest.raises((ValueError, runner.BoundaryError)):
        runner._integer("TEST_LIMIT", 10, 1, 100)


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "-inf", "101"])
def test_invalid_deadlines_are_rejected(runner, monkeypatch, value):
    monkeypatch.setenv("EXECUTION_TEST_TIMEOUT", value)
    with pytest.raises((ValueError, runner.BoundaryError)):
        runner._seconds("TEST_TIMEOUT", 3.0, 100)


def _report(**changes):
    report = dict(
        version=1, phase="RUN", status="SUCCESS", exit_code=0, duration_ms=1,
        stdout_b64="", stderr_b64="", artifact_b64=None, error_category=None, error=None,
    )
    report.update(changes)
    return json.dumps(report)


@pytest.mark.parametrize("changes", [
    {"exit_code": True}, {"exit_code": 256}, {"exit_code": -65},
    {"duration_ms": -1}, {"duration_ms": 300001}, {"version": 2},
    {"version": True}, {"version": 1.0},
    {"status": "RUNTIME_ERROR", "exit_code": 1},
    {"status": "RUNTIME_ERROR", "exit_code": 1, "error_category": "TIMEOUT", "error": "failed"},
    {"status": "RUNTIME_ERROR", "exit_code": 0, "error_category": "RUNTIME", "error": "failed"},
    {"status": "TIMEOUT", "error_category": "TIMEOUT", "error": " "},
    {"phase": "COMPILE"}, {"artifact_b64": "RUxG"},
    {"phase": "COMPILE", "status": "RUNTIME_ERROR", "exit_code": 1, "error_category": "RUNTIME", "error": "failed"},
])
def test_malformed_or_inconsistent_phase_report_is_rejected(changes):
    with pytest.raises(ValidationError):
        PhaseReport.model_validate_json(_report(**changes))


@pytest.mark.skipif(os.name != "posix", reason="Real nofollow artifact checks require Linux/POSIX")
@pytest.mark.parametrize("kind", ["symlink", "directory", "oversize", "non_elf"])
def test_invalid_artifacts_are_rejected(runner, monkeypatch, tmp_path, kind):
    path = tmp_path / "artifact"
    if kind == "symlink":
        target = tmp_path / "target"
        target.write_bytes(b"\x7fELFcontents")
        path.symlink_to(target)
    elif kind == "directory":
        path.mkdir()
    elif kind == "oversize":
        path.write_bytes(b"\x7fELF" + b"x" * 1024)
    else:
        path.write_bytes(b"not an ELF")
    original_open = os.open

    def redirected_open(requested, flags):
        assert requested == "/workspace/main"
        return original_open(path, flags)

    monkeypatch.setattr(runner.os, "open", redirected_open)
    with pytest.raises((OSError, runner.BoundaryError)):
        runner._artifact(1024)
