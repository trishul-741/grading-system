"""Objective grading state is independent from final assignment grading."""

from enum import Enum


class RunStatus(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    INCOMPLETE = "INCOMPLETE"
    NOT_GRADEABLE = "NOT_GRADEABLE"
    CANCELLED = "CANCELLED"


class TestOutcome(str, Enum):
    PENDING = "PENDING"
    PASSED = "PASSED"
    WRONG_ANSWER = "WRONG_ANSWER"
    COMPILE_ERROR = "COMPILE_ERROR"
    RUNTIME_ERROR = "RUNTIME_ERROR"
    TIMEOUT = "TIMEOUT"
    MEMORY_LIMIT = "MEMORY_LIMIT"
    OUTPUT_LIMIT = "OUTPUT_LIMIT"
    SYSTEM_ERROR = "SYSTEM_ERROR"
    SKIPPED = "SKIPPED"
    CANCELLED = "CANCELLED"
