"""Local worker CLI. There is deliberately no execution HTTP endpoint."""

import argparse
from pathlib import Path
import signal
import sys
import threading

from pydantic import ValidationError

from app.execution.config import ExecutionSettings
from app.execution.models import ExecutionErrorCategory, ExecutionRequest, ExecutionStatus
from app.execution.sandbox import failure
from app.execution.service import ExecutionService, configure_worker_logging


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, help="JSON request file, or - for stdin")
    parser.add_argument("--log-file", default="worker.execution.log")
    arguments = parser.parse_args()
    settings = ExecutionSettings()
    envelope_limit = 6 * (settings.source_limit_bytes + settings.input_limit_bytes) + 8192
    cancellation = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: cancellation.set())
    signal.signal(signal.SIGTERM, lambda *_: cancellation.set())
    try:
        configure_worker_logging(arguments.log_file)
        if arguments.request == "-":
            raw = sys.stdin.buffer.read(envelope_limit + 1)
        else:
            with Path(arguments.request).open("rb") as stream:
                raw = stream.read(envelope_limit + 1)
        if len(raw) > envelope_limit:
            raise ValueError("Oversized request")
        request = ExecutionRequest.model_validate_json(raw)
    except (OSError, ValueError, ValidationError):
        result = failure("Invalid or unreadable execution request.", category=ExecutionErrorCategory.VALIDATION)
    else:
        result = ExecutionService(settings=settings).execute(request, cancel_event=cancellation)
    print(result.model_dump_json())
    return 0 if result.status == ExecutionStatus.SUCCESS else (2 if result.status == ExecutionStatus.SYSTEM_ERROR else 1)


if __name__ == "__main__":
    raise SystemExit(main())
