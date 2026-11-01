# Objective Test Grader

This milestone implements durable objective grading through the existing secure
execution service. It does not implement LLM assessment, combine rubric scores,
or finalize an assignment grade.

## Architecture

```text
Authenticated assignment owner, assigned TA, or administrator
  -> POST .../grade/objective
  -> validate live account/session, role and assignment scope
  -> validate assignment/submission/test configuration
  -> snapshot APPROVED tests + source hash + versions/limits
  -> persist QUEUED run and PENDING per-test rows -> HTTP 202

Separate objective worker process
  -> claim QUEUED run in PostgreSQL -> commit RUNNING claim
  -> validate source hash, language, versions and execution settings
  -> for each snapshot: ExecutionService -> Docker compile/run containers
  -> compare raw stdout -> persist bounded evidence/checkpoint
  -> publish score only when every test has a student outcome
  -> preserve history and leave final assignment grading unresolved

GET .../grading-runs/{run_id} or .../grading-runs/latest
  -> authorize assignment/submission access -> retrieve persisted run
  -> staff: redact hidden-test evidence; student owner: summary with results=[]
```

The API queues work only. It never calls Docker, runs student code, launches
background execution tasks, or requires Docker access. The worker is a separate
CLI process with its own SQLAlchemy session and one cached execution service.
Keeping that execution service alive preserves its cleanup-failure circuit
across jobs. No Redis, Celery, Kafka, or external broker was introduced.

The security prerequisite was reviewed: the existing service has separate
compile/run containers, nonroot PID1 supervision, no network/host mounts, bounded
input/output, CPU/memory/swap/PID/tmpfs limits, independent deadlines, strict
reports, and verified forced removal. Docker/transport failures remain
infrastructure errors. See [execution controls and limitations](execution-engine.md).
Real isolation tests are distinct from mocked Docker flag tests.

## Schema and migration

Revision **`9c2e1f7a4b60`**, following `6a87b9acee87`, adds:

- `objective_grading_runs`: submission/assignment IDs, run lifecycle, counts,
  nullable `Numeric(6,3)` correctness, safe failure classification, queue/start/end
  timestamps, grading/comparison/execution versions, source SHA-256, language,
  previous submission state, and a non-secret execution-settings snapshot.
- `objective_test_results`: ordered approved-test snapshots (including original
  visibility, stdin and expected output), original test UUID, outcome, bounded
  sanitized actual output/stderr, truncation indicators, raw decoded-output hash,
  exit/timing/category fields, and execution job UUID.

Source code remains only on the existing submission. Its hash is checked before
execution; the submission API has no source-edit operation. If someone edits the
database source, language or relevant configuration, the worker refuses to grade
the queued snapshot and leaves a null score. Exact source replay still requires
retaining the corresponding immutable submission.

Editing/deleting an original test does not change a queued run. The live test FK
becomes null on deletion; the snapshot UUID, input, expected output and visibility
remain. Deleting a submission/assignment cascades its objective history, following
the existing ownership lifecycle. No old tables, enum values, or data are rewritten.

Checks enforce nonnegative/bounded counts, score range, bounded evidence, and a
non-null score only for COMPLETED runs with all tests accounted for. A partial
unique index prevents more than one QUEUED/RUNNING run per submission. Both ORM
definitions and the Alembic migration carry this invariant.

## Approval, comparison and scoring policies

Only `APPROVED` tests belonging to the assignment are selected, including both
PUBLIC and HIDDEN cases. DRAFT, VALIDATED and REJECTED tests are excluded. Order
is `(created_at ASC, id ASC)` and is preserved as one-based snapshot positions.
The existing test schema has no weights, so every approved test has equal weight.

Policy **`text-v1`**:

1. Convert CRLF and bare CR to LF.
2. Remove ASCII space, tab, LF, CR, vertical tab and form feed only at the end
   of the entire string.
3. Compare the resulting strings exactly.

Trailing newlines and final blank lines are ignored. Leading spaces, internal
blank lines, indentation, internal tabs and per-line trailing spaces are
significant. Unicode whitespace, punctuation, case and numeric formatting are
unchanged. There is no implicit token matching or floating-point tolerance.

For a complete run:

```text
correctness_percentage = passed_tests / total_tests * 100
```

The result is rounded to three decimal places with decimal ROUND_HALF_UP.
All-pass is 100.000; all student failures is 0.000; two of three is 66.667.
This is an objective correctness percentage, not a rubric/final assignment score.

An execution SUCCESS becomes PASSED or WRONG_ANSWER. COMPILE_ERROR,
RUNTIME_ERROR, TIMEOUT, MEMORY_LIMIT and OUTPUT_LIMIT are student outcomes and
count as failed tests. Tests continue individually after student errors, including
compile errors; this phase intentionally recompiles per test through the existing
service rather than introducing an unaudited cross-test artifact cache.

No approved tests produces a persisted **NOT_GRADEABLE** run with zero counts,
null score and a configuration explanation. It does not change submission status
or invoke execution. Infrastructure/malformed results/cancellation stop the run;
unattempted tests become SKIPPED, infrastructure tests do not increment failed
counts, and no proportional score is published from a partial suite.

## Lifecycle and concurrency

Assignments must be PUBLISHED or CLOSED, and submission/assignment languages must
match an implemented execution adapter (currently CPP only). Queueable submissions
are SUBMITTED or FAILED. Active or already-finalized submissions return 409.

| Event | Run state | Submission state |
| --- | --- | --- |
| Accepted request with approved tests | QUEUED | QUEUED |
| Worker commits a claim | RUNNING | GRADING, only from QUEUED |
| Complete objective suite | COMPLETED | SUBMITTED, awaiting later grading |
| Infrastructure/configuration/internal failure | INCOMPLETE | FAILED, retryable |
| Cooperative cancellation/interruption | CANCELLED | FAILED, retryable |
| No approved tests | NOT_GRADEABLE | Unchanged |

The worker never sets GRADED or assigns a final grade. It tracks whether it
successfully acquired the submission transition, and only conditionally releases
its own GRADING state. Manual GRADED/ESCALATED transitions are preserved.
Retries create new runs; a failed retry cannot overwrite an earlier successful
run. The latest endpoint means latest attempt, even when incomplete; use a stored
successful run ID when that distinction matters.

Enqueue locks the submission row, and the database partial unique index is the
independent overlap safeguard. Workers use `FOR UPDATE SKIP LOCKED` plus a
conditional QUEUED update, commit the claim, and then execute. No transaction or
row lock remains open during Docker calls; evidence is committed after each test.
Duplicate processing of an already running/terminal run does nothing.
PostgreSQL documents SKIP LOCKED for queue consumers, while SQLAlchemy commits
expire ORM state; the worker copies a plain snapshot before releasing its read
transaction. References: [PostgreSQL SELECT locking](https://www.postgresql.org/docs/current/sql-select.html),
[SQLAlchemy session transactions](https://docs.sqlalchemy.org/en/20/orm/session_basics.html).

Concurrency limits in the execution service are per process, not machine-wide.
Use one objective worker per dedicated execution daemon unless aggregate resource
admission is provided. Database locking prevents duplicate grading, not Docker
host oversubscription by multiple processes.

## API and authorization

New routes:

| Method | Route | Result |
| --- | --- | --- |
| POST | `/assignments/{assignment_id}/submissions/{submission_id}/grade/objective` | 202 persisted queued/non-gradeable run |
| GET | `/assignments/{assignment_id}/submissions/{submission_id}/grading-runs/{run_id}` | 200 run and ordered results |
| GET | `/assignments/{assignment_id}/submissions/{submission_id}/grading-runs/latest` | 200 latest attempt |

The request is empty JSON `{}` (or no body). Extra fields, including caller-picked
tests, Docker images, limits or scores, are rejected. UUID/body errors and unsupported
or oversized grading inputs return 422; missing or out-of-scope records and wrong
assignment/submission parent paths return 404; invalid lifecycle/overlap returns
409; persistence/configuration unavailability returns a sanitized 503. Error
bodies use `detail`, containing a safe message or sanitized validation details.

These routes require a signed user access token issued by `POST /auth/login`.
Every request checks the live account, session expiry/revocation, role and
assignment scope; services repeat the authorization checks. Assignment owners,
explicitly assigned TAs and administrators may enqueue grading. Students cannot
enqueue it; the denial precedes looking up a submission, so its existence is not
disclosed. Staff outside the assignment scope receive 404.

The API remains disabled by default. Authenticated requests receive 404 while it
is disabled, and only explicit development mode can enable it. Missing/invalid
user authentication returns 401 when authentication is configured; an unavailable
authentication configuration returns a sanitized 503. Insufficient permission
within a visible scope returns 403. Production mode cannot enable this
development API. `OBJECTIVE_GRADING_API_TOKEN` is deprecated and ignored: it
grants no user identity or permission. There are no demo-ID authorization paths.
See [authentication setup and the permission matrix](authentication.md).

HIDDEN snapshots are always redacted by the response schema, including for
authorized staff: stdin, expected output, actual output, stderr and diagnostic
messages are null. Outcome, counts and safe timing/category metadata remain visible.
Original visibility is used, so a later test visibility edit cannot reveal old
hidden evidence. Raw output hashes and private limits/source hashes are not
exposed. PostgreSQL stores full private snapshots for trusted audit access.
Students can retrieve only their own submission's safe run summary; its
`results` list is empty, including public-test evidence. Another student's
submission/run returns 404.

Example request (PowerShell, after provisioning an authorized account and
configuring authentication as described in the [authentication guide](authentication.md)).
The login response and access token are assigned to variables rather than printed:

```powershell
$credential = Get-Credential -Message 'Sign in as the assignment owner, an assigned TA, or an administrator'
$loginBody = @{
  email = $credential.UserName
  password = $credential.GetNetworkCredential().Password
} | ConvertTo-Json -Compress
$login = Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/auth/login' -ContentType 'application/json' -Body $loginBody
$accessToken = $login.access_token
Remove-Variable credential, loginBody, login
$headers = @{ Authorization = "Bearer $accessToken" }
$base = "http://127.0.0.1:8000/assignments/$assignmentId/submissions/$submissionId"
$run = Invoke-RestMethod -Method Post -Uri "$base/grade/objective" -Headers $headers -ContentType 'application/json' -Body '{}'
Invoke-RestMethod -Uri "$base/grading-runs/$($run.id)" -Headers $headers
```

Representative response fragments (illustrative IDs/values; actual responses
also contain timestamps, versions, language and per-test identifiers):

```json
{
  "id": "22222222-2222-4222-8222-222222222222",
  "status": "QUEUED", "total_tests": 2, "passed_tests": 0,
  "failed_tests": 0, "correctness_percentage": null,
  "results": [{"position": 1, "outcome": "PENDING", "visibility": "HIDDEN",
    "input_data": null, "expected_output": null, "actual_output": null,
    "stderr": null, "details_redacted": true}]
}
```

```json
{
  "status": "COMPLETED", "total_tests": 2, "passed_tests": 1,
  "failed_tests": 1, "correctness_percentage": 50.0,
  "error_category": null, "error_message": null
}
```

```json
{
  "status": "INCOMPLETE", "total_tests": 2, "passed_tests": 1,
  "failed_tests": 0, "correctness_percentage": null,
  "error_category": "INFRASTRUCTURE",
  "error_message": "Execution infrastructure could not complete the test suite; retry later."
}
```

Existing assignment/rubric/test-case/reference/submission paths and valid payloads
are preserved, with authentication and service-level ownership checks now
required throughout. Students see only PUBLIC+APPROVED test cases; hidden and
unapproved detail requests return 404. Reference solutions require assignment
staff access. Only the owner instructor or an administrator may modify content
or validate/approve/reject tests. See the [authentication guide](authentication.md)
for the full endpoint and role policy.

## Configuration and operation

Objective grading itself added no dependency; the current requirements also
include the authentication libraries. Use the existing Python environment and
Docker image, and complete [authentication configuration and account setup](authentication.md)
before making API requests. From the repository root, PowerShell:

```powershell
if (-not (Test-Path .venv\Scripts\python.exe)) { python -m venv .venv }
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend/requirements-dev.txt
docker compose up -d postgres
docker build --pull -f backend/execution/cpp/DockerFile -t grading-cpp:1 backend/execution/cpp
Set-Location backend
python -m alembic upgrade head
python -m alembic current
python -m alembic check
```

Use the configured initialized development database and preserve its data volume.
The old initial migrations remain incomplete for a fresh empty database; this
new migration assumes the legacy tables already exist. No production code uses
`Base.metadata.create_all()` as a migration replacement. Unit tests use isolated
SQLite fixtures; the live integration suite requires actual Alembic-created tables.

Enable the API only in the local backend's environment. Configure `AUTH_SECRET_KEY`
and provision user accounts according to the authentication guide first:

```powershell
$env:OBJECTIVE_GRADING_ENVIRONMENT = 'development'
$env:OBJECTIVE_GRADING_API_ENABLED = 'true'
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Keep signing secrets and user access tokens private. These settings can instead
be placed in the ignored local `.env`, followed by a backend restart;
`.env.example` keeps objective HTTP access disabled. The worker does not need a
user JWT, signing secret or legacy operator token. Supply only its database and
execution configuration in a separate deployment. Docker container environment
remains an explicit whitelist of resource limits, never database credentials or
authentication secrets.

Start a separate worker terminal from `backend` (same database and execution
limits as the enqueueing API):

```powershell
python -m app.grading.worker
# Or process at most one queued job and exit:
python -m app.grading.worker --once
# Or process one specific queued job:
python -m app.grading.worker --run-id YOUR_QUEUED_RUN_UUID
```

Docker must be on the worker's PATH. On this installation its CLI is under
`C:\Users\trish\AppData\Local\Programs\DockerDesktop\resources\bin`; if needed,
prepend that directory to the worker terminal's PATH. The worker is intentionally
not launched inside Uvicorn or automatically by an HTTP request.

| Setting | Default |
| --- | --- |
| `OBJECTIVE_GRADING_API_ENABLED` | false |
| `OBJECTIVE_GRADING_ENVIRONMENT` | development |
| `OBJECTIVE_GRADING_API_TOKEN` | Deprecated and ignored; grants no identity or permission |
| `OBJECTIVE_GRADING_MAX_TESTS` | 100 |
| `OBJECTIVE_GRADING_MAX_SNAPSHOT_BYTES` | 8388608 total input/expected UTF-8 bytes |
| `OBJECTIVE_GRADING_EVIDENCE_LIMIT_BYTES` | 65536 per persisted stdout/stderr |
| `OBJECTIVE_GRADING_WORKER_POLL_SECONDS` | 2 |

Source/stdin/expected-output limits additionally follow the frozen
`ExecutionSettings` snapshot (default 64 KiB each). The CLI polls the database,
exits after SIGINT/SIGTERM cancellation, and writes only run ID/status/score to
stdout. Continuous mode continues after an incomplete job; persistent database
failure exits nonzero. In one-shot mode, no queued work is a successful no-op;
an incomplete/non-gradeable/cancelled processed job exits 2.

`objective.execution.log` rotates at 1 MiB with three backups. Structured events
include run/submission/test/execution IDs, outcomes, timing, score counts and
versions. Source, stdin, expected/actual output, stderr, credentials and exception
tracebacks are not logged. Embedded worker users must configure a bounded sink.

Raw output is compared before evidence sanitization/truncation. Persisted output
replaces NUL/control characters (except CR/LF/tab) and DEL, truncates by UTF-8 byte
count, records truncation flags, and hashes the original decoded stdout. This
prevents PostgreSQL NUL failures and stored terminal controls while retaining an
audit fingerprint. It does not implement an HTML renderer; future UIs must render
student strings as text.

## Failure and crash recovery

Execution SYSTEM_ERROR, capacity, validation/configuration mismatch, malformed
results and unsupported worker behavior never become student failures. Available
completed evidence stays persisted, remaining tests are SKIPPED, and the score
is null. A persistence failure rolls back the failed checkpoint and attempts an
INCOMPLETE finish; if PostgreSQL remains unavailable, the run stays RUNNING with
no score and the caller receives a safe recovery error.
SKIPPED means no committed result is available; after a failed database checkpoint
it does not assert that execution never began.

SIGINT/SIGTERM are cooperative: the current execution is cancelled and cleaned up
through the existing service, and the run becomes CANCELLED. SIGKILL, host crashes
or permanently unavailable storage cannot guarantee finalization. Stale RUNNING
rows deliberately continue blocking overlap, with no automatic requeue/lease
expiry. Stop the old worker, reconcile labelled Docker containers, and have a
trusted operator transactionally mark the stale run INCOMPLETE (score null), mark
unexecuted evidence SKIPPED and release its still-QUEUED/GRADING submission to
FAILED before enqueuing a **new run**. Do not reset the old row to QUEUED while its
worker may still be alive. Status alone is not a distributed fencing generation.
An audited recovery command/lease protocol is future operational work.

Historical successful runs remain available by ID after retry failures.

## Historical objective-milestone verification (2026-10-23, before authentication)

The commands, counts and migration revision below record the objective-grader
milestone. They are not the current authentication verification report or current
migration head; see [authentication verification](authentication.md).

Executed migration commands from `backend`:

```powershell
python -m alembic current
python -m alembic upgrade head
python -m alembic check
```

The configured PostgreSQL database upgraded from `6a87b9acee87` to
`9c2e1f7a4b60`; Alembic then reported **No new upgrade operations detected**.
The migration was also checked by isolated SQLite upgrade/metadata comparison/
downgrade tests, without replacing the production migration workflow.

The initial unit/regression run without external prerequisites used:

```powershell
python -m pytest backend -c backend/pytest.ini -q
```

Result: **230 passed, 35 skipped** (23 real sandbox cases, eight objective
PostgreSQL/Docker cases, four POSIX-only artifact cases). This includes 74 new
comparison/grading/API unit tests and all existing endpoint/execution regressions.

Live prerequisites are opt-in:

```powershell
$env:EXECUTION_DOCKER_TESTS = '1'
$env:OBJECTIVE_GRADING_INTEGRATION_TESTS = '1'
$objectiveTestTemp = Join-Path $env:TEMP ('codex-objective-final-' + [guid]::NewGuid().ToString('N'))
python -m pytest backend/tests -c backend/pytest.ini -p no:cacheprovider --basetemp $objectiveTestTemp --log-level=INFO -q
```

Use a new unique basetemp path; pytest owns that directory. In this environment
the command also prepends the installed Docker CLI to PATH and the workspace's
`.test-deps` and `backend` to PYTHONPATH. Test-only packages are isolated in that
ignored workspace directory.

**Final full live run: 266 passed, four skipped, one warning in 120.40 seconds.**
All 23 real sandbox cases and all eight PostgreSQL/objective integration cases
passed. The four skipped cases require POSIX host artifact APIs unavailable on
Windows. The final run also includes five additional configuration/logging
hardening tests that passed separately before the full run.

The earlier live run already passed **259 tests**, including all 23 real sandbox
and eight objective PostgreSQL/Docker cases; four POSIX cases skipped, and two
CLI tests hit Windows temp-fixture permission errors before their bodies ran.
A new temp directory addressed those environment errors. An intermediate full
run then reported two preflight infrastructure failures in existing sandbox
stdin/compile-error cases; both passed on targeted rerun (2 passed in 7.67s),
and the final full run passed. Safe operation-level failure telemetry was added;
no student limits, Docker checks, or timeouts were weakened and no automatic
retry was introduced. One Starlette/httpx dependency deprecation warning remains.

PostgreSQL integration fixtures insert and delete only fresh test-owned UUID
records. They verify concurrent enqueue/claim, actual partial-index rejection for
QUEUED and RUNNING rows, source mutation refusal, real all-pass grading, test
edit/delete snapshot stability, and compile-error zero scoring. Opt-in tests fail
when required infrastructure is unavailable instead of silently passing mocked
controls. Four host POSIX `O_NOFOLLOW` artifact checks remain not run on Windows.

## Remaining limits and next milestone

Authentication and assignment authorization are now implemented; see the
[authentication deployment limitations](authentication.md). This remains an
execution baseline rather than a claim of production-grade isolation.
Docker shares a kernel; use a dedicated patched execution host/VM, restricted
daemon access, reviewed image digests/versions, stronger runtime isolation where
needed, and coordinated machine-wide admission. The objective worker currently
needs scoped database access and should use a dedicated least-privilege role.
Execution source/config hashes detect changes but are not cryptographic signing
or tamper-proof audit storage. Historical job retention and crash recovery need
deployment policy.

Next, add the LLM assessment component using a separate versioned result model
behind the authenticated role and assignment boundaries. The eventual
confidence engine should combine the objective percentage with rubric-based LLM
signals, handle incomplete infrastructure runs through retry/escalation, and only
then finalize the submission's overall grade.

## Historical objective-milestone file manifest

This records the original objective-grader changes. Later authentication changes
and migrations are listed in the [authentication guide](authentication.md).

Created:

- `backend/app/grading/__init__.py`
- `backend/app/grading/config.py`
- `backend/app/grading/enums.py`
- `backend/app/grading/errors.py`
- `backend/app/grading/comparison.py`
- `backend/app/grading/worker.py`
- `backend/app/models/objective_grading.py`
- `backend/app/schemas/objective_grading.py`
- `backend/app/services/objective_grading_service.py`
- `backend/app/routers/objective_grading.py`
- `backend/alembic/versions/9c2e1f7a4b60_objective_grading.py`
- `backend/tests/test_objective_grading.py`
- `backend/tests/test_objective_api.py`
- `backend/tests/test_objective_configuration.py`
- `backend/tests/test_objective_integration.py`
- `docs/objective-grader.md`

Modified:

- `backend/app/main.py`: grading router and safe domain-error handling.
- `backend/app/models/__init__.py`: exports new ORM models for Alembic metadata.
- `backend/tests/conftest.py`: shared isolated objective fixtures.
- `backend/pytest.ini`: excludes production modules and hidden cache directories.
- `backend/app/execution/sandbox.py`: safe Docker control-failure telemetry (fixed operation, timing and flags only).
- `.env.example`: disabled-by-default grading settings.
- `README.md` and `docs/execution-engine.md`: links/current milestone context.

No files were removed; existing in-progress execution hardening files were retained.
