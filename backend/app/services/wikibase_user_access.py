"""Grant Wikibase Cloud access to registered users on login / invite accept."""

from __future__ import annotations

import asyncio
import logging
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.models.wikibase_user_access import (
    WIKI_ACCOUNT_ACTIVE,
    WIKI_ACCOUNT_FAILED,
    WIKI_ACCOUNT_NONE,
    WIKI_ACCOUNT_PENDING,
    WIKI_ACCOUNT_SKIPPED,
    WikibaseUserAccess,
)
from app.services.wikibase_credentials import (
    get_server_wikibase_auth,
    server_wikibase_endpoint_config,
)
from app.settings import get_settings
from converter.wikibase.cloud_client import WikibaseCloudWriter

logger = logging.getLogger(__name__)

# Login /me must never wait on Wikibase Cloud's full retry budget (Rule W-40 /
# Heroku H12). Provision is best-effort with a hard ceiling.
_PROVISION_BUDGET_SECONDS = 5.0

_INVALID_WIKI_USERNAME_CHARS = re.compile(r"[#<>[\]|{}\\/:]")

# Do not re-attempt remote createaccount on every login/me once we already
# have a decisive outcome. FAILED used to be retried forever, which turned a
# flaky Wikibase Cloud into H12s on POST /api/auth/login.
_WIKI_NO_RETRY_STATUSES = frozenset({
    WIKI_ACCOUNT_ACTIVE,
    WIKI_ACCOUNT_SKIPPED,
    WIKI_ACCOUNT_FAILED,
})


@dataclass(frozen=True)
class WikibaseAccessSnapshot:
    wikibase_configured: bool
    wikibase_authorized: bool
    wikibase_base_url: str | None
    wikibase_username: str | None
    wikibase_wiki_account_status: str


def wiki_username_from_email(email: str) -> str:
    """Derive a MediaWiki-local username from the app user's email."""
    normalized = email.strip()
    if normalized and _INVALID_WIKI_USERNAME_CHARS.search(normalized) is None:
        return normalized
    local, _, domain = normalized.partition("@")
    safe_local = _INVALID_WIKI_USERNAME_CHARS.sub("", local) or "user"
    safe_domain = _INVALID_WIKI_USERNAME_CHARS.sub("", domain) or "local"
    candidate = f"{safe_local}@{safe_domain}"
    return candidate[:255]


def snapshot_from_row(
    row: WikibaseUserAccess | None,
    *,
    configured: bool,
    base_url: str | None,
) -> WikibaseAccessSnapshot:
    if not configured:
        return WikibaseAccessSnapshot(
            wikibase_configured=False,
            wikibase_authorized=False,
            wikibase_base_url=None,
            wikibase_username=None,
            wikibase_wiki_account_status=WIKI_ACCOUNT_NONE,
        )
    authorized = bool(row and row.app_authorized)
    return WikibaseAccessSnapshot(
        wikibase_configured=True,
        wikibase_authorized=authorized,
        wikibase_base_url=base_url,
        wikibase_username=row.wiki_username if row else None,
        wikibase_wiki_account_status=(
            row.wiki_account_status if row else WIKI_ACCOUNT_NONE
        ),
    )


def _provision_wiki_account_sync(username: str, email: str) -> tuple[str, str | None]:
    """Best-effort wiki account creation via server OAuth. Returns (status, error)."""
    auth = get_server_wikibase_auth()
    if auth is None:
        return WIKI_ACCOUNT_SKIPPED, "server OAuth not configured"

    settings = get_settings()
    write_user = settings.wikibase_cloud_write_user.strip() or "mhm-pipeline-web"
    writer = WikibaseCloudWriter(
        server_wikibase_endpoint_config(),
        auth,
        user_agent=f"{write_user}/1.0 (MHM Pipeline Web)",
    )
    try:
        writer.ensure_authenticated()
        outcome = writer.create_local_account(
            username,
            secrets.token_urlsafe(24),
            email=email,
        )
    except Exception as exc:  # noqa: BLE001 — provisioning must not break login
        logger.warning("wikibase account provision failed for %s: %s", username, exc)
        return WIKI_ACCOUNT_FAILED, str(exc)

    if outcome.ok:
        return WIKI_ACCOUNT_ACTIVE, None
    if "permission" in outcome.message.lower():
        return WIKI_ACCOUNT_SKIPPED, outcome.message
    return WIKI_ACCOUNT_FAILED, outcome.message


async def ensure_wikibase_access(
    db: AsyncSession,
    *,
    user: User,
    email: str,
    attempt_provision: bool = False,
) -> WikibaseAccessSnapshot:
    """Authorize the user for Wikibase features; optionally provision a wiki account.

    ``attempt_provision`` is True only on login / invite accept. ``/me`` must
    never open a Wikibase Cloud retry loop — that path is polled constantly and
    previously H12'd login when status was ``failed`` (non-terminal retry).
    """
    settings = get_settings()
    configured = settings.wikibase_cloud_configured
    base_url = settings.wikibase_cloud_base_url.strip() or None

    if not configured:
        return snapshot_from_row(None, configured=False, base_url=None)

    row = (
        await db.execute(
            select(WikibaseUserAccess).where(WikibaseUserAccess.user_id == user.id)
        )
    ).scalar_one_or_none()

    now = datetime.now(timezone.utc)
    wiki_username = wiki_username_from_email(email)

    if row is None:
        row = WikibaseUserAccess(
            user_id=user.id,
            app_authorized=True,
            wiki_username=wiki_username,
            wiki_account_status=WIKI_ACCOUNT_PENDING,
            authorized_at=now,
        )
        db.add(row)
    else:
        row.app_authorized = True
        row.wiki_username = wiki_username
        if row.authorized_at is None:
            row.authorized_at = now

    await db.flush()

    should_provision = (
        attempt_provision
        and settings.wikibase_auto_provision_wiki_accounts
        and row.wiki_account_status not in _WIKI_NO_RETRY_STATUSES
    )
    if should_provision:
        try:
            status, error = await asyncio.wait_for(
                asyncio.to_thread(
                    _provision_wiki_account_sync, wiki_username, email,
                ),
                timeout=_PROVISION_BUDGET_SECONDS,
            )
        except TimeoutError:
            logger.warning(
                "wikibase account provision timed out after %.1fs for %s",
                _PROVISION_BUDGET_SECONDS,
                wiki_username,
            )
            status, error = (
                WIKI_ACCOUNT_FAILED,
                f"provision timed out after {_PROVISION_BUDGET_SECONDS:.0f}s",
            )
        row.wiki_account_status = status
        row.wiki_account_error = error
        if status == WIKI_ACCOUNT_ACTIVE:
            row.wiki_provisioned_at = now

    return snapshot_from_row(row, configured=True, base_url=base_url)
