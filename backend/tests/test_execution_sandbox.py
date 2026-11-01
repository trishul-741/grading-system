"""Mocked Docker control-plane tests, never evidence of kernel isolation.

Real compiler/runtime and adversarial isolation tests live in the separately
marked Docker integration suite. This file only runs trusted Python fakes.
"""

import base64
from copy import deepcopy
import json
import threading
from uuid import uuid4

import pytest

from app.execution.config import ExecutionSettings
from app.execution.models import (
    ExecutionErrorCategory as Category,
    ExecutionPhase as Phase,
    ExecutionRequest,
    ExecutionStatus as Status,
)
from app.execution.process import CommandResult
from app.execution.sandbox import DockerSandbox


ARTIFACT = b"\x7fELFfake-compiled-program"
IMAGE_ID = "sha256:" + "a" * 64
INFO = {
    "OSType": "linux", "CgroupVersion": "2", "CgroupDriver": "systemd",
    "MemoryLimit": True, "SwapLimit": True, "PidsLimit": True,
    "CpuCfsQuota": True, "CpuCfsPeriod": True,
    "SecurityOptions": ["name=seccomp,profile=builtin", "name=cgroupns"],
    "Warnings": [],
}
IMAGE = {
    "Id": IMAGE_ID, "Os": "linux",
    "Config": {
        "Volumes": None, "OnBuild": None,
        "Entrypoint": ["/usr/bin/python3", "-I", "/opt/execution/phase_runner.py"],
    },
}
STATE = {"Running": False, "OOMKilled": False, "ExitCode": 0, "Error": ""}


def encoded(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def report(
    phase: Phase,
    *,
    status: Status = Status.SUCCESS,
    exit_code: int | None = 0,
    stdout: bytes = b"",
    stderr: bytes = b"",
    **overrides: object,
) -> dict:
    categories = {
        Status.COMPILE_ERROR: Category.COMPILE,
        Status.RUNTIME_ERROR: Category.RUNTIME,
        Status.TIMEOUT: Category.TIMEOUT,
        Status.MEMORY_LIMIT: Category.MEMORY_LIMIT,
        Status.OUTPUT_LIMIT: Category.OUTPUT_LIMIT,
        Status.SYSTEM_ERROR: Category.INFRASTRUCTURE,
    }
    value = {
        "version": 1, "phase": phase.value, "status": status.value,
        "exit_code": exit_code, "duration_ms": 17 if phase == Phase.COMPILE else 29,
        "stdout_b64": encoded(stdout), "stderr_b64": encoded(stderr),
        "artifact_b64": encoded(ARTIFACT) if phase == Phase.COMPILE and status == Status.SUCCESS else None,
        "error_category": categories[status].value if status in categories else None,
        "error": "trusted report explanation" if status != Status.SUCCESS else None,
    }
    value.update(overrides)
    return value


class FakeDocker(DockerSandbox):
    def __init__(self, settings: ExecutionSettings | None = None) -> None:
        super().__init__(settings)
        self.calls: list[tuple[list[str], dict]] = []
        self.info = deepcopy(INFO)
        self.image = deepcopy(IMAGE)
        self.states = {phase: deepcopy(STATE) for phase in Phase}
        self.reports = {phase: report(phase) for phase in Phase}
        self.names: dict[str, Phase] = {}
        self.overrides: dict[str, CommandResult | Exception] = {}
        self.cancel_on_create = False
        self.cleanup_present = False

    def _command(self, arguments: list[str], **kwargs) -> CommandResult:
        self.calls.append((list(arguments), kwargs))
        operation = arguments[0]
        if operation == "create":
            name = arguments[arguments.index("--name") + 1]
            phase = Phase.COMPILE if arguments[-1] == "compile" else Phase.RUN
            self.names[name] = phase
            if self.cancel_on_create:
                kwargs["cancel_event"].set()
        elif operation in {"start", "inspect"}:
            name = arguments[-1] if operation == "start" else arguments[1]
            phase = self.names[name]
        else:
            phase = None
        override = self.overrides.get(f"{operation}:{phase.value}" if phase else operation)
        if override is None:
            override = self.overrides.get(operation)
        if override is not None:
            if isinstance(override, Exception):
                raise override
            return override
        if operation == "info":
            payload = self.info
        elif operation == "image":
            payload = self.image
        elif operation == "start":
            payload = self.reports[phase]
        elif operation == "inspect":
            payload = self.states[phase]
        elif operation == "ps":
            return CommandResult(0, b"still-present" if self.cleanup_present else b"", b"")
        else:
            return CommandResult(0, b"container-id\n" if operation == "create" else b"", b"")
        return CommandResult(0, payload if isinstance(payload, bytes) else json.dumps(payload).encode(), b"")

    def calls_for(self, operation: str) -> list[tuple[list[str], dict]]:
        return [call for call in self.calls if call[0][0] == operation]


def run(sandbox: FakeDocker, *, cancel_event: threading.Event | None = None):
    return sandbox.run(
        ExecutionRequest(source_code="int main() { /* shell: ; $() */ }", input_data="é 2\n"),
        job_id=uuid4(),
        cancel_event=cancel_event if cancel_event is not None else threading.Event(),
    )


def assert_cleaned(sandbox: FakeDocker, phases: int) -> None:
    created_names = {
        args[args.index("--name") + 1] for args, _ in sandbox.calls_for("create")
    }
    assert len(created_names) == phases
    assert {args[-1] for args, _ in sandbox.calls_for("rm")} == created_names
    assert len(sandbox.calls_for("ps")) >= phases
    assert all("--force" in args for args, _ in sandbox.calls_for("rm"))


def test_valid_program_and_stdin_use_bounded_binary_transport() -> None:
    sandbox = FakeDocker()
    sandbox.reports[Phase.RUN] = report(Phase.RUN, stdout=b"3\n", stderr=b"note\n")
    result = run(sandbox)
    assert result.status == Status.SUCCESS
    assert result.stdout == "3\n" and result.stderr == "note\n"
    assert result.compilation_time_ms == 17 and result.execution_time_ms == 29
    starts = sandbox.calls_for("start")
    assert starts[0][1]["input_data"] == b"int main() { /* shell: ; $() */ }"
    assert starts[1][1]["input_data"] == len(ARTIFACT).to_bytes(8, "big") + ARTIFACT + "é 2\n".encode()
    assert all(call[1]["stdout_limit"] > len(ARTIFACT) for call in starts)
    assert all(call[1]["timeout"] > 0 for call in starts)
    assert_cleaned(sandbox, 2)


def test_compilation_error_stops_before_runtime() -> None:
    sandbox = FakeDocker()
    sandbox.reports[Phase.COMPILE] = report(
        Phase.COMPILE, status=Status.COMPILE_ERROR, exit_code=1,
        stderr=b"main.cpp:1: error: missing semicolon",
    )
    result = run(sandbox)
    assert result.status == Status.COMPILE_ERROR
    assert result.error_category == Category.COMPILE
    assert result.exit_code == 1
    assert "missing semicolon" in result.stderr
    assert result.compilation_time_ms == 17
    assert result.execution_time_ms is None
    assert len(sandbox.calls_for("start")) == 1
    assert_cleaned(sandbox, 1)


@pytest.mark.parametrize("exit_code", [-11, 1, 124, 137])
def test_runtime_exit_is_not_guessed_as_timeout_or_oom(exit_code: int) -> None:
    sandbox = FakeDocker()
    sandbox.reports[Phase.RUN] = report(
        Phase.RUN, status=Status.RUNTIME_ERROR, exit_code=exit_code,
    )
    result = run(sandbox)
    assert result.status == Status.RUNTIME_ERROR
    assert result.error_category == Category.RUNTIME
    assert result.exit_code == exit_code
    assert_cleaned(sandbox, 2)


@pytest.mark.parametrize("phase", list(Phase))
def test_trusted_timeout_report_is_a_student_timeout(phase: Phase) -> None:
    sandbox = FakeDocker()
    sandbox.reports[phase] = report(phase, status=Status.TIMEOUT, exit_code=-9)
    result = run(sandbox)
    assert result.status == Status.TIMEOUT
    assert result.phase == phase
    assert result.error_category == Category.TIMEOUT
    assert_cleaned(sandbox, 1 if phase == Phase.COMPILE else 2)


@pytest.mark.parametrize("phase", list(Phase))
def test_oom_requires_positive_docker_state(phase: Phase) -> None:
    sandbox = FakeDocker()
    sandbox.states[phase].update(OOMKilled=True, ExitCode=137)
    sandbox.overrides[f"start:{phase.value}"] = CommandResult(137, b"", b"")
    result = run(sandbox)
    assert result.status == Status.MEMORY_LIMIT
    assert result.error_category == Category.MEMORY_LIMIT
    assert result.exit_code == 137
    assert_cleaned(sandbox, 1 if phase == Phase.COMPILE else 2)


def test_memory_report_without_confirming_docker_oom_is_infrastructure_error() -> None:
    sandbox = FakeDocker()
    sandbox.reports[Phase.RUN] = report(Phase.RUN, status=Status.MEMORY_LIMIT, exit_code=137)
    result = run(sandbox)
    assert result.status == Status.SYSTEM_ERROR
    assert result.error_category == Category.INFRASTRUCTURE
    assert_cleaned(sandbox, 2)


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_reported_output_limit_is_preserved(stream: str) -> None:
    sandbox = FakeDocker()
    sandbox.reports[Phase.RUN] = report(
        Phase.RUN, status=Status.OUTPUT_LIMIT, exit_code=-9, **{stream: b"X" * 20}
    )
    result = run(sandbox)
    assert result.status == Status.OUTPUT_LIMIT
    assert result.error_category == Category.OUTPUT_LIMIT
    assert getattr(result, stream) == "X" * 20
    assert_cleaned(sandbox, 2)


@pytest.mark.parametrize(
    "override",
    [
        FileNotFoundError("docker executable /host/private/path"),
        CommandResult(1, b"", b"infrastructure credential=secret"),
        CommandResult(-1, b"", b"", timed_out=True),
    ],
)
def test_docker_unavailable_or_invocation_failure_is_safe(override: object) -> None:
    sandbox = FakeDocker()
    sandbox.overrides["info"] = override
    result = run(sandbox)
    assert result.status == Status.SYSTEM_ERROR
    assert result.error_category == Category.INFRASTRUCTURE
    assert "/host/private/path" not in result.model_dump_json()
    assert "credential=secret" not in result.model_dump_json()
    assert sandbox.calls_for("create") == []


@pytest.mark.parametrize(
    "change",
    [
        {"OSType": "windows"}, {"CgroupVersion": "1"},
        {"CgroupDriver": "none"}, {"MemoryLimit": False}, {"SwapLimit": False},
        {"PidsLimit": False}, {"CpuCfsQuota": False}, {"CpuCfsPeriod": False},
        {"SecurityOptions": []},
        {"SecurityOptions": ["name=seccomp,profile=unconfined"]},
        {"SecurityOptions": [42]},
        {"SecurityOptions": None},
        {"Warnings": ["No memory limit support"]},
        {"MemoryLimit": "true"},
    ],
)
def test_preflight_rejects_missing_safeguards(change: dict) -> None:
    sandbox = FakeDocker()
    sandbox.info.update(change)
    assert run(sandbox).status == Status.SYSTEM_ERROR
    assert sandbox.calls_for("create") == []


@pytest.mark.parametrize(
    "image",
    [
        {**IMAGE, "Id": "sha256:" + "z" * 64},
        {**IMAGE, "Os": "windows"},
        {**IMAGE, "Config": {**IMAGE["Config"], "Volumes": {"/host": {}}}},
        {**IMAGE, "Config": {**IMAGE["Config"], "OnBuild": ["RUN untrusted"]}},
        {**IMAGE, "Config": {**IMAGE["Config"], "Entrypoint": ["/bin/sh"]}},
        {**IMAGE, "Config": None},
        {**IMAGE, "Config": []},
    ],
)
def test_unexpected_image_configuration_is_rejected(image: dict) -> None:
    sandbox = FakeDocker()
    sandbox.image = image
    assert run(sandbox).status == Status.SYSTEM_ERROR
    assert sandbox.calls_for("create") == []


@pytest.mark.parametrize(
    "malformed",
    [
        b"not-json", b"{}", b"[]",
        report(Phase.RUN),
        report(Phase.COMPILE, version=True),
        {**report(Phase.COMPILE), "status": "UNKNOWN"},
        report(Phase.COMPILE, duration_ms=-1),
        report(Phase.COMPILE, duration_ms=999999999),
        report(Phase.COMPILE, stdout_b64="%%%"),
        report(Phase.COMPILE, artifact_b64=encoded(b"not-an-ELF")),
        report(Phase.COMPILE, exit_code=1),
        report(Phase.COMPILE, artifact_b64=None),
        report(Phase.COMPILE, extra_field="must not be accepted"),
        report(Phase.COMPILE, status=Status.COMPILE_ERROR, exit_code=1,
               error_category="RUNTIME"),
    ],
)
def test_malformed_reports_fail_closed_and_cleanup(malformed: object) -> None:
    sandbox = FakeDocker()
    sandbox.reports[Phase.COMPILE] = malformed
    result = run(sandbox)
    assert result.status == Status.SYSTEM_ERROR
    assert result.error_category == Category.INFRASTRUCTURE
    assert result.stdout == "" and result.stderr == ""
    assert_cleaned(sandbox, 1)


@pytest.mark.parametrize("field", ["stdout_b64", "stderr_b64", "artifact_b64"])
def test_decoded_stream_or_artifact_cannot_exceed_limits(field: str) -> None:
    settings = ExecutionSettings(stdout_limit_bytes=10, stderr_limit_bytes=10, artifact_limit_bytes=1024)
    sandbox = FakeDocker(settings)
    payload = b"\x7fELF" + b"X" * 1025 if field == "artifact_b64" else b"X" * 11
    sandbox.reports[Phase.COMPILE][field] = encoded(payload)
    result = run(sandbox)
    assert result.status == Status.SYSTEM_ERROR
    assert result.stdout == "" and result.stderr == ""
    assert_cleaned(sandbox, 1)


@pytest.mark.parametrize(
    "state",
    [
        b"unexpected state", {}, [],
        {**STATE, "Running": True}, {**STATE, "Running": "false"},
        {**STATE, "OOMKilled": "false"}, {**STATE, "ExitCode": "0"},
        {**STATE, "Error": "infrastructure secret"},
    ],
)
def test_malformed_or_nonterminal_container_state_is_infrastructure(state: object) -> None:
    sandbox = FakeDocker()
    sandbox.states[Phase.COMPILE] = state
    result = run(sandbox)
    assert result.status == Status.SYSTEM_ERROR
    assert "infrastructure secret" not in result.model_dump_json()
    assert_cleaned(sandbox, 1)


@pytest.mark.parametrize(
    "attached",
    [
        CommandResult(-1, b"", b"", timed_out=True),
        CommandResult(-1, b"", b"", output_limited=True),
        CommandResult(125, b"", b"daemon credentials=/host/path"),
    ],
)
def test_transport_failure_is_infrastructure_and_still_cleans_up(attached: CommandResult) -> None:
    sandbox = FakeDocker()
    sandbox.overrides["start:COMPILE"] = attached
    result = run(sandbox)
    assert result.status == Status.SYSTEM_ERROR
    assert result.error_category == Category.INFRASTRUCTURE
    assert "daemon credentials" not in result.model_dump_json()
    assert_cleaned(sandbox, 1)


@pytest.mark.parametrize("during_create", [False, True])
def test_cancelled_work_is_classified_and_cleaned(during_create: bool) -> None:
    sandbox = FakeDocker()
    if during_create:
        sandbox.cancel_on_create = True
        sandbox.overrides["create"] = CommandResult(-1, b"", b"", cancelled=True)
    else:
        sandbox.overrides["start:COMPILE"] = CommandResult(-1, b"", b"", cancelled=True)
    result = run(sandbox)
    assert result.status == Status.SYSTEM_ERROR
    assert result.error_category == Category.CANCELLED
    assert_cleaned(sandbox, 1)


def test_cleanup_failure_blocks_all_future_work() -> None:
    sandbox = FakeDocker()
    sandbox.cleanup_present = True
    result = run(sandbox)
    assert result.status == Status.SYSTEM_ERROR
    assert "cleanup" in result.error.lower()
    assert len(sandbox.calls_for("rm")) == 2
    calls_before = len(sandbox.calls)
    sandbox.cleanup_present = False
    assert run(sandbox).status == Status.SYSTEM_ERROR
    assert len(sandbox.calls) == calls_before


def test_uncertain_create_blocks_future_work_even_after_absence_check() -> None:
    sandbox = FakeDocker()
    sandbox.overrides["create"] = CommandResult(-1, b"", b"", timed_out=True)
    assert run(sandbox).status == Status.SYSTEM_ERROR
    assert_cleaned(sandbox, 1)
    calls_before = len(sandbox.calls)
    sandbox.overrides.clear()
    assert run(sandbox).status == Status.SYSTEM_ERROR
    assert len(sandbox.calls) == calls_before


def test_cancelled_event_after_successful_create_does_not_start_container() -> None:
    sandbox = FakeDocker()
    sandbox.cancel_on_create = True
    result = run(sandbox)
    assert result.error_category == Category.CANCELLED
    assert sandbox.calls_for("start") == []
    assert_cleaned(sandbox, 1)


def test_commands_request_restricted_configuration_without_untrusted_arguments() -> None:
    sandbox = FakeDocker()
    assert run(sandbox).status == Status.SUCCESS
    creates = sandbox.calls_for("create")
    for args, _ in creates:
        assert args[args.index("--network") + 1] == "none"
        assert args[args.index("--ipc") + 1] == "none"
        assert args[args.index("--cgroupns") + 1] == "private"
        assert args[args.index("--user") + 1] == "10001:10001"
        assert args[args.index("--cap-drop") + 1] == "ALL"
        assert args[args.index("--security-opt") + 1] == "no-new-privileges=true"
        assert args[args.index("--log-driver") + 1] == "none"
        assert args[args.index("--restart") + 1] == "no"
        assert "--read-only" in args
        assert args[-2] == IMAGE_ID
        assert "--pids-limit" in args and "--cpus" in args
        assert "--memory-swap" in args and "--memory" in args
        assert "grading.execution.managed=true" in args
        tmpfs = [args[index + 1] for index, value in enumerate(args) if value == "--tmpfs"]
        assert len(tmpfs) == 2
        assert all("nr_inodes=1024" in value and "mode=0700" in value for value in tmpfs)
        assert all("nosuid" in value and "nodev" in value and "size=" in value for value in tmpfs)
        assert not {"--privileged", "--pid", "--uts", "--volume", "-v", "--mount", "--volumes-from", "--device"}.intersection(args)
        assert not any("docker.sock" in value or "int main" in value or "$()" in value for value in args)
        assert not {"sh", "bash", "-c"}.intersection(args)
    assert creates[0][0][creates[0][0].index("--memory") + 1] == "512m"
    assert creates[1][0][creates[1][0].index("--memory") + 1] == "256m"


def test_docker_failure_log_excludes_arguments_input_and_stderr(monkeypatch, caplog) -> None:
    import logging
    from app.execution import sandbox as sandbox_module

    expected = CommandResult(1, b"private-docker-output-332874", b"private-docker-stderr-token-628441")
    calls = []
    def fake_transport(arguments, **kwargs):
        calls.append((arguments, kwargs))
        return expected
    monkeypatch.setattr(sandbox_module, "run_bounded", fake_transport)
    with caplog.at_level(logging.INFO, logger="grading.execution"):
        actual = DockerSandbox()._command(
            ["inspect", "private-container-argument-440978"],
            input_data=b"private-standard-input-720923",
        )
    assert actual is expected
    assert len(calls) == 1
    records = [record for record in caplog.records if record.name == "grading.execution"]
    assert len(records) == 1
    payload = json.loads(records[0].getMessage())
    assert set(payload) == {
        "event", "operation", "duration_ms", "returncode", "timed_out", "output_limited", "cancelled",
    }
    assert payload["event"] == "docker_control_failure"
    assert payload["operation"] == "inspect"
    assert payload["duration_ms"] >= 0
    assert payload["returncode"] == 1
    assert payload["timed_out"] is False
    assert payload["output_limited"] is False
    assert payload["cancelled"] is False
    assert all(secret not in caplog.text for secret in (
        "private-docker-output-332874", "private-docker-stderr-token-628441",
        "private-container-argument-440978", "private-standard-input-720923",
    ))
