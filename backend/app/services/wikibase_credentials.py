"""Server-held Wikibase Cloud OAuth credentials (Heroku config vars)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from fastapi import HTTPException, status

from app.settings import get_settings
from converter.wikibase.cloud_client import (
    WikibaseCloudAuth,
    WikibaseCloudWriter,
    WikibaseEndpointConfig,
    wikibase_edit_summary,
)


@dataclass(frozen=True)
class WikibaseAuthVerifyResult:
    ok: bool
    message: str
    base_url: str | None = None
    api_username: str | None = None
    write_user: str | None = None


def get_server_wikibase_auth() -> WikibaseCloudAuth | None:
    """Return server OAuth config when sufficiently complete, else ``None``."""
    settings = get_settings()
    if not settings.wikibase_cloud_configured:
        return None
    access_token = settings.wikibase_cloud_oauth_access_token.strip() or None
    return WikibaseCloudAuth(
        mode="oauth2",
        client_id=settings.wikibase_cloud_oauth_client_id.strip(),
        client_secret=settings.wikibase_cloud_oauth_client_secret.strip(),
        access_token=access_token,
    )


def server_wikibase_endpoint_config() -> WikibaseEndpointConfig:
    settings = get_settings()
    base_url = settings.wikibase_cloud_base_url.strip() or "https://mhm-hmo.wikibase.cloud"
    return WikibaseEndpointConfig(base_url=base_url, display_name="MHM HMO Wikibase")


def build_server_wikibase_writer() -> WikibaseCloudWriter:
    auth = get_server_wikibase_auth()
    if auth is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Wikibase Cloud is not configured on this server. "
                "Set WIKIBASE_CLOUD_OAUTH_CLIENT_ID and "
                "WIKIBASE_CLOUD_OAUTH_CLIENT_SECRET (or "
                "WIKIBASE_CLOUD_OAUTH_ACCESS_TOKEN) in the deployment config."
            ),
        )
    settings = get_settings()
    write_user = settings.wikibase_cloud_write_user.strip() or "mhm-pipeline-web"
    writer = WikibaseCloudWriter(
        server_wikibase_endpoint_config(),
        auth,
        user_agent=f"{write_user}/1.0 (MHM Pipeline Web)",
    )
    try:
        writer.ensure_authenticated()
        api_user = writer.current_api_user()
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Wikibase Cloud authentication failed: {exc}",
        ) from exc
    if api_user != write_user:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                f"Wikibase OAuth session is {api_user!r}, expected {write_user!r}. "
                f"Create the {write_user} wiki account on "
                f"{settings.wikibase_cloud_base_url}, register a new OAuth consumer "
                f"under that account, and update the Heroku OAuth config vars."
            ),
        )
    return writer


def verify_server_wikibase_auth_sync(
    auth: WikibaseCloudAuth | None = None,
) -> WikibaseAuthVerifyResult:
    """Smoke-test server OAuth against the configured Wikibase Cloud."""
    resolved = auth or get_server_wikibase_auth()
    if resolved is None:
        return WikibaseAuthVerifyResult(
            ok=False,
            message="Wikibase Cloud OAuth is not configured on this server.",
        )
    settings = get_settings()
    config = server_wikibase_endpoint_config()
    write_user = settings.wikibase_cloud_write_user.strip() or "mhm-pipeline-web"
    writer = WikibaseCloudWriter(
        config,
        resolved,
        user_agent=f"{write_user}/1.0 (MHM Pipeline Web)",
    )
    try:
        writer.ensure_authenticated()
        writer._get_csrf_token()  # noqa: SLF001 — integration smoke test
        api_username = writer.current_api_user()
    except RuntimeError as exc:
        return WikibaseAuthVerifyResult(
            ok=False,
            message=str(exc),
            base_url=config.base_url,
            write_user=write_user,
        )
    if api_username != write_user:
        return WikibaseAuthVerifyResult(
            ok=False,
            message=(
                f"OAuth session is user {api_username!r}, expected "
                f"{write_user!r}. Re-register the OAuth consumer under the "
                f"{write_user} wiki account on {config.base_url}."
            ),
            base_url=config.base_url,
            api_username=api_username,
            write_user=write_user,
        )
    return WikibaseAuthVerifyResult(
        ok=True,
        message=f"Connected to {config.base_url} as {api_username}",
        base_url=config.base_url,
        api_username=api_username,
        write_user=write_user,
    )


async def verify_server_wikibase_auth() -> WikibaseAuthVerifyResult:
    return await asyncio.to_thread(verify_server_wikibase_auth_sync)
