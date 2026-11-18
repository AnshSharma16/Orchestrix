"""Central application settings (Orchestrix V2).

Single source of runtime configuration, loaded once at import time.
Required: DATABASE_URL, SECRET_KEY. Everything else has a safe default.
"""

import os
import socket
import uuid
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_MIN_PRODUCTION_SECRET_LENGTH = 32


def _default_worker_id() -> str:
    """Unique per process start: host-pid-random."""
    return f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"


class Settings(BaseSettings):
    # ---- Application ----
    APP_ENV: Literal["development", "test", "production"] = "development"
    DEBUG: bool = False
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    LOG_FORMAT: Literal["console", "json"] = "console"
    METRICS_ENABLED: bool = True

    # ---- Database ----
    # Async URL, e.g. postgresql+asyncpg://user:pass@host:5432/db
    DATABASE_URL: str = Field(repr=False)
    # Optional explicit sync URL for Alembic (postgresql+psycopg://...).
    DATABASE_SYNC_URL: str | None = Field(default=None, repr=False)
    DB_POOL_SIZE: int = Field(default=5, ge=1)
    DB_MAX_OVERFLOW: int = Field(default=10, ge=0)
    DB_POOL_TIMEOUT_SECONDS: int = Field(default=30, ge=1)
    DB_POOL_RECYCLE_SECONDS: int = Field(default=1800, ge=30)
    DB_ECHO: bool = False

    # ---- Authentication ----
    SECRET_KEY: str = Field(repr=False)
    # Symmetric algorithms only: SECRET_KEY is a shared secret, never a keypair.
    ALGORITHM: Literal["HS256", "HS384", "HS512"] = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(default=30, ge=1)

    # ---- Worker / durable queue ----
    WORKER_ID: str = Field(default_factory=_default_worker_id)
    WORKER_CONCURRENCY: int = Field(default=4, ge=1, le=64)
    WORKER_SHUTDOWN_GRACE_SECONDS: int = Field(default=30, ge=1)
    JOB_POLL_INTERVAL_SECONDS: float = Field(default=1.0, gt=0)
    JOB_LEASE_SECONDS: int = Field(default=60, ge=5)
    JOB_HEARTBEAT_SECONDS: int = Field(default=20, ge=1)
    # Counts job claims (crash recoveries), separate from node retry attempts.
    JOB_MAX_ATTEMPTS: int = Field(default=3, ge=1)
    REAPER_INTERVAL_SECONDS: int = Field(default=15, ge=1)

    # ---- Execution engine ----
    NODE_TIMEOUT_SECONDS: int = Field(default=30, ge=1)
    RETRY_MAX_ATTEMPTS: int = Field(default=5, ge=1)
    RETRY_BASE_DELAY_SECONDS: float = Field(default=0.5, gt=0)
    RETRY_MAX_DELAY_SECONDS: float = Field(default=30.0, gt=0)
    RETRY_JITTER_RATIO: float = Field(default=0.25, ge=0, le=1)
    MAX_SNAPSHOT_BYTES: int = Field(default=1_048_576, ge=1024)
    MAX_WORKFLOW_NODES: int = Field(default=100, ge=1)

    # ---- Webhook adapter ----
    WEBHOOK_TIMEOUT_SECONDS: float = Field(default=10.0, gt=0)
    WEBHOOK_MAX_RESPONSE_BYTES: int = Field(default=1_048_576, ge=1024)
    # Must stay False in production (SSRF protection). True only for local dev.
    WEBHOOK_ALLOW_PRIVATE_NETWORKS: bool = False
    WEBHOOK_SIGNING_SECRET: SecretStr | None = None

    # ---- LLM adapter (any OpenAI-compatible endpoint) ----
    LLM_BASE_URL: str | None = None
    LLM_API_KEY: SecretStr | None = None
    LLM_DEFAULT_MODEL: str | None = None
    LLM_TIMEOUT_SECONDS: float = Field(default=60.0, gt=0)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @model_validator(mode="after")
    def _validate_cross_field_rules(self) -> "Settings":
        # Heartbeat must fire at least twice per lease so one missed beat
        # does not expire a healthy job.
        if self.JOB_HEARTBEAT_SECONDS * 2 >= self.JOB_LEASE_SECONDS:
            raise ValueError(
                "JOB_HEARTBEAT_SECONDS must be less than half of JOB_LEASE_SECONDS"
            )
        if self.RETRY_BASE_DELAY_SECONDS > self.RETRY_MAX_DELAY_SECONDS:
            raise ValueError(
                "RETRY_BASE_DELAY_SECONDS must not exceed RETRY_MAX_DELAY_SECONDS"
            )
        if self.APP_ENV == "production":
            if self.DEBUG:
                raise ValueError("DEBUG must be False when APP_ENV=production")
            if len(self.SECRET_KEY) < _MIN_PRODUCTION_SECRET_LENGTH:
                raise ValueError(
                    f"SECRET_KEY must be at least {_MIN_PRODUCTION_SECRET_LENGTH} "
                    "characters when APP_ENV=production"
                )
            if self.WEBHOOK_ALLOW_PRIVATE_NETWORKS:
                raise ValueError(
                    "WEBHOOK_ALLOW_PRIVATE_NETWORKS must be False "
                    "when APP_ENV=production"
                )
        return self

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"

    @property
    def sync_database_url(self) -> str:
        """Sync URL for Alembic: explicit override, else swap asyncpg -> psycopg."""
        if self.DATABASE_SYNC_URL:
            return self.DATABASE_SYNC_URL
        return self.DATABASE_URL.replace("+asyncpg", "+psycopg", 1)


settings = Settings()