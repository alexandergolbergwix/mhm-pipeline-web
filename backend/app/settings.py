"""Typed settings loaded from env vars / .env file."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _project_root() -> Path:
    # backend/app/settings.py → repo root is two parents up.
    return Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_project_root() / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── App ───────────────────────────────────────────────────────────
    env: str = Field(default="development")
    frontend_origin: str = Field(default="http://localhost:5173")
    cookie_secure: bool = Field(default=False)
    session_ttl_hours: int = Field(default=12)

    # ── Database ──────────────────────────────────────────────────────
    database_url: str = Field(
        default="postgresql+asyncpg://mhm:mhm@localhost:5432/mhm_pipeline_web",
    )

    # ── Server-held crypto keys ───────────────────────────────────────
    # Both must be 32 raw bytes (we accept urlsafe-base64 or hex).
    master_key: str = Field(default="")
    email_hmac_key: str = Field(default="")

    # ── Registration / spam protection ────────────────────────────────
    resend_api_key: str = Field(default="")
    resend_from_email: str = Field(default="MHM Pipeline <noreply@example.org>")
    admin_notification_email: str = Field(default="")
    turnstile_site_key: str = Field(default="")
    turnstile_secret_key: str = Field(default="")
    request_confirm_ttl_hours: int = Field(default=24)
    admin_decision_ttl_hours: int = Field(default=168)

    @field_validator("database_url", mode="before")
    @classmethod
    def _normalise_db_url(cls, v: str) -> str:
        # Heroku ships DATABASE_URL with the legacy ``postgres://`` scheme;
        # SQLAlchemy 2 + asyncpg need ``postgresql+asyncpg://``.
        if not v:
            return v
        return re.sub(r"^postgres(?:ql)?://", "postgresql+asyncpg://", v)

    @property
    def is_production(self) -> bool:
        return self.env.lower() == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
