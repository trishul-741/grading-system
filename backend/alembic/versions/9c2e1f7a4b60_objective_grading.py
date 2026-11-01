"""Persist durable objective grading runs and per-test evidence.

Revision ID: 9c2e1f7a4b60
Revises: 6a87b9acee87
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "9c2e1f7a4b60"
down_revision: str | Sequence[str] | None = "6a87b9acee87"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "objective_grading_runs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("assignment_id", sa.UUID(), nullable=False),
        sa.Column("submission_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.Enum("QUEUED", "RUNNING", "COMPLETED", "INCOMPLETE", "NOT_GRADEABLE", "CANCELLED", name="objective_run_status", native_enum=False, create_constraint=True), nullable=False),
        sa.Column("total_tests", sa.Integer(), nullable=False),
        sa.Column("passed_tests", sa.Integer(), nullable=False),
        sa.Column("failed_tests", sa.Integer(), nullable=False),
        sa.Column("correctness_percentage", sa.Numeric(6, 3), nullable=True),
        sa.Column("error_category", sa.String(64), nullable=True),
        sa.Column("error_message", sa.String(256), nullable=True),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("engine_version", sa.String(32), nullable=False),
        sa.Column("comparison_policy_version", sa.String(64), nullable=False),
        sa.Column("execution_engine_version", sa.String(32), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("language", sa.String(32), nullable=False),
        sa.Column("previous_submission_status", sa.String(32), nullable=False),
        sa.Column("execution_settings", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["assignment_id"], ["assignments.id"], name="fk_objective_runs_assignment", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["submission_id"], ["submissions.id"], name="fk_objective_runs_submission", ondelete="CASCADE"),
        sa.CheckConstraint("total_tests >= 0 AND passed_tests >= 0 AND failed_tests >= 0", name="ck_objective_run_nonnegative_counts"),
        sa.CheckConstraint("passed_tests + failed_tests <= total_tests", name="ck_objective_run_bounded_counts"),
        sa.CheckConstraint("correctness_percentage IS NULL OR (correctness_percentage >= 0 AND correctness_percentage <= 100)", name="ck_objective_run_score_range"),
        sa.CheckConstraint("(status = 'COMPLETED' AND correctness_percentage IS NOT NULL AND total_tests > 0 AND passed_tests + failed_tests = total_tests) OR (status <> 'COMPLETED' AND correctness_percentage IS NULL)", name="ck_objective_run_complete_score"),
        sa.CheckConstraint("length(source_sha256) = 64", name="ck_objective_run_source_hash"),
    )
    op.create_index("ix_objective_grading_runs_assignment_id", "objective_grading_runs", ["assignment_id"])
    op.create_index("ix_objective_runs_submission_queued", "objective_grading_runs", ["submission_id", "queued_at"])
    op.create_index("ix_objective_runs_status_queued", "objective_grading_runs", ["status", "queued_at"])
    op.create_index("uq_objective_active_submission", "objective_grading_runs", ["submission_id"], unique=True,
                    postgresql_where=sa.text("status IN ('QUEUED', 'RUNNING')"),
                    sqlite_where=sa.text("status IN ('QUEUED', 'RUNNING')"))
    op.create_table(
        "objective_test_results",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("grading_run_id", sa.UUID(), nullable=False),
        sa.Column("test_case_id", sa.UUID(), nullable=True),
        sa.Column("test_case_id_snapshot", sa.UUID(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("visibility", sa.Enum("PUBLIC", "HIDDEN", name="objective_result_visibility", native_enum=False, create_constraint=True), nullable=False),
        sa.Column("input_data", sa.Text(), nullable=False),
        sa.Column("expected_output", sa.Text(), nullable=False),
        sa.Column("actual_output", sa.Text(), nullable=True),
        sa.Column("stderr", sa.Text(), nullable=True),
        sa.Column("actual_output_truncated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("stderr_truncated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("actual_output_sha256", sa.String(64), nullable=True),
        sa.Column("outcome", sa.Enum("PENDING", "PASSED", "WRONG_ANSWER", "COMPILE_ERROR", "RUNTIME_ERROR", "TIMEOUT", "MEMORY_LIMIT", "OUTPUT_LIMIT", "SYSTEM_ERROR", "SKIPPED", "CANCELLED", name="objective_test_outcome", native_enum=False, create_constraint=True), nullable=False),
        sa.Column("execution_status", sa.String(32), nullable=True),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("execution_time_ms", sa.Integer(), nullable=True),
        sa.Column("compilation_time_ms", sa.Integer(), nullable=True),
        sa.Column("error_category", sa.String(64), nullable=True),
        sa.Column("error_message", sa.String(256), nullable=True),
        sa.Column("execution_job_id", sa.UUID(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["grading_run_id"], ["objective_grading_runs.id"], name="fk_objective_results_run", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["test_case_id"], ["test_cases.id"], name="fk_objective_results_test_case", ondelete="SET NULL"),
        sa.UniqueConstraint("grading_run_id", "position", name="uq_objective_result_position"),
        sa.UniqueConstraint("grading_run_id", "test_case_id_snapshot", name="uq_objective_result_test_snapshot"),
        sa.CheckConstraint("position >= 0", name="ck_objective_result_position"),
        sa.CheckConstraint("execution_time_ms IS NULL OR execution_time_ms >= 0", name="ck_objective_result_execution_time"),
        sa.CheckConstraint("compilation_time_ms IS NULL OR compilation_time_ms >= 0", name="ck_objective_result_compilation_time"),
        sa.CheckConstraint("length(input_data) <= 4194304", name="ck_objective_result_input_size"),
        sa.CheckConstraint("length(expected_output) <= 4194304", name="ck_objective_result_expected_size"),
        sa.CheckConstraint("actual_output IS NULL OR length(actual_output) <= 4194304", name="ck_objective_result_actual_size"),
        sa.CheckConstraint("stderr IS NULL OR length(stderr) <= 4194304", name="ck_objective_result_stderr_size"),
        sa.CheckConstraint("actual_output_sha256 IS NULL OR length(actual_output_sha256) = 64", name="ck_objective_result_actual_hash"),
    )
    op.create_index("ix_objective_test_results_grading_run_id", "objective_test_results", ["grading_run_id"])


def downgrade() -> None:
    op.drop_index("ix_objective_test_results_grading_run_id", table_name="objective_test_results")
    op.drop_table("objective_test_results")
    op.drop_index("uq_objective_active_submission", table_name="objective_grading_runs")
    op.drop_index("ix_objective_runs_status_queued", table_name="objective_grading_runs")
    op.drop_index("ix_objective_runs_submission_queued", table_name="objective_grading_runs")
    op.drop_index("ix_objective_grading_runs_assignment_id", table_name="objective_grading_runs")
    op.drop_table("objective_grading_runs")
