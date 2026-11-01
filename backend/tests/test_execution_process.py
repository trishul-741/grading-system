"""Exercise transport against trusted fixed Python helpers, never student code."""

import sys
import threading
import time

import pytest

from app.execution.process import run_bounded


def invoke(script: str, **overrides):
    return run_bounded([sys.executable, "-I", "-c", script], **{
        "timeout": 3, "stdout_limit": 4096, "stderr_limit": 4096, **overrides,
    })


def test_binary_stdin_round_trip():
    payload = b"\x00\xffhello\n" * 10000
    result = invoke("import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())",
                    input_data=payload, stdout_limit=len(payload))
    assert result.returncode == 0
    assert result.stdout == payload
    assert not result.output_limited
    assert not result.timed_out


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_output_flood_is_bounded_and_terminated(stream):
    result = invoke(f"import sys; sys.{stream}.buffer.write(b'x' * 2000000)")
    assert result.output_limited
    assert len(getattr(result, stream)) == 4096
    assert not result.timed_out


def test_both_streams_are_drained_without_deadlock():
    result = invoke("import sys; sys.stdout.buffer.write(b'o'*50000); sys.stderr.buffer.write(b'e'*50000)",
                    stdout_limit=50000, stderr_limit=50000)
    assert result.returncode == 0
    assert result.stdout == b"o" * 50000
    assert result.stderr == b"e" * 50000


def test_unread_stdin_and_stuck_process_have_deadline():
    started = time.monotonic()
    result = invoke("import time; time.sleep(30)", input_data=b"x" * 1000000, timeout=0.15)
    assert result.timed_out
    assert time.monotonic() - started < 3


def test_cancellation_terminates_transport():
    cancellation = threading.Event()
    timer = threading.Timer(0.1, cancellation.set)
    timer.start()
    try:
        result = invoke("import time; time.sleep(30)", cancel_event=cancellation)
    finally:
        timer.cancel()
    assert result.cancelled
    assert not result.timed_out


def test_missing_cli_raises_without_exposing_diagnostics():
    with pytest.raises(FileNotFoundError):
        run_bounded(["definitely-not-a-docker-command-32133"], timeout=1,
                    stdout_limit=100, stderr_limit=100)
