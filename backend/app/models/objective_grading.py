"""Durable grading jobs and bounded snapshots of their per-test evidence.

Source is kept only on Submission. A hash detects changes before execution;
approved test snapshots preserve the inputs to a decision after tests are edited
or deleted. API serialization must redact hidden snapshots and diagnostics.
"""

import uuid

from sqlalchemy import (
    JSON, Boolean, CheckConstraint, Column, DateTime, Enum, ForeignKey, Index, Integer,
    Numeric, String, Text, UniqueConstraint, false, text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base
from app.enums import TestVisibility
from app.grading.enums import RunStatus, TestOutcome


MAX_EVIDENCE_CHARACTERS = 4 * 1024 * 1024


class ObjectiveGradingRun(Base):
    __tablename__ = "objective_grading_runs"
    __table_args__ = (
        CheckConstraint("total_tests >= 0 AND passed_tests >= 0 AND failed_tests >= 0", name="ck_objective_run_nonnegative_counts"),
        CheckConstraint("passed_tests + failed_tests <= total_tests", name="ck_objective_run_bounded_counts"),
        CheckConstraint("correctness_percentage IS NULL OR (correctness_percentage >= 0 AND correctness_percentage <= 100)", name="ck_objective_run_score_range"),
        CheckConstraint("(status = 'COMPLETED' AND correctness_percentage IS NOT NULL AND total_tests > 0 AND passed_tests + failed_tests = total_tests) OR (status <> 'COMPLETED' AND correctness_percentage IS NULL)", name="ck_objective_run_complete_score"),
        CheckConstraint("length(source_sha256) = 64", name="ck_objective_run_source_hash"),
        Index("uq_objective_active_submission", "submission_id", unique=True,
              postgresql_where=text("status IN ('QUEUED', 'RUNNING')"),
              sqlite_where=text("status IN ('QUEUED', 'RUNNING')")),
        Index("ix_objective_runs_submission_queued", "submission_id", "queued_at"),
        Index("ix_objective_runs_status_queued", "status", "queued_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    assignment_id = Column(UUID(as_uuid=True), ForeignKey("assignments.id", ondelete="CASCADE"), nullable=False, index=True)
    submission_id = Column(UUID(as_uuid=True), ForeignKey("submissions.id", ondelete="CASCADE"), nullable=False)
    status = Column(Enum(RunStatus, name="objective_run_status", native_enum=False, create_constraint=True, validate_strings=True), nullable=False, default=RunStatus.QUEUED)
    total_tests = Column(Integer, nullable=False, default=0)
    passed_tests = Column(Integer, nullable=False, default=0)
    failed_tests = Column(Integer, nullable=False, default=0)
    correctness_percentage = Column(Numeric(6, 3), nullable=True)
    error_category = Column(String(64), nullable=True)
    error_message = Column(String(256), nullable=True)
    queued_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    engine_version = Column(String(32), nullable=False)
    comparison_policy_version = Column(String(64), nullable=False)
    execution_engine_version = Column(String(32), nullable=False)
    source_sha256 = Column(String(64), nullable=False)
    language = Column(String(32), nullable=False)
    previous_submission_status = Column(String(32), nullable=False)
    execution_settings = Column(JSON, nullable=False)

    assignment = relationship("Assignment")
    submission = relationship("Submission")
    results = relationship("ObjectiveTestResult", back_populates="grading_run",
                           order_by="ObjectiveTestResult.position",
                           cascade="all, delete-orphan", passive_deletes=True)


class ObjectiveTestResult(Base):
    __tablename__ = "objective_test_results"
    __table_args__ = (
        UniqueConstraint("grading_run_id", "position", name="uq_objective_result_position"),
        UniqueConstraint("grading_run_id", "test_case_id_snapshot", name="uq_objective_result_test_snapshot"),
        CheckConstraint("position >= 0", name="ck_objective_result_position"),
        CheckConstraint("execution_time_ms IS NULL OR execution_time_ms >= 0", name="ck_objective_result_execution_time"),
        CheckConstraint("compilation_time_ms IS NULL OR compilation_time_ms >= 0", name="ck_objective_result_compilation_time"),
        CheckConstraint(f"length(input_data) <= {MAX_EVIDENCE_CHARACTERS}", name="ck_objective_result_input_size"),
        CheckConstraint(f"length(expected_output) <= {MAX_EVIDENCE_CHARACTERS}", name="ck_objective_result_expected_size"),
        CheckConstraint(f"actual_output IS NULL OR length(actual_output) <= {MAX_EVIDENCE_CHARACTERS}", name="ck_objective_result_actual_size"),
        CheckConstraint(f"stderr IS NULL OR length(stderr) <= {MAX_EVIDENCE_CHARACTERS}", name="ck_objective_result_stderr_size"),
        CheckConstraint("actual_output_sha256 IS NULL OR length(actual_output_sha256) = 64", name="ck_objective_result_actual_hash"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    grading_run_id = Column(UUID(as_uuid=True), ForeignKey("objective_grading_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    test_case_id = Column(UUID(as_uuid=True), ForeignKey("test_cases.id", ondelete="SET NULL"), nullable=True)
    test_case_id_snapshot = Column(UUID(as_uuid=True), nullable=False)
    position = Column(Integer, nullable=False)
    visibility = Column(Enum(TestVisibility, name="objective_result_visibility", native_enum=False, create_constraint=True, validate_strings=True), nullable=False)
    input_data = Column(Text, nullable=False)
    expected_output = Column(Text, nullable=False)
    actual_output = Column(Text, nullable=True)
    stderr = Column(Text, nullable=True)
    actual_output_truncated = Column(Boolean, nullable=False, default=False, server_default=false())
    stderr_truncated = Column(Boolean, nullable=False, default=False, server_default=false())
    actual_output_sha256 = Column(String(64), nullable=True)
    outcome = Column(Enum(TestOutcome, name="objective_test_outcome", native_enum=False, create_constraint=True, validate_strings=True), nullable=False, default=TestOutcome.PENDING)
    execution_status = Column(String(32), nullable=True)
    exit_code = Column(Integer, nullable=True)
    execution_time_ms = Column(Integer, nullable=True)
    compilation_time_ms = Column(Integer, nullable=True)
    error_category = Column(String(64), nullable=True)
    error_message = Column(String(256), nullable=True)
    execution_job_id = Column(UUID(as_uuid=True), nullable=True)

    grading_run = relationship("ObjectiveGradingRun", back_populates="results")
    test_case = relationship("TestCase")
