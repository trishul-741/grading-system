import os
import subprocess
import tempfile
import time

from app.execution.models import ExecutionResult
from app.execution.languages import LANGUAGES


TIME_LIMIT_SECONDS = 3

MEMORY_LIMIT = "256m"

CPU_LIMIT = "1.0"

PIDS_LIMIT = 64

COMPILE_TIMEOUT_SECONDS = 15


def execute_cpp(
    source_code: str,
    input_data: str
) -> ExecutionResult:

    config = LANGUAGES["CPP"]

    start = time.perf_counter()

    with tempfile.TemporaryDirectory() as temp_dir:

        source_path = os.path.join(
            temp_dir,
            config.source_file
        )

        with open(
            source_path,
            "w",
            encoding="utf-8"
        ) as f:
            f.write(source_code)

        compile_cmd = [
            "docker",
            "run",
            "--rm",

            "--network",
            "none",

            "--memory",
            MEMORY_LIMIT,

            "--cpus",
            CPU_LIMIT,

            "--pids-limit",
            str(PIDS_LIMIT),

            "--read-only",

            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m",

            "-v",
            f"{temp_dir}:/workspace:rw",

            "-w",
            "/workspace",

            config.image,

            "g++",
            "-std=c++17",
            "-O2",
            "-pipe",
            "main.cpp",
            "-o",
            "main"
        ]

        try:
            compile_result = subprocess.run(
                compile_cmd,
                capture_output=True,
                text=True,
                timeout=COMPILE_TIMEOUT_SECONDS
            )
        except subprocess.TimeoutExpired:
            execution_time = int((time.perf_counter() - start) * 1000)

            return ExecutionResult(
                status="COMPILE_TIMEOUT",
                execution_time_ms=execution_time,
                error="Compilation exceeded time limit"
            )

        if compile_result.returncode != 0:

            execution_time = int(
                (time.perf_counter() - start) * 1000
            )

            return ExecutionResult(
                status="COMPILE_ERROR",
                stdout=compile_result.stdout,
                stderr=compile_result.stderr,
                exit_code=compile_result.returncode,
                execution_time_ms=execution_time
            )

        run_cmd = [
            "docker",
            "run",
            "--rm",
            "-i",

            "--network",
            "none",

            "--memory",
            MEMORY_LIMIT,

            "--cpus",
            CPU_LIMIT,

            "--pids-limit",
            str(PIDS_LIMIT),

            "--read-only",

            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m",

            "-v",
            f"{temp_dir}:/workspace:rw",

            "-w",
            "/workspace",

            config.image,

            "./main"
        ]

        try:

            run_result = subprocess.run(
                run_cmd,
                input=input_data,
                capture_output=True,
                text=True,
                timeout=TIME_LIMIT_SECONDS + 2
            )

        except subprocess.TimeoutExpired:

            execution_time = int(
                (time.perf_counter() - start) * 1000
            )

            return ExecutionResult(
                status="TIMEOUT",
                execution_time_ms=execution_time,
                error="Execution exceeded time limit"
            )

        execution_time = int(
            (time.perf_counter() - start) * 1000
        )

        if run_result.returncode != 0:

            return ExecutionResult(
                status="RUNTIME_ERROR",
                stdout=run_result.stdout,
                stderr=run_result.stderr,
                exit_code=run_result.returncode,
                execution_time_ms=execution_time
            )

        return ExecutionResult(
            status="SUCCESS",
            stdout=run_result.stdout,
            stderr=run_result.stderr,
            exit_code=run_result.returncode,
            execution_time_ms=execution_time
        )