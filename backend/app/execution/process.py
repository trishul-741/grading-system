"""Bounded binary subprocess transport for the trusted Docker CLI only."""

from dataclasses import dataclass
import subprocess
import threading
import time
from collections.abc import Sequence


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: bytes
    stderr: bytes
    timed_out: bool = False
    output_limited: bool = False
    cancelled: bool = False


def run_bounded(
    arguments: Sequence[str],
    *,
    timeout: float,
    stdout_limit: int,
    stderr_limit: int,
    input_data: bytes = b"",
    cancel_event: threading.Event | None = None,
) -> CommandResult:
    """Drain both pipes concurrently, retain at most each limit, and kill on overflow.

    communicate()/capture_output would retain unlimited output. Reader threads
    never wait for a queue consumer and have a fixed retained-byte budget. They
    are daemon threads as a final fallback for a broken OS pipe/CLI; all joins
    and waits are bounded. Killing the CLI does NOT kill its container: callers
    must independently remove the named container in a finally block.
    """
    process = subprocess.Popen(
        list(arguments), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, shell=False, bufsize=0,
    )
    stdout = bytearray()
    stderr = bytearray()
    overflow = threading.Event()
    pipe_failure = threading.Event()

    def drain(pipe, target: bytearray, limit: int) -> None:
        try:
            while chunk := pipe.read(8192):
                remaining = max(0, limit - len(target))
                target.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    overflow.set()
        except (OSError, ValueError):
            pipe_failure.set()
        finally:
            pipe.close()

    def feed() -> None:
        try:
            view = memoryview(input_data)
            while view:
                written = process.stdin.write(view[:8192])
                if not written:
                    break
                view = view[written:]
        except (BrokenPipeError, OSError, ValueError):
            pass  # Early child exit is normal; inspect its exit/result instead.
        finally:
            process.stdin.close()

    threads = [
        threading.Thread(target=drain, args=(process.stdout, stdout, stdout_limit), daemon=True),
        threading.Thread(target=drain, args=(process.stderr, stderr, stderr_limit), daemon=True),
        threading.Thread(target=feed, daemon=True),
    ]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + timeout
    timed_out = cancelled = False
    try:
        while process.poll() is None or any(t.is_alive() for t in threads):
            cancelled = cancel_event is not None and cancel_event.is_set()
            timed_out = time.monotonic() >= deadline
            if cancelled or timed_out or overflow.is_set() or pipe_failure.is_set():
                break
            time.sleep(0.01)
    finally:
        if process.poll() is None:
            process.kill()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            # Fail closed in caller; this should only occur for an OS/CLI fault.
            timed_out = True
        for thread in threads:
            thread.join(timeout=0.2)
    if pipe_failure.is_set() or any(t.is_alive() for t in threads):
        timed_out = True
    return CommandResult(
        returncode=process.returncode if process.returncode is not None else -1,
        stdout=bytes(stdout), stderr=bytes(stderr), timed_out=timed_out,
        output_limited=overflow.is_set(), cancelled=cancelled,
    )
