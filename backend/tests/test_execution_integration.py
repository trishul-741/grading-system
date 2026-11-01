"""Real Docker safeguards; opt in only on a disposable Linux execution worker.

These tests execute untrusted programs only inside the production sandbox. They
are skipped by default, never replaced by mocked flag checks. An opted-in run
fails when Docker, the image, or required daemon controls are unavailable.
"""

from collections.abc import Callable, Iterator
import json
import os
import shutil
import subprocess
import threading
import time

import pytest

from app.execution.config import ExecutionSettings
from app.execution.models import (
    ExecutionErrorCategory, ExecutionPhase, ExecutionRequest, ExecutionResult, ExecutionStatus,
)


pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(
        os.getenv("EXECUTION_DOCKER_TESTS") != "1",
        reason="Not run: set EXECUTION_DOCKER_TESTS=1 on a disposable Docker worker",
    ),
]


def docker_control(*arguments: str) -> str:
    """Only fixed trusted control operations run on the host, never student code."""
    try:
        completed = subprocess.run(
            ["docker", *arguments], capture_output=True, text=True, timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        pytest.fail("Docker integration prerequisite/control operation unavailable")
    if completed.returncode != 0:
        pytest.fail("Docker integration prerequisite/control operation failed")
    return completed.stdout


@pytest.fixture(scope="module")
def docker_ready() -> None:
    if shutil.which("docker") is None:
        pytest.fail("EXECUTION_DOCKER_TESTS=1 requires the Docker CLI")
    information = json.loads(docker_control("info", "--format", "{{json .}}"))
    assert information["OSType"] == "linux", "Linux containers are required"
    assert str(information.get("CgroupVersion")) == "2", "cgroup v2 is required"
    assert information.get("CgroupDriver") not in (None, "", "none")
    assert any("seccomp" in value for value in information.get("SecurityOptions", []))
    for control in ("MemoryLimit", "SwapLimit", "PidsLimit", "CpuCfsQuota", "CpuCfsPeriod"):
        assert information.get(control) is True, f"Docker must support {control}"
    docker_control("image", "inspect", "grading-cpp:1")


@pytest.fixture
def limits(request: pytest.FixtureRequest) -> ExecutionSettings:
    values = {
        "runtime_timeout_seconds": 1.0,
        "runtime_memory_mb": 64,
        "stdout_limit_bytes": 4096,
        "stderr_limit_bytes": 4096,
        "max_concurrent_jobs": 1,
    }
    values.update(getattr(request, "param", {}))
    return ExecutionSettings(**values)


@pytest.fixture
def execute(
    docker_ready: None, limits: ExecutionSettings,
) -> Iterator[Callable[..., ExecutionResult]]:
    from app.execution.service import ExecutionService

    service = ExecutionService(settings=limits)
    jobs: list[str] = []

    def run(source: str, stdin: str = "", **kwargs) -> ExecutionResult:
        result = service.execute(
            ExecutionRequest(source_code=source, input_data=stdin), **kwargs,
        )
        assert result.job_id is not None
        jobs.append(str(result.job_id))
        # Demonstrates actual removal after every result, including failures.
        remaining = docker_control(
            "ps", "-aq", "--filter", f"label=grading.execution.job={result.job_id}",
        )
        assert remaining.strip() == "", "An execution container was left behind"
        return result

    yield run

    for job in jobs:
        assert not docker_control(
            "ps", "-aq", "--filter", f"label=grading.execution.job={job}",
        ).strip()


def test_valid_program_and_cleanup(execute) -> None:
    result = execute('#include <iostream>\nint main(){std::cout << "hello";}')
    assert result.status == ExecutionStatus.SUCCESS
    assert result.stdout == "hello"
    assert result.stderr == ""
    assert result.exit_code == 0


def test_stdin(execute) -> None:
    result = execute(
        '#include <iostream>\nint main(){int a,b;std::cin>>a>>b;std::cout<<a+b;}',
        "10 20\n",
    )
    assert result.status == ExecutionStatus.SUCCESS
    assert result.stdout == "30"


def test_compile_error_is_student_error(execute, limits) -> None:
    result = execute("int main(){ this_identifier_does_not_exist; }")
    assert result.status == ExecutionStatus.COMPILE_ERROR
    assert "this_identifier_does_not_exist" in result.stderr
    assert len(result.stderr.encode()) <= limits.stderr_limit_bytes
    assert result.compilation_time_ms is not None


@pytest.mark.parametrize("limits", [{"compile_timeout_seconds": 0.01}], indirect=True)
def test_compilation_deadline_is_enforced(execute, limits) -> None:
    # Parsing and compiling these standard headers exceeds this deliberately
    # tiny wall budget. The source is finite and all compiler work stays inside
    # the normal resource-limited compilation container.
    result = execute(
        "#include <regex>\n#include <map>\n"
        "int main(){std::regex expression(\"a+\");return 0;}",
    )
    assert result.status == ExecutionStatus.TIMEOUT
    assert result.phase == ExecutionPhase.COMPILE
    assert result.compilation_time_ms is not None
    assert result.compilation_time_ms <= 1000 * limits.compile_timeout_seconds + 3000


@pytest.mark.parametrize("exit_code", [1, 124, 125, 126, 127, 137])
def test_program_exit_code_cannot_impersonate_docker_or_limits(execute, exit_code) -> None:
    result = execute(f"int main(){{return {exit_code};}}")
    assert result.status == ExecutionStatus.RUNTIME_ERROR
    assert result.exit_code == exit_code


def test_runtime_signal(execute) -> None:
    result = execute("#include <cstdlib>\nint main(){std::abort();}")
    assert result.status == ExecutionStatus.RUNTIME_ERROR


def test_infinite_loop_timeout_and_cleanup(execute, limits) -> None:
    started = time.monotonic()
    result = execute("int main(){for(;;){asm volatile(\"\");}}")
    assert result.status == ExecutionStatus.TIMEOUT
    assert result.execution_time_ms is not None
    assert result.execution_time_ms <= 1000 * limits.runtime_timeout_seconds + 3000
    assert time.monotonic() - started < 60


def test_ignored_sigterm_cannot_defeat_runtime_deadline(execute, limits) -> None:
    result = execute(
        "#include <signal.h>\n#include <unistd.h>\n"
        "int main(){signal(SIGTERM,SIG_IGN);raise(SIGTERM);"
        "write(1,\"ignored\",7);for(;;)pause();}",
    )
    assert result.status == ExecutionStatus.TIMEOUT
    assert result.phase == ExecutionPhase.RUN
    assert result.stdout == "ignored"
    assert result.execution_time_ms is not None
    assert result.execution_time_ms <= 1000 * limits.runtime_timeout_seconds + 3000


@pytest.mark.parametrize("descriptor", [1, 2])
def test_each_output_stream_is_bounded(execute, limits, descriptor) -> None:
    result = execute(
        "#include <unistd.h>\n"
        f"int main(){{char b[8192]={{}};for(;;)write({descriptor},b,sizeof b);}}",
    )
    assert result.status == ExecutionStatus.OUTPUT_LIMIT
    assert len(result.stdout.encode()) <= limits.stdout_limit_bytes
    assert len(result.stderr.encode()) <= limits.stderr_limit_bytes


def test_memory_exhaustion_is_observed_by_cgroup(execute) -> None:
    # Touch every allocated page so virtual overcommit cannot fake this test.
    result = execute(
        "#include <cstdlib>\n#include <vector>\n"
        "int main(){std::vector<void*> blocks;for(;;){"
        "auto p=static_cast<volatile char*>(std::malloc(8*1024*1024));"
        "if(!p)return 42;for(int i=0;i<8*1024*1024;i+=4096)p[i]=1;"
        "blocks.push_back(const_cast<char*>(p));}}",
    )
    assert result.status == ExecutionStatus.MEMORY_LIMIT


def test_network_egress_is_unavailable(execute) -> None:
    result = execute(
        "#include <sys/socket.h>\n#include <arpa/inet.h>\n"
        "#include <fcntl.h>\n#include <cerrno>\n#include <iostream>\n"
        "int main(){int fd=socket(AF_INET,SOCK_STREAM|SOCK_NONBLOCK,0);"
        "if(fd<0){std::cout<<\"blocked\";return 0;}"
        "sockaddr_in a{};a.sin_family=AF_INET;a.sin_port=htons(443);"
        "inet_pton(AF_INET,\"1.1.1.1\",&a.sin_addr);"
        "int r=connect(fd,reinterpret_cast<sockaddr*>(&a),sizeof a);"
        "if(r<0 && (errno==ENETUNREACH||errno==EHOSTUNREACH||errno==EPERM"
        "||errno==EACCES)){std::cout<<\"blocked\";return 0;}return 1;}",
    )
    assert result.status == ExecutionStatus.SUCCESS
    assert result.stdout == "blocked"


def test_filesystem_and_identity_boundary(execute) -> None:
    # Image files such as /etc/passwd remain readable by design. These checks
    # target host/API secrets, write-protected image paths and allowed tmpfs.
    result = execute(
        "#include <unistd.h>\n#include <fcntl.h>\n#include <iostream>\n"
        "int main(){if(getuid()==0)return 1;"
        "const char* forbidden[]={\"/var/run/docker.sock\",\"/app/.env\","
        "\"/proc/1/root/app/.env\",\"/root/.ssh/id_rsa\"};"
        "for(auto p:forbidden)if(access(p,F_OK)==0)return 2;"
        "if(open(\"/etc/grading-write-probe\",O_CREAT|O_WRONLY,0600)>=0)return 3;"
        "int fd=open(\"/workspace/allowed\",O_CREAT|O_WRONLY,0600);"
        "if(fd<0)return 4;close(fd);std::cout<<\"isolated\";}",
    )
    assert result.status == ExecutionStatus.SUCCESS
    assert result.stdout == "isolated"


def test_tmpfs_inode_limit_with_finite_file_creation(execute) -> None:
    # Each container tmpfs permits 1024 inodes. A finite 2048-attempt loop
    # proves ENOSPC occurs without depending on file contents or host storage.
    result = execute(
        "#include <fcntl.h>\n#include <unistd.h>\n#include <cerrno>\n"
        "#include <cstdio>\n#include <iostream>\n"
        "int main(){char path[64];for(int i=0;i<2048;i++){"
        "std::snprintf(path,sizeof path,\"/workspace/inode-%d\",i);"
        "int fd=open(path,O_CREAT|O_EXCL|O_WRONLY,0600);"
        "if(fd<0){if(errno==ENOSPC){std::cout<<\"inode-bound\";return 0;}return 1;}"
        "close(fd);}return 2;}",
    )
    assert result.status == ExecutionStatus.SUCCESS
    assert result.stdout == "inode-bound"


def test_process_limit_with_bounded_fork_attempts(execute) -> None:
    # A finite loop (128 attempts), never a recursive fork bomb. Children only
    # sleep and are reaped; production cgroup and wall watchdog bound the test.
    result = execute(
        "#include <unistd.h>\n#include <signal.h>\n#include <sys/wait.h>\n"
        "#include <cerrno>\n#include <iostream>\n"
        "int main(){pid_t p[128];int n=0;bool blocked=false;"
        "for(int i=0;i<128;i++){pid_t c=fork();"
        "if(c==0){sleep(5);_exit(0);}"
        "if(c<0){blocked=(errno==EAGAIN);break;}p[n++]=c;}"
        "for(int i=0;i<n;i++)kill(p[i],SIGKILL);"
        "while(wait(nullptr)>0){}"
        "if(!blocked)return 1;std::cout<<\"bounded\";}",
    )
    assert result.status == ExecutionStatus.SUCCESS
    assert result.stdout == "bounded"


def test_student_cannot_forge_report_or_signal_supervisor(execute) -> None:
    result = execute(
        "#include <unistd.h>\n#include <fcntl.h>\n#include <signal.h>\n"
        "#include <iostream>\n"
        "int main(){if(getpid()==1)return 1;"
        "if(open(\"/proc/1/fd/1\",O_WRONLY)>=0)return 2;"
        "if(open(\"/proc/1/mem\",O_RDWR)>=0)return 3;"
        "kill(1,SIGSTOP);kill(1,SIGKILL);kill(1,SIGINT);"
        "usleep(20000);std::cout<<\"protected\";}",
    )
    assert result.status == ExecutionStatus.SUCCESS
    assert result.stdout == "protected"


def test_detached_descendant_cannot_outlive_job(execute) -> None:
    result = execute(
        "#include <unistd.h>\n#include <iostream>\n"
        "int main(){pid_t p=fork();if(p<0)return 1;"
        "if(p==0){setsid();for(;;)pause();}std::cout<<\"parent-exited\";}",
    )
    # A retained output pipe may make the descendant trigger the watchdog;
    # either outcome must end with the fixture verifying container removal.
    assert result.status in {ExecutionStatus.SUCCESS, ExecutionStatus.TIMEOUT}


def test_cancellation_removes_container(execute) -> None:
    cancelled = threading.Event()
    timer = threading.Timer(0.5, cancelled.set)
    timer.daemon = True
    timer.start()
    try:
        result = execute("int main(){for(;;){}}", cancel_event=cancelled)
    finally:
        timer.cancel()
    assert result.status == ExecutionStatus.SYSTEM_ERROR
    assert result.error_category == ExecutionErrorCategory.CANCELLED
