"""Typed settings loaded from env vars / .env file."""

from __future__ import annotations

import hashlib
import re
from functools import lru_cache
from uuid import UUID
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

    # ── Redis (L1 inference cache + optional slowapi storage) ─────────
    # Heroku sets REDIS_URL when heroku-redis:mini is provisioned.
    redis_url: str = Field(default="")

    # ── Server-held crypto keys ───────────────────────────────────────
    # Both must be 32 raw bytes (we accept urlsafe-base64 or hex).
    master_key: str = Field(default="")
    email_hmac_key: str = Field(default="")

    # ── Linked Data Explorer ──────────────────────────────────────────
    # Optional: SPARQL endpoint for the project's local Wikibase instance.
    # Leave empty to disable the Wikibase data source in the explorer UI.
    wikibase_sparql_url: str = Field(default="")

    # ── HMO Wikibase Cloud (server-held OAuth 2.0) ────────────────────
    wikibase_cloud_base_url: str = Field(default="https://mhm-hmo.wikibase.cloud")
    wikibase_cloud_oauth_client_id: str = Field(default="")
    wikibase_cloud_oauth_client_secret: str = Field(default="")
    # Optional JWT from Special:OAuthConsumerRegistration — when set,
    # used as Bearer auth for both WBI entity writes and MediaWiki edits.
    wikibase_cloud_oauth_access_token: str = Field(default="")
    # Prefix for every edit summary / revision attribution on the wiki.
    wikibase_cloud_write_user: str = Field(default="mhm-pipeline-web")
    # When true, login/invite-accept attempts to create a matching wiki account
    # via the server OAuth session (best-effort; app auth still succeeds on failure).
    wikibase_auto_provision_wiki_accounts: bool = Field(default=True)

    @property
    def wikibase_cloud_configured(self) -> bool:
        if self.wikibase_cloud_oauth_access_token.strip():
            return True
        return bool(
            self.wikibase_cloud_oauth_client_id.strip()
            and self.wikibase_cloud_oauth_client_secret.strip()
        )

    # Canonical HMO rollout: keep disabled until backfill and shadow checks pass.
    # Explicit run IDs are the safest first stage; percentage is deterministic
    # by run UUID so a rollout never changes cohort between dynos.
    hmo_canonical_first: bool = Field(default=False)
    hmo_canonical_first_run_ids: str = Field(default="")
    hmo_canonical_first_percentage: int = Field(default=0, ge=0, le=100)
    # The standalone Authority editor is retired once the HMO canonical
    # migration is live. Keep the switch explicit so a rollback can reopen
    # compatibility mutations without a code deploy.
    legacy_authority_mutations_enabled: bool = Field(default=False)

    def canonical_first_for_run(self, run_id: UUID | str) -> bool:
        """Return whether this run is in the canonical-first rollout cohort."""
        if self.hmo_canonical_first:
            return True
        run_text = str(run_id).strip()
        explicit = {
            token.strip().lower()
            for token in self.hmo_canonical_first_run_ids.split(",")
            if token.strip()
        }
        if run_text.lower() in explicit:
            return True
        if self.hmo_canonical_first_percentage <= 0:
            return False
        bucket = int(hashlib.sha256(run_text.encode("utf-8")).hexdigest()[:8], 16) % 100
        return bucket < self.hmo_canonical_first_percentage

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
