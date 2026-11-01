from functools import lru_cache

from pydantic import Field, SecretStr, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.grading.errors import GradingError


class ObjectiveGradingSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OBJECTIVE_GRADING_", extra="ignore", frozen=True,
                                     hide_input_in_errors=True)

    api_enabled: bool = False
    api_token: SecretStr | None = None
    environment: str = "development"
    max_tests: int = Field(default=100, ge=1, le=1000)
    max_snapshot_bytes: int = Field(default=8 * 1024 * 1024, ge=1024, le=64 * 1024 * 1024)
    evidence_limit_bytes: int = Field(default=65536, ge=1, le=4 * 1024 * 1024)
    worker_poll_seconds: float = Field(default=2, ge=0.1, le=60, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_enabled_api(self) -> "ObjectiveGradingSettings":
        if self.api_enabled and self.environment != "development":
            raise ValueError("The development grading API requires development mode")
        # Retained only to accept existing configuration files. This legacy
        # operator credential grants no user identity or application permission.
        return self


@lru_cache(maxsize=1)
def get_objective_grading_settings() -> ObjectiveGradingSettings:
    try:
        return ObjectiveGradingSettings()
    except ValidationError:
        # Dependency setup failures must not log a configuration traceback that
        # could contain environment inputs or tokens.
        raise GradingError(503, "Objective grading configuration is unavailable") from None
