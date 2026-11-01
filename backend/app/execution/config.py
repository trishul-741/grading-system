"""Validated execution limits loaded once for each worker process."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ExecutionSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="EXECUTION_", extra="ignore", frozen=True, allow_inf_nan=False,
    )

    source_limit_bytes: int = Field(default=64 * 1024, ge=1, le=4 * 1024 * 1024)
    input_limit_bytes: int = Field(default=64 * 1024, ge=0, le=4 * 1024 * 1024)
    stdout_limit_bytes: int = Field(default=64 * 1024, ge=1, le=4 * 1024 * 1024)
    stderr_limit_bytes: int = Field(default=64 * 1024, ge=1, le=4 * 1024 * 1024)
    artifact_limit_bytes: int = Field(default=16 * 1024 * 1024, ge=1024, le=32 * 1024 * 1024)
    compile_timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    runtime_timeout_seconds: float = Field(default=3.0, gt=0, le=60)
    docker_control_timeout_seconds: float = Field(default=5.0, gt=0, le=30)
    compile_memory_mb: int = Field(default=512, ge=64, le=4096)
    runtime_memory_mb: int = Field(default=256, ge=32, le=4096)
    cpus: float = Field(default=1.0, gt=0, le=4)
    pids_limit: int = Field(default=32, ge=8, le=256)
    workspace_size_mb: int = Field(default=64, ge=16, le=512)
    tmp_size_mb: int = Field(default=16, ge=1, le=128)
    max_concurrent_jobs: int = Field(default=2, ge=1, le=16)


@lru_cache(maxsize=1)
def get_execution_settings() -> ExecutionSettings:
    return ExecutionSettings()
