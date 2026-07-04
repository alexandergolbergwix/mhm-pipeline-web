"""Resolve and verify per-user Wikibase Cloud bot credentials."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.session import AuthContext
from converter.wikibase.cloud_client import (
    WikibaseBotCredentials,
    WikibaseCloudWriter,
)

_DEFAULT_BOT_NAME = "mhm-pipeline"


@dataclass(frozen=True)
class WikibaseCredentialVerifyResult:
    ok: bool
    message: str
    login_name: str | None = None


async def resolve_wikibase_bot_credentials(
    db: AsyncSession,
    auth: AuthContext,
    *,
    username: str | None = None,
    bot_name: str | None = None,
    password: str | None = None,
) -> WikibaseBotCredentials | None:
    """Build a credential tuple from overrides plus stored Settings values."""
    from app.routers.hmo_studio import _resolve_bot_name, _unwrap_user_secret  # noqa: PLC0415

    resolved_username = username or await _unwrap_user_secret(
        db, auth, "wikibase_cloud_bot_username",
    )
    resolved_password = password or await _unwrap_user_secret(
        db, auth, "wikibase_cloud_bot_password",
    )
    if not resolved_username or not resolved_password:
        return None

    if bot_name is not None:
        resolved_bot_name = bot_name.strip() or _DEFAULT_BOT_NAME
    else:
        resolved_bot_name = await _resolve_bot_name(db, auth)

    return WikibaseBotCredentials(
        username=resolved_username.strip(),
        bot_name=resolved_bot_name.strip(),
        password=resolved_password,
    )


def verify_wikibase_bot_credentials_sync(
    credentials: WikibaseBotCredentials,
) -> WikibaseCredentialVerifyResult:
    """Log in to mhm-hmo.wikibase.cloud with the given bot password."""
    if "@" in credentials.username:
        return WikibaseCredentialVerifyResult(
            ok=False,
            message=(
                "Username should be your account name only — not User@BotName. "
                "Put the bot name in the separate Wikibase Cloud bot name field."
            ),
        )

    writer = WikibaseCloudWriter.for_mhm_hmo_cloud(credentials)
    try:
        writer.login()
    except RuntimeError as exc:
        message = str(exc)
        prefix = "Login failed ("
        if message.startswith(prefix) and message.endswith(")"):
            message = message[len(prefix):-1]
        return WikibaseCredentialVerifyResult(ok=False, message=message)

    return WikibaseCredentialVerifyResult(
        ok=True,
        message="Login successful",
        login_name=credentials.login_name,
    )


async def verify_wikibase_bot_credentials(
    db: AsyncSession,
    auth: AuthContext,
    *,
    username: str | None = None,
    bot_name: str | None = None,
    password: str | None = None,
) -> WikibaseCredentialVerifyResult:
    """Resolve stored/overridden credentials and verify them against Wikibase."""
    credentials = await resolve_wikibase_bot_credentials(
        db, auth, username=username, bot_name=bot_name, password=password,
    )
    if credentials is None:
        return WikibaseCredentialVerifyResult(
            ok=False,
            message="Set Wikibase Cloud bot username and password first.",
        )
    return await asyncio.to_thread(verify_wikibase_bot_credentials_sync, credentials)
