"""Docker boundary for a dedicated worker, never an HTTP router.

No host mounts, socket mounts, volumes, shell commands or host execution of
compiled artifacts. Artifacts cross bounded pipes between independent containers.
"""

import base64
import binascii
import json
import logging
import threading
import time
from dataclasses import dataclass
from uuid import UUID, uuid4

from pydantic import ValidationError

from app.execution.config import ExecutionSettings
from app.execution.languages import get_language_config
from app.execution.models import (
    ExecutionErrorCategory as Category, ExecutionPhase as Phase,
    ExecutionRequest, ExecutionResult, ExecutionStatus as Status, PhaseReport,
)
from app.execution.process import CommandResult, run_bounded


logger = logging.getLogger("grading.execution")


class InfrastructureError(Exception):
    """Only fixed, public-safe messages may be stored in this exception."""


@dataclass(frozen=True)
class PhaseOutcome:
    result: ExecutionResult
    artifact: bytes = b""


_MESSAGES = {
    Status.COMPILE_ERROR: "Compilation failed.",
    Status.RUNTIME_ERROR: "The program exited unsuccessfully.",
    Status.TIMEOUT: "The phase exceeded its time limit.",
    Status.MEMORY_LIMIT: "The container exceeded its memory limit.",
    Status.OUTPUT_LIMIT: "The phase exceeded its output limit.",
    Status.SYSTEM_ERROR: "Execution infrastructure failed; retry later.",
}
_CATEGORIES = {
    Status.COMPILE_ERROR: Category.COMPILE, Status.RUNTIME_ERROR: Category.RUNTIME,
    Status.TIMEOUT: Category.TIMEOUT, Status.MEMORY_LIMIT: Category.MEMORY_LIMIT,
    Status.OUTPUT_LIMIT: Category.OUTPUT_LIMIT, Status.SYSTEM_ERROR: Category.INFRASTRUCTURE,
}


def failure(message: str, *, category: Category = Category.INFRASTRUCTURE,
            job_id: UUID | None = None, phase: Phase | None = None) -> ExecutionResult:
    return ExecutionResult(status=Status.SYSTEM_ERROR, error_category=category,
                           error=message, job_id=job_id, phase=phase)


class DockerSandbox:
    def __init__(self, settings: ExecutionSettings | None = None) -> None:
        self.settings = settings or ExecutionSettings()
        # A failed cleanup is a safety fault, not permission to start more work.
        self._cleanup_failed = threading.Event()

    def _command(self, arguments: list[str], *, input_data: bytes = b"",
                 timeout: float | None = None, stdout_limit: int = 65536,
                 cancel_event: threading.Event | None = None) -> CommandResult:
        started = time.monotonic()
        result = run_bounded(
            ["docker", *arguments], input_data=input_data,
            timeout=timeout or self.settings.docker_control_timeout_seconds,
            stdout_limit=stdout_limit, stderr_limit=8192, cancel_event=cancel_event,
        )
        if result.returncode != 0 or result.timed_out or result.output_limited or result.cancelled:
            # Operations are fixed trusted verbs. Never log Docker stderr, CLI
            # arguments, stdin or exception text: they may include internals.
            logger.info(json.dumps({"event": "docker_control_failure", "operation": arguments[0],
                "duration_ms": int((time.monotonic() - started) * 1000),
                "returncode": result.returncode, "timed_out": result.timed_out,
                "output_limited": result.output_limited, "cancelled": result.cancelled}, separators=(",", ":")))
        return result

    @staticmethod
    def _checked(result: CommandResult) -> bytes:
        if (result.returncode != 0 or result.timed_out or result.output_limited
                or result.cancelled):
            raise InfrastructureError("The Docker operation failed or exceeded its deadline.")
        return result.stdout

    def _preflight(self) -> None:
        raw = self._checked(self._command(["info", "--format", "{{json .}}"], stdout_limit=131072))
        try:
            info = json.loads(raw)
            if not isinstance(info, dict):
                raise ValueError("Invalid Docker metadata")
            security_options = info.get("SecurityOptions")
            if not isinstance(security_options, list) or not all(isinstance(option, str) for option in security_options):
                raise ValueError("Invalid Docker security options")
            if (info["OSType"] != "linux" or info["CgroupVersion"] != "2"
                    or info["CgroupDriver"] not in {"systemd", "cgroupfs"}
                    or not all(info.get(key) is True for key in (
                        "MemoryLimit", "SwapLimit", "PidsLimit", "CpuCfsQuota", "CpuCfsPeriod",
                    ))
                    or not any(option.startswith("name=seccomp,") and "profile=unconfined" not in option
                               for option in security_options)):
                raise ValueError("Unsupported Docker security configuration")
            if info.get("Warnings"):
                # Reject warnings that resource controllers may be ignored.
                raise ValueError("Docker reports security/resource warnings")
        except (KeyError, TypeError, ValueError) as exc:
            raise InfrastructureError("Docker requires Linux, cgroup v2 and seccomp without resource warnings.") from exc

    def _resolve_image(self, image: str) -> str:
        raw = self._checked(self._command(["image", "inspect", image, "--format", "{{json .}}"], stdout_limit=131072))
        try:
            data = json.loads(raw)
            if not isinstance(data, dict) or not isinstance(data.get("Config"), dict):
                raise ValueError("Invalid image metadata")
            image_id = data["Id"]
            config = data["Config"]
            if (not isinstance(image_id, str) or not image_id.startswith("sha256:")
                    or len(image_id) != 71 or data["Os"] != "linux"
                    or config.get("Volumes") or config.get("OnBuild")
                    or config.get("Entrypoint") != ["/usr/bin/python3", "-I", "/opt/execution/phase_runner.py"]):
                raise ValueError("Invalid execution image")
            int(image_id[7:], 16)
            # Use the resolved ID so tag replacement cannot change this job.
            return image_id
        except (KeyError, TypeError, ValueError) as exc:
            raise InfrastructureError("The local execution image is missing or incompatible.") from exc

    def _create_arguments(self, name: str, image: str, phase: Phase, job_id: UUID,
                          command: tuple[str, ...]) -> list[str]:
        settings = self.settings
        memory = settings.compile_memory_mb if phase == Phase.COMPILE else settings.runtime_memory_mb
        arguments = [
            "create", "--pull", "never", "--name", name,
            "--label", "grading.execution.managed=true",
            "--label", f"grading.execution.job={job_id}",
            "--interactive", "--network", "none", "--ipc", "none",
            "--cgroupns", "private", "--user", "10001:10001",
            "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges=true",
            "--memory", f"{memory}m", "--memory-swap", f"{memory}m",
            "--cpus", str(settings.cpus), "--pids-limit", str(settings.pids_limit),
            "--ulimit", "nofile=64:64", "--ulimit", "core=0:0",
            "--ulimit", f"fsize={settings.artifact_limit_bytes}:{settings.artifact_limit_bytes}",
            "--tmpfs", f"/workspace:rw,exec,nosuid,nodev,size={settings.workspace_size_mb}m,nr_inodes=1024,mode=0700,uid=10001,gid=10001",
            "--tmpfs", f"/tmp:rw,noexec,nosuid,nodev,size={settings.tmp_size_mb}m,nr_inodes=1024,mode=0700,uid=10001,gid=10001",
            "--workdir", "/workspace", "--log-driver", "none",
            "--restart", "no", "--no-healthcheck", "--stop-timeout", "0",
        ]
        for field in (
            "source_limit_bytes", "input_limit_bytes", "stdout_limit_bytes",
            "stderr_limit_bytes", "artifact_limit_bytes", "compile_timeout_seconds",
            "runtime_timeout_seconds",
        ):
            arguments.extend(["--env", f"EXECUTION_{field.upper()}={getattr(settings, field)}"])
        return [*arguments, image, *command]

    def _cleanup(self, name: str) -> bool:
        for _ in range(2):
            try:
                removed = self._command(["rm", "--force", "--volumes", name])
                # Do not parse localized stderr or assume failure means absent.
                remaining = self._command(["ps", "--all", "--quiet", "--filter", f"name=^/{name}$"])
                if (not removed.timed_out and not removed.output_limited
                        and not remaining.timed_out and not remaining.output_limited
                        and remaining.returncode == 0 and not remaining.stdout.strip()):
                    return True
            except OSError:
                continue
        self._cleanup_failed.set()
        return False

    def _inspect_state(self, name: str) -> dict:
        raw = self._checked(self._command(["inspect", name, "--format", "{{json .State}}"], stdout_limit=16384))
        try:
            state = json.loads(raw)
            if (type(state["Running"]) is not bool or type(state["OOMKilled"]) is not bool
                    or type(state["ExitCode"]) is not int or type(state["Error"]) is not str
                    or state["Running"] or state["Error"]):
                raise ValueError("Invalid terminal container state")
            return state
        except (KeyError, TypeError, ValueError) as exc:
            raise InfrastructureError("Docker returned an invalid container state.") from exc

    def _decode_report(self, raw: bytes, phase: Phase) -> tuple[PhaseReport, bytes, bytes, bytes]:
        try:
            report = PhaseReport.model_validate_json(raw)
            if report.phase != phase:
                raise ValueError("Wrong execution phase")
            if report.status == Status.MEMORY_LIMIT:
                raise ValueError("Only Docker OOM inspection can confirm a memory limit")
            stdout = base64.b64decode(report.stdout_b64, validate=True)
            stderr = base64.b64decode(report.stderr_b64, validate=True)
            artifact = base64.b64decode(report.artifact_b64, validate=True) if report.artifact_b64 else b""
            if (len(stdout) > self.settings.stdout_limit_bytes
                    or len(stderr) > self.settings.stderr_limit_bytes
                    or len(artifact) > self.settings.artifact_limit_bytes):
                raise ValueError("Oversized execution envelope")
            if phase == Phase.COMPILE and report.status == Status.SUCCESS and not artifact.startswith(b"\x7fELF"):
                raise ValueError("Invalid compiled artifact")
            return report, stdout, stderr, artifact
        except (ValidationError, ValueError, binascii.Error) as exc:
            raise InfrastructureError("The execution worker returned an invalid result.") from exc

    def _phase(self, *, phase: Phase, image: str, command: tuple[str, ...],
               payload: bytes, job_id: UUID, cancel_event: threading.Event) -> PhaseOutcome:
        name = f"grading-{job_id.hex}-{phase.value.lower()}-{uuid4().hex[:8]}"
        result = failure("Execution infrastructure failed; retry later.", job_id=job_id, phase=phase)
        artifact = b""
        if self._cleanup_failed.is_set():
            return PhaseOutcome(failure("The worker requires container cleanup before accepting jobs.", job_id=job_id, phase=phase))
        try:
            created = self._command(self._create_arguments(name, image, phase, job_id, command), cancel_event=cancel_event)
            if created.timed_out or created.cancelled or created.output_limited:
                # A create may complete daemon-side after its client is killed.
                # Require label reconciliation before accepting further work.
                self._cleanup_failed.set()
            self._checked(created)
            if cancel_event.is_set():
                result = failure("Execution was cancelled.", category=Category.CANCELLED, job_id=job_id, phase=phase)
            else:
                wall_limit = (self.settings.compile_timeout_seconds if phase == Phase.COMPILE
                              else self.settings.runtime_timeout_seconds)
                # Bounded startup/report allowance, in addition to inner deadline.
                transport_limit = wall_limit + 2 * self.settings.docker_control_timeout_seconds
                wire_limit = 4096 + 4 * ((self.settings.artifact_limit_bytes
                    + self.settings.stdout_limit_bytes + self.settings.stderr_limit_bytes + 6) // 3)
                attached = self._command(["start", "--attach", "--interactive", name],
                    input_data=payload, timeout=transport_limit, stdout_limit=wire_limit,
                    cancel_event=cancel_event)
                if attached.cancelled:
                    result = failure("Execution was cancelled.", category=Category.CANCELLED, job_id=job_id, phase=phase)
                elif attached.timed_out or attached.output_limited:
                    # No trusted report: daemon/transport may be at fault.
                    result = failure("The sandbox stopped responding; retry later.", job_id=job_id, phase=phase)
                else:
                    state = self._inspect_state(name)
                    if state["OOMKilled"]:
                        result = ExecutionResult(status=Status.MEMORY_LIMIT,
                            error_category=Category.MEMORY_LIMIT, error=_MESSAGES[Status.MEMORY_LIMIT],
                            exit_code=state["ExitCode"], job_id=job_id, phase=phase)
                    elif state["ExitCode"] != 0 or attached.returncode != 0:
                        result = failure("The execution container failed unexpectedly.", job_id=job_id, phase=phase)
                    else:
                        report, stdout, stderr, artifact = self._decode_report(attached.stdout, phase)
                        status = report.status
                        result = ExecutionResult(status=status,
                            stdout=stdout.decode("utf-8", errors="replace"),
                            stderr=stderr.decode("utf-8", errors="replace"), exit_code=report.exit_code,
                            **({"compilation_time_ms": report.duration_ms} if phase == Phase.COMPILE
                               else {"execution_time_ms": report.duration_ms}),
                            error_category=_CATEGORIES.get(status), error=_MESSAGES.get(status),
                            phase=phase, job_id=job_id)
        except (OSError, InfrastructureError):
            result = (failure("Execution was cancelled.", category=Category.CANCELLED, job_id=job_id, phase=phase)
                      if cancel_event.is_set() else failure("Execution infrastructure is unavailable; retry later.", job_id=job_id, phase=phase))
        finally:
            # Name is known before create, covering partially successful creates,
            # client timeouts, KeyboardInterrupt and cooperative cancellation.
            if not self._cleanup(name):
                result = failure("Container cleanup could not be confirmed; the worker is unavailable.", job_id=job_id, phase=phase)
                artifact = b""
        return PhaseOutcome(result=result, artifact=artifact)

    def run(self, request: ExecutionRequest, job_id: UUID,
            cancel_event: threading.Event) -> ExecutionResult:
        if self._cleanup_failed.is_set():
            return failure("The worker requires container cleanup before accepting jobs.", job_id=job_id)
        try:
            self._preflight()
            adapter = get_language_config(request.language)
            image = self._resolve_image(adapter.image)
        except (OSError, InfrastructureError):
            return failure("Execution infrastructure is unavailable; retry later.", job_id=job_id)
        compiled = self._phase(phase=Phase.COMPILE, image=image,
            command=adapter.compile_command, payload=request.source_code.encode("utf-8"),
            job_id=job_id, cancel_event=cancel_event)
        if compiled.result.status != Status.SUCCESS:
            return compiled.result
        if cancel_event.is_set():
            return failure("Execution was cancelled.", category=Category.CANCELLED, job_id=job_id)
        payload = len(compiled.artifact).to_bytes(8, "big") + compiled.artifact + request.input_data.encode("utf-8")
        executed = self._phase(phase=Phase.RUN, image=image, command=adapter.run_command,
                              payload=payload, job_id=job_id, cancel_event=cancel_event)
        return executed.result.model_copy(update={"compilation_time_ms": compiled.result.compilation_time_ms})


def execute_cpp(source_code: str, input_data: str = "") -> ExecutionResult:
    """Local compatibility entry point sharing the worker admission limits."""
    from app.execution.service import get_execution_service
    return get_execution_service().execute(ExecutionRequest(source_code=source_code, input_data=input_data))
