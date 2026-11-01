# C++ execution engine

The [Objective Test Grader](objective-grader.md) and
[application authentication](authentication.md) are now implemented. The execution
boundary below remains in use; the dated verification and file manifest record
the original hardening milestone. See the later guides for current integration,
authentication setup and subsequent Docker/PostgreSQL verification.

The original phase hardened execution without changing grading or database
schema. Later milestones added durable grading jobs and account/role tables.
C++17 remains the only implemented language. Real Docker tests were subsequently
run during the objective milestone; deployment-specific isolation validation and
review remain necessary before claiming production readiness.

## Inspection findings

The previous runner used unlimited `capture_output`, writable host bind mounts,
and an outer timeout that killed the Docker CLI without reliably killing its
container. It did not handle missing Docker, bound concurrency or source/input
sizes, validate structured results, or distinguish daemon failures from compiler
failures. Its reported runtime included compilation. The original image was
nonroot but that alone did not enforce the execution boundary. The existing
`backend/test_execution.py` was a script that executed Docker at import time.

The current FastAPI application has no `/execution/cpp` route. None was added.
Assignment and submission routers now require signed user access tokens and
live account/session checks, with ownership and role checks repeated in services.
Students receive only approved public tests and their own submission/score
summaries; reference solutions require assignment staff access. Demo IDs and the
legacy grading operator token grant no application identity or permission.
See the [authentication guide](authentication.md) for the complete policy.

The initial two Alembic revisions are empty, so migrations cannot bootstrap all
tables on a fresh database. The existing configured PostgreSQL database passed
`alembic check`; that does not repair the historical bootstrap problem.

## Architecture and integration boundary

Current implemented flows:

```text
Authenticated student -> eligible published Submission API -> SUBMITTED
Authenticated owner/assigned TA/admin -> development-enabled grading API
  -> durable QUEUED objective run -> separate objective worker

Local CLI / trusted worker caller
  -> ExecutionRequest -> ExecutionService (validation + bounded admission)
  -> DockerSandbox (daemon/image checks)
  -> compile container (unprivileged protected PID1 -> g++)
  -> bounded ELF artifact in worker memory -> remove compile container
  -> fresh runtime container (unprivileged protected PID1 -> executable)
  -> strict PhaseReport + Docker terminal/OOM state
  -> remove runtime container -> ExecutionResult + structured completion log
```

The API persists submissions and authorized grading requests; it never executes
student code and needs no Docker socket. Keep the API process/account/host away
from Docker in production. `ExecutionService` itself accepts no database session
or authentication credential. The separate objective coordinator uses scoped
database access for claims/checkpoints, outside the execution boundary; none of
that session, database configuration or user identity is passed into containers.

The objective worker loads approved snapshots, sends bounded `ExecutionRequest`
values including `submission_id`, compares successful stdout and persists results.
It leaves scores null for `SYSTEM_ERROR`, capacity or cancellation instead of
assigning a zero. Timeout, OOM, output, compile and runtime failures remain
distinguishable. A PostgreSQL-backed durable queue is implemented without a
broker; crash recovery and deployment-wide admission still require operational
policy. One execution request recompiles per test. A future compile-once artifact
cache needs separate integrity, size, ownership and eviction design.

## Enforced controls and their limits

| Control | Enforcement | Limitation / evidence needed |
| --- | --- | --- |
| Identity | Image and Docker create both select UID/GID 10001; supervisor refuses root | No user-namespace remapping is claimed; configure rootless Docker or daemon user remapping separately |
| Network | `--network none`, no published ports | Loopback inside the namespace remains; this does not prevent kernel exploits |
| Filesystem | Read-only image, no host bind mounts, sockets, named volumes or credentials; only per-container tmpfs workspaces | Image libraries and ordinary image files remain readable; this is not a filesystem confidentiality boundary within the image |
| Workspace | `/workspace` 0700, nosuid/nodev, 64 MiB, 1024 inodes; executable because it holds the binary | Data consumes the container's memory budget; limits require supported Linux tmpfs options |
| Scratch | `/tmp` 0700, noexec/nosuid/nodev, 16 MiB, 1024 inodes | Filesystem exhaustion is normally a compiler/runtime error, not a distinct DISK_LIMIT status |
| Privileges | Drop all capabilities, `no-new-privileges`, no privileged mode, retain daemon seccomp | No custom syscall allowlist or mandatory LSM policy is supplied |
| Namespaces | Docker private PID/UTS/mount defaults, explicit private cgroup namespace, IPC none, isolated network | Do not add `--pid host`, `--init`, shared namespaces or a different entrypoint; PID1 is a security assumption |
| Memory/swap | Compile 512 MiB, runtime 256 MiB; memory-swap equals memory | Fail closed on missing controllers/warnings; real cgroup enforcement must be tested on the deployment kernel |
| CPU | `--cpus 1` CFS quota in both stages, plus independent elapsed deadlines | CPU quota limits CPU bandwidth, not exact instruction count or a separately measured CPU-time budget |
| Processes/files | 32 cgroup PIDs (including supervisor/threads); 64 FDs per process; no core dumps; 16 MiB per regular file | PID usage depends on compiler/library behavior; total tmpfs/inode bounds also apply |
| Input | Worker validates source and stdin by UTF-8 byte size before Docker; CLI bounds JSON before parsing; supervisor checks its payload again | Trusted service callers already own their Python input string; ingress/queue providers must also bound message size |
| Output | Supervisor drains two nonblocking pipes with independent retained-byte caps, kills on overflow; host independently bounds both Docker pipes | UTF-8 replacement decoding may expand a raw byte to three serialized UTF-8 bytes; caps refer to captured raw bytes |
| Deadlines | PID1 uses monotonic polling and namespace-wide SIGKILL; every Docker CLI operation has a separate bounded host deadline | An unresponsive daemon/kernel can prevent immediate removal; a missing trustworthy report is SYSTEM_ERROR, not a student timeout |
| Report integrity | Non-dumpable PID1 blocks same-UID procfs FD/ptrace access; children inherit only private pipes; SIGINT is ignored | Depends on Linux PID namespace and procfs behavior; supervisor-integrity integration test is required |
| Artifacts | Compile/run containers share no filesystem; bounded regular ELF file read without following symlinks; framed binary transport | ELF magic is a format sanity check, not proof of binary safety; execution still requires the sandbox |
| Image selection | Immutable enum-to-adapter mapping; local image inspected, pinned to resolved ID per job, `--pull never`; entrypoint/volume metadata checked | Trusted operators must build and protect the image/tag; pin/review base image digests and scan/rebuild packages in production |
| Cleanup | Known unique name before create; `finally` force removal and exact-name absence verification, with two bounded attempts | A timed-out create can finish later; latch worker unavailable and reconcile labels before restarting |
| Admission | Nonblocking semaphore, two jobs per service instance by default, no internal waiting queue | Not a machine-wide quota. Use one shared service per worker and one worker process per dedicated daemon, or add coordinated admission |
| Logs | Structured completion and safe Docker control-failure events; job/submission IDs, language, status, durations, limit and failure category; CLI log rotates at 1 MiB with three backups | Embedding workers must configure a bounded external sink; no source, stdin, program output, CLI arguments or raw Docker exception text is logged |

The supervisor is deliberately namespace PID1, with no Docker init process or
shell in front of it. Linux's namespace-init signal rules prevent children from
killing/stopping it; `PR_SET_DUMPABLE=0` prevents reopening its report descriptor
or modifying its memory. Its monotonic watchdog cannot be disabled by an
attacker's signal handlers. Detached descendants are killed too. These are
explicit assumptions, not substitutes for integration tests.

The daemon must report Linux, cgroup v2, a supported cgroup driver, seccomp,
memory/swap/PID/CPU controller support and no warnings. Unsupported configurations
fail closed rather than silently running without limits. Docker create itself
must accept all namespace/tmpfs/resource options.

References for the kernel/container assumptions:
[Docker resource limits](https://docs.docker.com/engine/containers/resource_constraints/),
[Docker container options](https://docs.docker.com/reference/cli/docker/container/run/),
[Linux PID namespaces](https://man7.org/linux/man-pages/man7/pid_namespaces.7.html),
[procfs descriptor access](https://man7.org/linux/man-pages/man5/proc_pid_fd.5.html),
[process dumpability](https://man7.org/linux/man-pages/man2/PR_SET_DUMPABLE.2const.html).

## Configuration

Settings are validated once per service and frozen. Set these in the **worker's
process environment**. The worker deliberately does not import the database
module or load the API's `.env`; `.env.example` shows example values, but copying
it alone does not export worker settings.

| Environment variable | Default |
| --- | --- |
| `EXECUTION_SOURCE_LIMIT_BYTES` | 65536 |
| `EXECUTION_INPUT_LIMIT_BYTES` | 65536 |
| `EXECUTION_STDOUT_LIMIT_BYTES` | 65536 |
| `EXECUTION_STDERR_LIMIT_BYTES` | 65536 |
| `EXECUTION_ARTIFACT_LIMIT_BYTES` | 16777216 |
| `EXECUTION_COMPILE_TIMEOUT_SECONDS` | 15 |
| `EXECUTION_RUNTIME_TIMEOUT_SECONDS` | 3 |
| `EXECUTION_DOCKER_CONTROL_TIMEOUT_SECONDS` | 5 |
| `EXECUTION_COMPILE_MEMORY_MB` | 512 |
| `EXECUTION_RUNTIME_MEMORY_MB` | 256 |
| `EXECUTION_CPUS` | 1 |
| `EXECUTION_PIDS_LIMIT` | 32 |
| `EXECUTION_WORKSPACE_SIZE_MB` | 64 |
| `EXECUTION_TMP_SIZE_MB` | 16 |
| `EXECUTION_MAX_CONCURRENT_JOBS` | 2 |

Compilation and execution have independent phase deadlines. Host start/attach
allows an additional twice-the-control-timeout for Docker startup and bounded
report transfer. Preflight, inspect, create and each cleanup operation have their
own control deadline. Durations are the isolated supervisor phase, including
payload preparation, not total queue/Docker wall time. The structured log also
includes total request wall duration. Durations may be null on OOM or infrastructure
failure.

CLI logs default to `worker.execution.log`, with at most three 1 MiB backups.
Use `--log-file PATH` to select the sink. Docker execution containers use the
`none` log driver so student output cannot accumulate in Docker's log files.
PostgreSQL Compose logs separately rotate at 10 MiB with three files.

## Stable service contract

`ExecutionRequest` and `ExecutionResult` are Pydantic models. Extra request
fields are rejected. Users cannot supply image names, commands, limits or
container options. The enum includes other application languages, but execution
rejects all except CPP with `SYSTEM_ERROR / UNSUPPORTED_LANGUAGE` before Docker.
Oversized input is `SYSTEM_ERROR / VALIDATION`; capacity and cancellation also
use SYSTEM_ERROR with their own categories. These are not student-code failures.

Local request (`backend/execution/example-request.json` is ready to use):

```json
{
  "language": "CPP",
  "source_code": "#include <iostream>\nint main(){int a,b;std::cin>>a>>b;std::cout<<a+b;}",
  "input_data": "10 20\n",
  "submission_id": "11111111-1111-4111-8111-111111111111"
}
```

These response examples are illustrative, not Docker results observed on this
development machine. Optional fields may be null; the CLI serializes all fields.

Successful execution:

```json
{
  "status": "SUCCESS", "stdout": "30", "stderr": "", "exit_code": 0,
  "execution_time_ms": 12, "compilation_time_ms": 250,
  "error_category": null, "error": null, "phase": "RUN",
  "job_id": "22222222-2222-4222-8222-222222222222"
}
```

Compilation failure (for `int main(){missing;}`):

```json
{
  "status": "COMPILE_ERROR", "stdout": "",
  "stderr": "/workspace/main.cpp:1:12: error: 'missing' was not declared in this scope\n",
  "exit_code": 1, "execution_time_ms": null, "compilation_time_ms": 18,
  "error_category": "COMPILE", "error": "Compilation failed.", "phase": "COMPILE",
  "job_id": "22222222-2222-4222-8222-222222222222"
}
```

Runtime timeout (for `int main(){for(;;){}}`):

```json
{
  "status": "TIMEOUT", "stdout": "", "stderr": "", "exit_code": -9,
  "execution_time_ms": 3002, "compilation_time_ms": 80,
  "error_category": "TIMEOUT", "error": "The phase exceeded its time limit.", "phase": "RUN",
  "job_id": "22222222-2222-4222-8222-222222222222"
}
```

Infrastructure failure (for missing Docker/image, malformed protocol or daemon
failure; the same valid request can produce this response):

```json
{
  "status": "SYSTEM_ERROR", "stdout": "", "stderr": "", "exit_code": null,
  "execution_time_ms": null, "compilation_time_ms": null,
  "error_category": "INFRASTRUCTURE",
  "error": "Execution infrastructure is unavailable; retry later.", "phase": null,
  "job_id": "22222222-2222-4222-8222-222222222222"
}
```

`RUNTIME_ERROR` preserves the actual child exit code, including 124, 125, 126,
127 and 137; those values alone never mean timeout, Docker failure or OOM.
Negative child codes indicate signals. `TIMEOUT` requires the trusted supervisor's
deadline outcome. `MEMORY_LIMIT` requires Docker's positive `OOMKilled` state;
allocation refusal/bad_alloc without confirmed OOM remains a normal runtime or
compile error. `OUTPUT_LIMIT` preserves each captured stream prefix. The
supervisor's strict private version-1 report validates status/phase/exit/category,
base64 framing and byte sizes; malformed results become SYSTEM_ERROR. Raw Docker
stderr, exceptions, host paths and tracebacks are not returned. Compiler diagnostics
come only from the isolated compiler and use image/workspace paths.

No execution HTTP request example is provided because `POST /execution/cpp`
returns 404, even in development. Use the local CLI, which requires local
worker/Docker privileges, or the service from a trusted worker.

## Exact setup and verification commands

From the repository root, PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend/requirements-dev.txt
docker build --pull -f backend/execution/cpp/DockerFile -t grading-cpp:1 backend/execution/cpp
```

The existing filename is `DockerFile` (capital F); pass it explicitly on Linux.
Use a dedicated disposable Linux Docker worker/VM with cgroup v2 and seccomp.
Docker Desktop Linux containers can be used for development if the daemon
reports and actually enforces the required controls. An image is built once;
there are no downloads or installs per submission.

API/database configuration (do not overwrite an existing `.env`):

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

Edit `.env`: set a strong `POSTGRES_PASSWORD` and a matching `DATABASE_URL`,
URL-encoding the password in the URL. For an existing PostgreSQL data volume,
use its current credentials; changing the environment does not rotate an existing
database password. Compose now requires a password and binds only loopback.
For API access, also configure `AUTH_SECRET_KEY` and provision users following
the [authentication guide](authentication.md). Keep secrets and login responses
out of terminal output. The standalone execution CLI does not require a user JWT
or signing secret; its access boundary is the operator's local worker privileges.

```powershell
docker compose up -d postgres
Set-Location backend
python -m alembic current
python -m alembic check
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Use the existing initialized database. `alembic upgrade head` is appropriate for
an existing compatible database when migrations are pending, but does **not**
bootstrap a fresh database with this repository's incomplete historical baseline.
Repair/review that baseline in a separate database milestone; do not run
`Base.metadata.create_all` as a hidden migration replacement.

In another terminal, activate the same venv and enter `backend`:

```powershell
$env:EXECUTION_RUNTIME_TIMEOUT_SECONDS = "3"
$env:EXECUTION_MAX_CONCURRENT_JOBS = "2"
python -m app.execution.cli --request execution/example-request.json
python -m pytest -q
$env:EXECUTION_DOCKER_TESTS = "1"
python -m pytest -m docker -q
Remove-Item Env:EXECUTION_DOCKER_TESTS
```

CLI exit codes: 0 success, 1 student-code failure/limit, 2 validation, capacity,
cancellation or infrastructure failure. Ctrl+C/SIGTERM requests cooperative
cancellation; every active container is force-removed in finally. The old
`python test_execution.py` remains a guarded local convenience script and no
longer runs during test collection.

Linux equivalents for the worker (repository root, configured Docker daemon):

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r backend/requirements-dev.txt
docker build --pull -f backend/execution/cpp/DockerFile -t grading-cpp:1 backend/execution/cpp
cd backend
EXECUTION_RUNTIME_TIMEOUT_SECONDS=3 python -m app.execution.cli --request execution/example-request.json
python -m pytest -q
EXECUTION_DOCKER_TESTS=1 python -m pytest -m docker -q
```

An explicitly opted-in Docker test run **fails** if Docker, the image, or the
required kernel/controller prerequisites are missing; it does not turn an
unavailable safeguard into a passing test. Run resource attack tests only on
the disposable worker. The process test uses a finite 128-attempt fork loop,
never a recursive host fork bomb. Inode exhaustion is a finite 2048-file loop.

## Cleanup and operational recovery

Containers carry `grading.execution.managed=true` and
`grading.execution.job=<UUID>` labels. Every normal success/failure, timeout,
output event and cancellation goes through forced removal and daemon-confirmed
absence. A cleanup failure, or ambiguous completion of Docker create, trips a
sticky in-memory circuit. Already running jobs still clean up; no subsequent
phase/job should start through that service. Restarting the worker discards the
latch, so reconcile containers **first**. Separate CLI invocations are separate
workers and do not share a latch or concurrency semaphore.

List containers on the dedicated daemon:

```sh
docker ps -a --filter label=grading.execution.managed=true
```

Stop admissions, restore daemon connectivity, inspect the job labels, force-remove
the confirmed execution containers by ID, verify absence and then restart the
worker. Do not blanket-prune unrelated containers. A crashed worker, SIGKILL,
host power loss or disconnected daemon cannot guarantee host-side finally runs.
The in-container watchdog limits running student processes while the kernel
remains functional, but stopped containers still need a deployment reaper. An
uncertain create may finish late, so reconciliation must include late arrivals.

## Historical execution-milestone verification (2026-10-22)

These results and migration identifiers describe the original execution phase,
before the objective grader and authentication changes. They are historical,
not the current application-wide test count or migration head. Later real
Docker/PostgreSQL results are recorded in the [objective guide](objective-grader.md),
and current authentication verification is in the [authentication guide](authentication.md).

The complete backend test suite, including existing API regression behavior,
runs without Docker. Unit tests of Docker options establish configured intent;
they do not demonstrate kernel isolation. Real helper subprocess tests demonstrate
bounded host transport/timeout/cancellation using trusted fixed snippets, never
student programs on the host.

| Check | Actual result |
| --- | --- |
| Full backend pytest suite | **156 passed, 27 skipped**; one Starlette/httpx dependency deprecation warning |
| Assignment/submission/related API regression tests | Passed against isolated SQLite with FK enforcement and dependency override; no production DB writes |
| Execution service and mocked Docker boundary | Passed; malformed metadata, source/stdin caps, semaphore concurrency, cancellation, statuses and cleanup faults covered |
| Trusted host transport | Passed binary stdin, both streams, floods, deadlines, unread input, missing executable and cancellation |
| Real Docker execution/isolation | **Not run: Docker CLI unavailable**; 23 opt-in integration cases skipped |
| POSIX nofollow artifact unit cases | **Not run on Windows**; four skipped |
| Live PostgreSQL `alembic heads` | `6a87b9acee87` |
| Live PostgreSQL `alembic check` | Passed: `No new upgrade operations detected.` |
| Model/migration diff | No database model or migration files changed |
| Python syntax and diff whitespace | Passed |
| Actual CLI without Docker | Returned sanitized SYSTEM_ERROR / INFRASTRUCTURE, with no host paths or exception details |
| Execution image build / Compose validation | **Not run: Docker CLI unavailable** |

The real Docker suite covers valid/stdin code, compile errors, abnormal exit and
signals, infinite loops, signal-resistant loops, touched memory exhaustion,
stdout/stderr floods, compile deadline, network egress, image/filesystem identity,
finite PID/inode exhaustion, supervisor-report spoofing/signals, detached
descendants, cancellation, and actual absence of labelled containers after
every result. They had not run at this historical checkpoint; subsequent real
development results are recorded in the objective guide. Deployment kernels and
daemon configurations still need their own validation.

## Remaining production work

Docker shares the worker's Linux kernel. A compiler, runtime, kernel, Docker or
supervisor vulnerability can cross this boundary. Use disposable workers on a
separate host/VM without API/database secrets, patch the kernel/runtime, constrain
the daemon itself, review a restrictive seccomp/AppArmor/SELinux policy, and
consider gVisor/Kata or microVM isolation for hostile multi-tenant workloads.

The runtime image still contains compiler/Python tooling required for the
supervisor/compiler pipeline; remove unnecessary runtime tooling with separately
reviewed images in a later image-hardening pass. Pin base image/package supply
chains and expected execution-image digests in deployment, scan and rebuild.

Machine-wide admission, persistent job recovery, startup/periodic label reaping,
daemon quotas, metrics/alerts and a cancellation-aware queue are deployment work.
Run real isolation tests on the deployment worker before enabling execution.
Authentication and service-level assignment authorization are implemented; apply
the [authentication deployment controls](authentication.md), including secret
management, HTTPS and login rate limiting. The historical initial-migration
bootstrap issue still needs repair before provisioning a fresh database.
Submission API body-size limits also need an ingress policy before public
exposure; the worker's source cap does not protect the API's earlier
submission-storage request.

## Historical execution-milestone file manifest

The list below records only the original execution hardening. Later grading and
authentication changes have their own manifests in the linked guides.

Modified:

- `.gitignore`: ignores local test dependencies and rotated worker logs.
- `README.md`: links this guide, preserving the existing title.
- `docker-compose.yml`: removes hardcoded password, binds PostgreSQL to loopback, bounds its logs.
- `backend/app/execution/models.py`: typed public contract and strict private wire report.
- `backend/app/execution/languages.py`: immutable enum-to-C++ adapter.
- `backend/app/execution/sandbox.py`: bounded Docker orchestration and cleanup.
- `backend/execution/cpp/DockerFile`: dedicated nonroot image and trusted entrypoint.
- `backend/test_execution.py`: guards the original local example from import-time execution.

Created:

- `.env.example`
- `docs/execution-engine.md`
- `backend/app/execution/config.py`
- `backend/app/execution/process.py`
- `backend/app/execution/service.py`
- `backend/app/execution/cli.py`
- `backend/execution/cpp/phase_runner.py`
- `backend/execution/example-request.json`
- `backend/pytest.ini`
- `backend/requirements-dev.txt`
- `backend/tests/conftest.py`
- `backend/tests/test_api_regression.py`
- `backend/tests/test_execution_service.py`
- `backend/tests/test_execution_sandbox.py`
- `backend/tests/test_execution_process.py`
- `backend/tests/test_execution_cli.py`
- `backend/tests/test_execution_integration.py`
- `backend/tests/test_phase_runner.py`

No files were removed in that milestone. It added no database models, migrations
or grading pipeline; subsequent objective and authentication milestones did.
