"""Runtime configuration. Every secret comes from the environment — nothing is hardcoded."""
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="AG_", extra="ignore")

    env: Literal["development", "test", "production"] = "development"
    database_url: str = "postgresql+psycopg://agentguard:agentguard@localhost:5432/agentguard"
    redis_url: str | None = None

    # Secrets (required — see .env.example)
    signing_secret: SecretStr = Field(..., description="HMAC key used to sign mandate versions")
    webhook_secret: SecretStr = Field(..., description="HMAC key used to verify provider webhooks")
    admin_api_key: SecretStr | None = Field(None, description="If set, operator endpoints require X-Admin-Key")
    enable_demo_endpoints: bool = True

    # LLM (advisory only — never authorizes money movement)
    llm_provider: Literal["rules", "anthropic", "openai"] = "rules"
    llm_model: str = "claude-sonnet-5-5"
    anthropic_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None

    # Engine tuning
    repeated_attempt_window_s: int = 60
    repeated_attempt_limit: int = 5
    recovery_max_attempts: int = 3
    recovery_backoff_s: int = 30
    deferred_event_timeout_s: int = 30

    cors_origins: list[str] = ["http://localhost:3000"]
    otel_exporter_endpoint: str | None = None

    @field_validator("redis_url", "admin_api_key", "anthropic_api_key", "openai_api_key",
                     "otel_exporter_endpoint", mode="before")
    @classmethod
    def _blank_is_unset(cls, v):
        """Docker Compose passes unset optional vars as "". Treat that as not configured."""
        return None if isinstance(v, str) and not v.strip() else v


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
