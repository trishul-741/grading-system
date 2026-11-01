# Authentication and role authorization

All assignment, rubric, test, reference-solution, submission, and objective-grading
routes now require an authenticated account. Existing resource paths and valid
payload fields are preserved. Ownership comes from the authenticated database
account; request fields cannot select a creator, student, role, or grading scope.
The LLM grader is outside this milestone.

## Authentication contract

Passwords use Argon2id through `pwdlib[argon2]`. Tokens use PyJWT with a fixed
HS256 algorithm and a required environment secret; no custom cryptography or
fallback key is used. These established libraries follow the approach in the
[FastAPI security guide](https://fastapi.tiangolo.com/tutorial/security/oauth2-jwt/).

- `POST /auth/login`: JSON `email` and `password`; returns `access_token`,
  `token_type="bearer"`, and `expires_in` seconds. Email is normalized to lowercase.
  Unknown email, incorrect password, invalid legacy hash, disabled account, and
  invalid role produce the same generic 401. A dummy Argon2 verification covers
  unknown accounts and unsupported hashes. No public registration is exposed.
- `GET /auth/me`: authenticated account UUID, name, email, role, and active
  status. Password hashes are never serialized.
- `POST /auth/logout`: revokes the current persisted session and returns 204.
  Subsequent use of that token returns 401.

Access tokens expire after 15 minutes by default (configurable from 1 to 60).
Required claims are UUID `sub` and `jti`, exact issuer/audience, integer
`iat`/`nbf`/`exp`, and `type="access"`. Signature, algorithm, expiry, claim types,
time ordering, and maximum lifetime are checked. Unexpected claims/headers are
rejected. No user role is encoded or trusted. Claim validation uses
[PyJWT's verification API](https://pyjwt.readthedocs.io/en/latest/api.html).

Each token also needs an unexpired, unrevoked `auth_sessions` row matching its
user, session UUID, and expiration. Every protected request reloads account status
and role. Deactivation and role changes therefore affect existing tokens on their
next request. Password resets revoke all of that user's sessions. Raw tokens are
not stored in the database. There is no refresh token; sign in again after expiry.
Login/me/logout responses use `Cache-Control: no-store`.

## Configuration and local setup

| Setting | Requirement/default |
| --- | --- |
| `AUTH_SECRET_KEY` | Required, 32–4096 printable ASCII characters; generate a random secret |
| `AUTH_ISSUER` | `grading-system` |
| `AUTH_AUDIENCE` | `grading-api` |
| `AUTH_ACCESS_TOKEN_MINUTES` | `15`, range 1–60 |
| `OBJECTIVE_GRADING_API_ENABLED` | `false` |
| `OBJECTIVE_GRADING_ENVIRONMENT` | `development`; enabled grading API refuses other modes |
| `OBJECTIVE_GRADING_API_TOKEN` | Deprecated, ignored, grants no permissions |

The existing ignored local `.env` now contains a generated signing secret. Its
value was not displayed. Existing passwords and data were not reset. For another
checkout, first configure `.env` using `.env.example`, keeping its database
credentials consistent with Docker Compose. Generate a key without displaying it:

```powershell
python -c "from dotenv import set_key; import secrets; set_key('.env', 'AUTH_SECRET_KEY', secrets.token_urlsafe(48))"
```

This command replaces the key, invalidating existing signatures. Keep `.env`
private and out of Git. Restart the API after configuration changes; settings are
cached per process. Missing/invalid signing configuration returns a safe 503.
Start with the initialized development database; see the migration limitation
below before using an empty database.

```powershell
# From the repository root
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend/requirements-dev.txt
docker compose up -d postgres
Set-Location backend
python -m alembic upgrade head
python -m alembic check

# Prompts twice; passwords are never command arguments or printed output.
python -m app.auth.manage create-user --email instructor@example.test --name Instructor --role INSTRUCTOR
python -m app.auth.manage create-user --email assistant@example.test --name Assistant --role TA
python -m app.auth.manage create-user --email student@example.test --name Student --role STUDENT

python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

The provisioning CLI requires a 12–1024 character password and prints the account
UUID after success. It supports `ADMIN` as an explicit global administrator role.
Run it only as a trusted database operator. No API accepts public role selection.
Existing accounts with unsupported/invalid password hashes cannot authenticate;
the inspected legacy accounts had no supported password hash. Reset each intended
account explicitly rather than treating old placeholder values as passwords:

```powershell
python -m app.auth.manage set-password --email existing-account@example.test
python -m app.auth.manage disable-user --email existing-account@example.test
```

Neither command silently changes roles or reactivates a disabled account.
There is no user/role management HTTP API or self-service password recovery yet.

Example client session (PowerShell 7, from a separate terminal):

```powershell
$credentials = @{ email = 'instructor@example.test'; password = (Read-Host 'Password' -MaskInput) }
try {
    $session = Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/auth/login' -ContentType 'application/json' -Body ($credentials | ConvertTo-Json)
} finally { $credentials.Clear() }
$headers = @{ Authorization = 'Bearer ' + $session.access_token }
Invoke-RestMethod -Uri 'http://127.0.0.1:8000/auth/me' -Headers $headers
Invoke-RestMethod -Uri 'http://127.0.0.1:8000/assignments/' -Headers $headers
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/auth/logout' -Headers $headers
```

Do not print or log `$session` or Authorization headers. The API does not need
Docker access. Grading continues through the separate worker in the
[objective-grader guide](objective-grader.md).

## Role and permission matrix

“Owner” means `assignment.created_by` matches the live instructor account.
“Assigned TA” means an explicit `assignment_ta_memberships` row exists. Revoking
membership removes access on the next request. Administrators have explicit
global management authority; they do not impersonate students to submit code.

| Action | Owner instructor | Other instructor | Assigned TA | Unassigned TA | Student | ADMIN |
| --- | --- | --- | --- | --- | --- | --- |
| Create assignment | Yes | Yes, as its owner | No | No | No | Yes |
| Read assignment/rubrics | Own | No | Assigned | No | Published/closed | All |
| Change/delete assignment or rubric | Own | No | No | No | No | All |
| Read original tests | All own tests | No | All assigned tests | No | Approved PUBLIC only, published/closed parent | All |
| Create/change/delete/validate/approve/reject tests | Own | No | No | No | No | All |
| Read reference solution | Own | No | Assigned | No | No | All |
| Create/change/delete reference solution | Own | No | No | No | No | All |
| List/grant/revoke TA membership | Own | No | No | No | No | All |
| Submit code | No | No | No | No | Own identity, PUBLISHED only | No |
| Read/list submissions | All own assignment | No | All assigned assignment | No | Own only, published/closed parent | All |
| Enqueue objective run | Own | No | Assigned | No | No | All |
| Read objective run | Own assignment | No | Assigned | No | Own submission summary only | All |

The current assignment model has no enrollment or feedback-release policy.
Accordingly, all active students may read published/closed assignments and submit
to published assignments whose language matches. Closed assignments accept no
new submissions. Student grading responses expose aggregate status/counts/score,
timestamps, versions, language, and a safe generic failure message, with
`results=[]`. No per-test student feedback is released without a future policy.

TA grants use `PUT /assignments/{assignment_id}/tas/{ta_id}` with an existing
active TA account UUID and no request body. List with `GET .../tas`; revoke with
`DELETE .../tas/{ta_id}`. Grant/revoke are idempotent. Grantor UUID and time are
stored. Membership never grants content changes or test approval rights.

## Endpoint audit and privacy

The OpenAPI audit found 34 method/path operations: 32 authenticated, plus public
`POST /auth/login` and `GET /` health. Framework `/docs`, `/redoc`, and
`/openapi.json` remain public metadata. No code-execution HTTP route exists.
Using `A=/assignments/{assignment_id}` and
`S=A/submissions/{submission_id}`, the protected inventory is:

| Path | Methods |
| --- | --- |
| `/assignments/` | GET, POST |
| `A` | GET, PUT, DELETE |
| `A/rubrics/` | GET, POST |
| `A/rubrics/{rubric_id}` | PUT, DELETE |
| `A/tests/` | GET, POST |
| `A/tests/{test_case_id}` | GET, PUT, DELETE |
| `A/tests/{test_case_id}/{validate,approve,reject}` | POST for each action |
| `A/reference-solution/` | GET, POST, PUT, DELETE |
| `A/submissions/` | GET, POST |
| `S` | GET |
| `S/grade/objective` | POST |
| `S/grading-runs/latest`, `S/grading-runs/{run_id}` | GET for each |
| `A/tas` | GET |
| `A/tas/{ta_id}` | PUT, DELETE |
| `/auth/me` | GET |
| `/auth/logout` | POST |

Every resource service independently requires an actor and checks current database
identity/scope. Detached ORM objects claiming another role cannot override the
persisted account. Parent-child identifiers are matched before returning data.
Extra request fields such as `created_by`, `student_id`, and `role` return 422.

Students' test lists omit every HIDDEN test and every unapproved test, regardless
of source or status. Matching detail requests return 404. References are denied
before checking whether a solution exists. Authorized staff can read current
original test content, but objective run responses retain the existing stricter
rule: HIDDEN snapshot input, expected/actual output, stderr, and diagnostic text
are redacted even for staff. Snapshot visibility is frozen at enqueue time;
later test edits cannot unhide historic evidence. Private hashes/limits are not
serialized. Test selection, comparison rules, equal weighting, worker controls,
and grade-finalization behavior are unchanged.

Status policy: missing/invalid/expired/revoked identities return 401 with a Bearer
challenge; insufficient permission on an otherwise visible resource returns 403;
missing/out-of-scope resources and wrong-parent paths return 404. Student
reference access and objective triggers return 403 before looking up those child
resources, independent of their existence. Lists return only permitted rows.
Existing lifecycle/input errors remain 400/409/422 as appropriate. Database or
configuration failures return sanitized 503 responses.

Validation errors no longer echo raw submitted values; database exceptions are
handled without logging bound SQL parameters or private contents. Authentication
does not log passwords/tokens. Existing execution/worker logs remain bounded and
exclude code, test contents, credentials, and raw tracebacks. Deployment proxies
and database logging must follow the same policy.

## Migration and data preservation

Revision **`b5e3a91c7d42`**, following **`9c2e1f7a4b60`**, adds:

- `users.is_active`, non-null and true by default, preserving existing accounts.
- `auth_sessions`, storing user UUID, session UUID, creation/expiration/revocation
  times, with an indexed user FK and cascade deletion.
- `assignment_ta_memberships`, composite assignment/TA primary key, indexed TA FK,
  grantor UUID and creation time, with assignment/user deletion relationships.

No existing account, password hash, assignment, submission, test, or grading
history is rewritten. No credentials are implicitly upgraded or reset. No
production `Base.metadata.create_all()` shortcut was introduced.

The initial revisions `a38fb9beff0d` and `5f8909e84569` are empty: the repository
still cannot bootstrap all legacy tables through Alembic on an empty database.
This migration assumes the initialized legacy schema. Existing history was not
rewritten, stamped over, or dropped. A future reviewed baseline/restore procedure
is required for a clean deployment. SQLite `create_all` is confined to isolated
unit fixtures; live PostgreSQL integration requires migration head.

## Verification on 2026-10-23

From `backend`, against the configured existing PostgreSQL database:

```powershell
python -m alembic current
python -m alembic upgrade head
python -m alembic check
```

The database upgraded from `9c2e1f7a4b60` to `b5e3a91c7d42` and reported
**No new upgrade operations detected**. Isolated migration upgrade, model
comparison, and downgrade were also checked without downgrading the live database.

The new tests cover authentication success/failure, signed claim validation,
expiry/revocation/account status/live roles, each role's permissions, owner/foreign
assignment and submission paths, TA grants/revokes, hidden list/detail privacy,
reference restrictions, grading authorization, unauthorized mutations, missing
identity, body/path spoofing, and forged detached service actors. Fixtures issue
real JWTs with persisted sessions, rather than bypassing authentication.

The consolidated regression command from the repository root was:

```powershell
$env:PATH = 'C:\Users\trish\AppData\Local\Programs\DockerDesktop\resources\bin;' + $env:PATH
$env:PYTHONPATH = (Resolve-Path '.test-deps').Path + ';' + (Resolve-Path 'backend').Path
$env:EXECUTION_DOCKER_TESTS = '1'
$env:OBJECTIVE_GRADING_INTEGRATION_TESTS = '1'
$env:AUTH_INTEGRATION_TESTS = '1'
$authTestTemp = Join-Path $env:TEMP ('codex-auth-final-' + [guid]::NewGuid().ToString('N'))
python -m pytest backend/tests -c backend/pytest.ini -p no:cacheprovider --basetemp $authTestTemp --log-level=INFO -q
```

Result: **364 passed, 4 skipped, 1 warning in 274.35 seconds**. This includes all
23 real Docker sandbox cases, eight real PostgreSQL/objective-grading cases, and
three real PostgreSQL authentication cases. The auth cases verify actual login,
persisted sessions/logout revocation, TA grant/revoke scope, and deactivation of
an already signed-in account. Integration fixtures insert/delete only fresh
test-owned UUID rows and do not reset the schema or existing data.

The four skips are POSIX `O_NOFOLLOW` host artifact checks unavailable on Windows.
One existing Starlette/httpx deprecation warning remains. The local run used
ignored `.test-deps` for installed test/auth packages and the installed Docker
CLI path shown above; a normal activated virtual environment can supply the same
dependencies without that PYTHONPATH entry.

Earlier focused auth and RBAC runs passed 27 and 71 tests respectively. Initial
broad runs exposed test expectations using the old wrong-parent 400 and a
student's role-first 403; those expectations were corrected before the final run
to match the documented concealment policy. There were no final-suite failures.

Also passed: `python -m compileall -q backend/app backend/alembic` and
`git diff --check` (only Git's existing README line-ending notice). A smoke check
using the actual `.env` configuration and configured database returned health
200, unauthenticated assignments 401, and unknown-account login 401; no dependency
overrides were used. Alembic subsequently confirmed `b5e3a91c7d42 (head)`.

## Changed files

Added for authentication/authorization:

- `backend/app/auth/{__init__,config,security,dependencies,authorization,service,manage}.py`
- `backend/app/models/auth.py`
- `backend/app/schemas/{auth,authorization}.py`
- `backend/app/routers/{auth,assignment_memberships}.py`
- `backend/app/services/assignment_membership_service.py`
- `backend/alembic/versions/b5e3a91c7d42_authentication_and_ta_membership.py`
- `backend/tests/{test_auth,test_authorization,test_auth_integration}.py`
- `docs/authentication.md`

Updated:

- `backend/app/main.py`: router registration and safe validation/database errors.
- `backend/app/models/{user,__init__}.py`: active status and metadata exports.
- `backend/app/routers/{assignments,rubrics,test_cases,reference_solutions,submissions,objective_grading}.py`
- `backend/app/services/{assignment_service,rubric_service,test_case_service,reference_solution_service,submission_service,objective_grading_service}.py`
- `backend/app/schemas/{assignment,rubric,test_case,reference_solution,submission}.py`: reject unexpected identity/permission fields.
- `backend/app/grading/config.py`: retain the development gate, retire shared-token authority.
- `backend/requirements.txt`: `pwdlib[argon2]` and `PyJWT`.
- `backend/tests/{conftest,test_api_regression,test_objective_api,test_objective_grading,test_objective_configuration,test_objective_integration}.py`: authenticated regression fixtures/contracts.
- `.env.example`, `README.md`, `docs/objective-grader.md`, `docs/execution-engine.md`.

Existing execution/objective milestone work remains in the checkout. No files
were removed. The ignored `.env` signing key is local configuration, not a source
artifact.

## Remaining deployment work

Deploy behind TLS and add login throttling at a trusted gateway; the application
does not implement a distributed rate limiter, and concurrent Argon2 verification
has a resource cost. Protect client bearer storage and prevent request/header/body
logging. Use distinct least-privilege API/worker database credentials and restrict
direct database and provisioning CLI access. Direct database operators remain
trusted and can change roles/memberships/data. HS256 verifiers holding the secret
can also sign tokens; keep the key in a managed secret store for deployment.

Define expired/revoked session pruning and account/role-change audit retention.
There is no refresh flow, MFA, email verification, enrollment table, self-service
password reset, or configurable feedback-release policy yet. Membership rows
record the latest grant, rather than an immutable revocation event history.
Rotate secrets with a planned sign-in reset. The incomplete fresh-database
baseline and existing Docker shared-kernel isolation/worker crash-recovery risks
remain documented in the execution/objective guides. These controls require a
deployment review before exposing the service to untrusted public traffic.
