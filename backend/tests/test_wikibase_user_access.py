"""Per-user Wikibase access on login / invite acceptance."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select

from app.models.wikibase_user_access import (
    WIKI_ACCOUNT_ACTIVE,
    WIKI_ACCOUNT_FAILED,
    WIKI_ACCOUNT_NONE,
    WIKI_ACCOUNT_SKIPPED,
    WikibaseUserAccess,
)
from app.services.wikibase_user_access import (
    ensure_wikibase_access,
    snapshot_from_row,
    wiki_username_from_email,
)
from converter.wikibase.cloud_client import CreateAccountOutcome


def test_wiki_username_from_email_uses_email_when_valid() -> None:
    assert wiki_username_from_email("Shvedbook@gmail.com") == "Shvedbook@gmail.com"


def test_wiki_username_from_email_sanitizes_invalid_chars() -> None:
    assert wiki_username_from_email("bad#name@example.com") == "badname@example.com"


def test_snapshot_from_row_unconfigured() -> None:
    snap = snapshot_from_row(None, configured=False, base_url=None)
    assert snap.wikibase_configured is False
    assert snap.wikibase_authorized is False


@pytest.mark.asyncio
async def test_ensure_wikibase_access_grants_when_oauth_configured(
    db_session,
    auth_user,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, _client = auth_user
    monkeypatch.setenv("WIKIBASE_CLOUD_OAUTH_ACCESS_TOKEN", "jwt")
    monkeypatch.setenv("WIKIBASE_CLOUD_OAUTH_CLIENT_ID", "")
    monkeypatch.setenv("WIKIBASE_CLOUD_OAUTH_CLIENT_SECRET", "")
    from app.settings import get_settings

    get_settings.cache_clear()

    fake_writer = MagicMock()
    fake_writer.create_local_account.return_value = CreateAccountOutcome(
        ok=True,
        username="test@example.com",
        status="created",
        message="ok",
    )
    with patch(
        "app.services.wikibase_user_access.WikibaseCloudWriter",
        return_value=fake_writer,
    ):
        snap = await ensure_wikibase_access(
            db_session,
            user=user,
            email="test@example.com",
            attempt_provision=True,
        )
    await db_session.commit()

    assert snap.wikibase_configured is True
    assert snap.wikibase_authorized is True
    assert snap.wikibase_wiki_account_status == WIKI_ACCOUNT_ACTIVE

    row = (
        await db_session.execute(
            select(WikibaseUserAccess).where(WikibaseUserAccess.user_id == user.id)
        )
    ).scalar_one()
    assert row.app_authorized is True
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_ensure_wikibase_access_skips_when_unconfigured(
    db_session,
    auth_user,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, _client = auth_user
    monkeypatch.setenv("WIKIBASE_CLOUD_OAUTH_ACCESS_TOKEN", "")
    monkeypatch.setenv("WIKIBASE_CLOUD_OAUTH_CLIENT_ID", "")
    monkeypatch.setenv("WIKIBASE_CLOUD_OAUTH_CLIENT_SECRET", "")
    from app.settings import get_settings

    get_settings.cache_clear()
    snap = await ensure_wikibase_access(
        db_session,
        user=user,
        email="test@example.com",
    )
    assert snap.wikibase_configured is False
    assert snap.wikibase_wiki_account_status == WIKI_ACCOUNT_NONE
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_ensure_wikibase_access_permission_denied_still_authorizes_app(
    db_session,
    auth_user,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, _client = auth_user
    monkeypatch.setenv("WIKIBASE_CLOUD_OAUTH_ACCESS_TOKEN", "jwt")
    from app.settings import get_settings

    get_settings.cache_clear()
    fake_writer = MagicMock()
    fake_writer.create_local_account.return_value = CreateAccountOutcome(
        ok=False,
        username="test@example.com",
        status="failed",
        message="permission denied for createaccount",
    )
    with patch(
        "app.services.wikibase_user_access.WikibaseCloudWriter",
        return_value=fake_writer,
    ):
        snap = await ensure_wikibase_access(
            db_session,
            user=user,
            email="test@example.com",
            attempt_provision=True,
        )
    assert snap.wikibase_authorized is True
    assert snap.wikibase_wiki_account_status == WIKI_ACCOUNT_SKIPPED
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_ensure_wikibase_access_does_not_retry_failed_status(
    db_session,
    auth_user,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, _client = auth_user
    monkeypatch.setenv("WIKIBASE_CLOUD_OAUTH_ACCESS_TOKEN", "jwt")
    from app.settings import get_settings

    get_settings.cache_clear()
    db_session.add(
        WikibaseUserAccess(
            user_id=user.id,
            app_authorized=True,
            wiki_username="test@example.com",
            wiki_account_status=WIKI_ACCOUNT_FAILED,
            wiki_account_error="All retries exhausted: None",
        )
    )
    await db_session.commit()

    with patch(
        "app.services.wikibase_user_access.WikibaseCloudWriter",
    ) as mock_writer_cls:
        snap = await ensure_wikibase_access(
            db_session,
            user=user,
            email="test@example.com",
            attempt_provision=True,
        )
    assert snap.wikibase_authorized is True
    assert snap.wikibase_wiki_account_status == WIKI_ACCOUNT_FAILED
    mock_writer_cls.assert_not_called()
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_ensure_wikibase_access_me_path_skips_provision(
    db_session,
    auth_user,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, _client = auth_user
    monkeypatch.setenv("WIKIBASE_CLOUD_OAUTH_ACCESS_TOKEN", "jwt")
    from app.settings import get_settings

    get_settings.cache_clear()
    with patch(
        "app.services.wikibase_user_access.WikibaseCloudWriter",
    ) as mock_writer_cls:
        snap = await ensure_wikibase_access(
            db_session,
            user=user,
            email="test@example.com",
            attempt_provision=False,
        )
    assert snap.wikibase_authorized is True
    mock_writer_cls.assert_not_called()
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_ensure_wikibase_access_provision_timeout_marks_failed(
    db_session,
    auth_user,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, _client = auth_user
    monkeypatch.setenv("WIKIBASE_CLOUD_OAUTH_ACCESS_TOKEN", "jwt")
    from app.settings import get_settings

    get_settings.cache_clear()

    def _hang(*_args: object, **_kwargs: object) -> tuple[str, str | None]:
        import time

        time.sleep(2.0)
        return WIKI_ACCOUNT_ACTIVE, None

    monkeypatch.setattr(
        "app.services.wikibase_user_access._PROVISION_BUDGET_SECONDS",
        0.05,
    )
    with patch(
        "app.services.wikibase_user_access._provision_wiki_account_sync",
        side_effect=_hang,
    ):
        snap = await ensure_wikibase_access(
            db_session,
            user=user,
            email="test@example.com",
            attempt_provision=True,
        )
    assert snap.wikibase_authorized is True
    assert snap.wikibase_wiki_account_status == WIKI_ACCOUNT_FAILED
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_login_returns_wikibase_fields(
    auth_user,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.crypto import pii

    user, client = auth_user
    email = pii.decrypt_pii(user.email_encrypted)
    monkeypatch.setenv("WIKIBASE_CLOUD_OAUTH_ACCESS_TOKEN", "jwt")
    from app.settings import get_settings

    get_settings.cache_clear()
    with patch(
        "app.services.wikibase_user_access.WikibaseCloudWriter",
    ) as mock_writer_cls:
        mock_writer_cls.return_value.create_local_account.return_value = (
            CreateAccountOutcome(
                ok=True,
                username=email,
                status="exists",
                message="account already exists",
            )
        )
        resp = await client.post(
            "/api/auth/login",
            json={"email": email, "password": user._test_password},  # noqa: SLF001
        )
    get_settings.cache_clear()
    assert resp.status_code == 200
    body = resp.json()
    assert body["wikibase_configured"] is True
    assert body["wikibase_authorized"] is True
    assert body["wikibase_base_url"] == "https://mhm-hmo.wikibase.cloud"
