# grading-system

Reliable LLM-Based Programming Assignment Grading System.

The hardened C++ execution worker, limits, result contract, setup commands,
verification report, and remaining deployment risks are documented in
[the execution-engine guide](docs/execution-engine.md).

Execution is available through a local worker CLI. The FastAPI app does not
expose an execution endpoint.

The [authentication and authorization guide](docs/authentication.md) describes
Argon2id accounts, expiring revocable access tokens, instructor ownership, TA
membership, and student privacy. Existing resource routes now require a user
Bearer token. It includes local account setup, the permission matrix, migration
details, audited endpoints, and verification results.

The [Objective Test Grader](docs/objective-grader.md) queues persistent grading
runs through protected development API routes. A separate worker executes only
approved test snapshots, persists per-test evidence, and computes an objective
percentage without finalizing the assignment grade. The guide includes migration,
API, worker, testing, and recovery instructions.
