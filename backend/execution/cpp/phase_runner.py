"""Linux PID1 supervisor. Its JSON transport is private to the host worker.

No student process inherits the transport descriptor. PID namespace init signal
rules plus non-dumpability prevent a same-UID child from killing/ptracing PID1
or reopening its descriptors through procfs. Docker/host deadlines still bound
the entire phase, including supervisor failures and resource exhaustion.
"""

import base64
import ctypes
import json
import math
import os
import selectors
import signal
import stat
import struct
import subprocess
import sys
import time


class BoundaryError(Exception):
    pass


class ArtifactLimitError(BoundaryError):
    pass


def _integer(name: str, default: int, minimum: int, maximum: int) -> int:
    value = int(os.environ.get("EXECUTION_" + name, default))
    if not minimum <= value <= maximum:
        raise BoundaryError("Invalid execution configuration")
    return value


def _seconds(name: str, default: float, maximum: float) -> float:
    value = float(os.environ.get("EXECUTION_" + name, default))
    if not math.isfinite(value) or not 0 < value <= maximum:
        raise BoundaryError("Invalid execution configuration")
    return value


def _protect_supervisor() -> None:
    if sys.platform != "linux" or os.getpid() != 1 or os.getuid() == 0:
        raise BoundaryError("The execution image requires an unprivileged Linux PID1")
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    # PR_SET_DUMPABLE=0 protects /proc/1/fd and process memory from same-UID
    # children. Children lose this protection when exec loads their executable.
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(4, 0, 0, 0, 0) != 0:
        raise BoundaryError("Supervisor process protection could not be enabled")


def _kill_children() -> None:
    # kill(-1) is safe ONLY inside our own PID namespace, as its namespace init.
    # It reaches forked/setsid descendants that have escaped a process group.
    if sys.platform != "linux" or os.getpid() != 1:
        raise BoundaryError("Refusing cleanup outside the sandbox PID namespace")
    try:
        os.kill(-1, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _reap() -> None:
    while True:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        if pid == 0:
            return


def _read_exact(stream, length: int) -> bytes:
    data = bytearray()
    while len(data) < length:
        part = stream.read(length - len(data))
        if not part:
            raise BoundaryError("Incomplete execution payload")
        data.extend(part)
    return bytes(data)


def _write_new(path: str, content: bytes, mode: int) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, "wb") as output:
        output.write(content)


def _run(command: tuple[str, ...], input_data: bytes, seconds: float,
         stdout_limit: int, stderr_limit: int) -> tuple[int | None, bytes, bytes, str | None]:
    process = subprocess.Popen(
        command, stdin=subprocess.PIPE if input_data else subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True,
        start_new_session=True, restore_signals=True,
    )
    # The child inherits ignored SIGINT. We terminate with SIGKILL; the
    # independent monotonic deadline cannot be defeated by signal handlers.
    selector = selectors.DefaultSelector()
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    limits = {"stdout": stdout_limit, "stderr": stderr_limit}
    input_offset = 0
    outcome = None
    deadline = time.monotonic() + seconds
    descendants_killed = False
    try:
        for stream, label in ((process.stdout, "stdout"), (process.stderr, "stderr")):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, label)
        if process.stdin is not None:
            os.set_blocking(process.stdin.fileno(), False)
            selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
        while selector.get_map() or process.poll() is None:
            if time.monotonic() >= deadline:
                outcome = "TIMEOUT"
                break
            if process.poll() is not None and not descendants_killed:
                _kill_children()
                descendants_killed = True
            events = selector.select(min(0.05, max(0, deadline - time.monotonic())))
            for key, _ in events:
                stream, label = key.fileobj, key.data
                if label == "stdin":
                    try:
                        input_offset += os.write(stream.fileno(), input_data[input_offset:input_offset + 8192])
                    except BrokenPipeError:
                        input_offset = len(input_data)
                    except BlockingIOError:
                        continue
                    if input_offset >= len(input_data):
                        selector.unregister(stream)
                        stream.close()
                    continue
                try:
                    chunk = os.read(stream.fileno(), 8192)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(stream)
                    stream.close()
                    continue
                remaining = limits[label] - len(buffers[label])
                buffers[label].extend(chunk[:remaining])
                if len(chunk) > remaining:
                    outcome = "OUTPUT_LIMIT"
                    break
            if outcome:
                break
    finally:
        _kill_children()
        selector.close()
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None and not stream.closed:
                stream.close()
        try:
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            # The host watchdog removes this entire namespace if the kernel
            # cannot promptly reap a SIGKILLed process (for example D-state).
            pass
        _reap()
    return process.returncode, bytes(buffers["stdout"]), bytes(buffers["stderr"]), outcome


def _artifact(limit: int) -> bytes:
    fd = os.open("/workspace/main", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as binary:
        metadata = os.fstat(binary.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size <= 0:
            raise BoundaryError("Compiler did not produce a regular executable")
        if metadata.st_size > limit:
            raise ArtifactLimitError("Compiled artifact exceeds the permitted size")
        data = binary.read(limit + 1)
    if len(data) > limit:
        raise ArtifactLimitError("Compiled artifact exceeds the permitted size")
    if not data.startswith(b"\x7fELF"):
        raise BoundaryError("Compiler did not produce a valid executable")
    return data


def main() -> int:
    started = time.monotonic()
    phase = "COMPILE" if len(sys.argv) == 2 and sys.argv[1] == "compile" else "RUN"
    report = {
        "version": 1, "phase": phase, "status": "SYSTEM_ERROR", "exit_code": None,
        "duration_ms": 0, "stdout_b64": "", "stderr_b64": "", "artifact_b64": None,
        "error_category": "INFRASTRUCTURE", "error": "Execution supervisor failed",
    }
    protected = False
    try:
        _protect_supervisor()
        protected = True
        if len(sys.argv) != 2 or sys.argv[1] not in ("compile", "run"):
            raise BoundaryError("Invalid execution phase")
        os.umask(0o077)
        source_limit = _integer("SOURCE_LIMIT_BYTES", 65536, 1, 4194304)
        input_limit = _integer("INPUT_LIMIT_BYTES", 65536, 0, 4194304)
        stdout_limit = _integer("STDOUT_LIMIT_BYTES", 65536, 1, 4194304)
        stderr_limit = _integer("STDERR_LIMIT_BYTES", 65536, 1, 4194304)
        artifact_limit = _integer("ARTIFACT_LIMIT_BYTES", 16777216, 1024, 33554432)
        if phase == "COMPILE":
            source = sys.stdin.buffer.read(source_limit + 1)
            if not source or len(source) > source_limit:
                raise BoundaryError("Source exceeds the permitted size")
            _write_new("/workspace/main.cpp", source, 0o400)
            command = ("/usr/bin/g++", "-std=c++17", "-O2", "-pipe", "/workspace/main.cpp", "-o", "/workspace/main")
            input_data = b""
            seconds = _seconds("COMPILE_TIMEOUT_SECONDS", 15.0, 120)
        else:
            length = struct.unpack("!Q", _read_exact(sys.stdin.buffer, 8))[0]
            if not 0 < length <= artifact_limit:
                raise BoundaryError("Executable exceeds the permitted size")
            artifact = _read_exact(sys.stdin.buffer, length)
            if not artifact.startswith(b"\x7fELF"):
                raise BoundaryError("Invalid executable payload")
            _write_new("/workspace/main", artifact, 0o500)
            input_data = sys.stdin.buffer.read(input_limit + 1)
            if len(input_data) > input_limit:
                raise BoundaryError("Input exceeds the permitted size")
            command = ("/workspace/main",)
            seconds = _seconds("RUNTIME_TIMEOUT_SECONDS", 3.0, 60)
        code, stdout, stderr, outcome = _run(command, input_data, seconds, stdout_limit, stderr_limit)
        report.update(exit_code=code, stdout_b64=base64.b64encode(stdout).decode("ascii"),
                      stderr_b64=base64.b64encode(stderr).decode("ascii"))
        if outcome is not None:
            report.update(status=outcome, error_category=outcome,
                          error="Phase exceeded its time limit" if outcome == "TIMEOUT" else "Phase exceeded its output limit")
        elif code != 0:
            report.update(status="COMPILE_ERROR" if phase == "COMPILE" else "RUNTIME_ERROR",
                          error_category="COMPILE" if phase == "COMPILE" else "RUNTIME",
                          error="Compilation failed" if phase == "COMPILE" else "Program exited unsuccessfully")
        else:
            if phase == "COMPILE":
                report["artifact_b64"] = base64.b64encode(_artifact(artifact_limit)).decode("ascii")
            report.update(status="SUCCESS", error_category=None, error=None)
    except ArtifactLimitError:
        report.update(status="OUTPUT_LIMIT", error_category="OUTPUT_LIMIT",
                      error="Compiled artifact exceeds its size limit", artifact_b64=None)
    except (BoundaryError, OSError, ValueError, struct.error, subprocess.SubprocessError):
        # Raw exceptions can contain internals. Diagnostics from the compiler or
        # program are preserved in their separately bounded stream fields.
        report.update(status="SYSTEM_ERROR", error_category="INFRASTRUCTURE",
                      error="Execution supervisor could not complete the phase", artifact_b64=None)
    finally:
        if protected:
            _kill_children()
            _reap()
    report["duration_ms"] = max(0, int((time.monotonic() - started) * 1000))
    sys.stdout.write(json.dumps(report, separators=(",", ":")))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
