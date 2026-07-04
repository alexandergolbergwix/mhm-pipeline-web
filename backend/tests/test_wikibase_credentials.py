"""Server-held Wikibase Cloud OAuth credential resolver."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.services.wikibase_credentials import (
    build_server_wikibase_writer,
    get_server_wikibase_auth,
    verify_server_wikibase_auth_sync,
)
from converter.wikibase.cloud_client import WikibaseCloudAuth


def test_get_server_wikibase_auth_none_when_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WIKIBASE_CLOUD_OAUTH_CLIENT_ID", "")
    monkeypatch.setenv("WIKIBASE_CLOUD_OAUTH_CLIENT_SECRET", "")
    monkeypatch.setenv("WIKIBASE_CLOUD_OAUTH_ACCESS_TOKEN", "")
    from app.settings import get_settings

    get_settings.cache_clear()
    assert get_server_wikibase_auth() is None
    get_settings.cache_clear()


def test_get_server_wikibase_auth_from_client_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WIKIBASE_CLOUD_OAUTH_CLIENT_ID", "client-id")
    monkeypatch.setenv("WIKIBASE_CLOUD_OAUTH_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("WIKIBASE_CLOUD_OAUTH_ACCESS_TOKEN", "")
    from app.settings import get_settings

    get_settings.cache_clear()
    auth = get_server_wikibase_auth()
    assert auth is not None
    assert auth.mode == "oauth2"
    assert auth.client_id == "client-id"
    assert auth.client_secret == "client-secret"
    assert auth.access_token is None
    get_settings.cache_clear()


def test_get_server_wikibase_auth_from_access_token_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WIKIBASE_CLOUD_OAUTH_CLIENT_ID", "")
    monkeypatch.setenv("WIKIBASE_CLOUD_OAUTH_CLIENT_SECRET", "")
    monkeypatch.setenv("WIKIBASE_CLOUD_OAUTH_ACCESS_TOKEN", "jwt-token")
    from app.settings import get_settings

    get_settings.cache_clear()
    auth = get_server_wikibase_auth()
    assert auth is not None
    assert auth.access_token == "jwt-token"
    get_settings.cache_clear()


def test_build_server_wikibase_writer_raises_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WIKIBASE_CLOUD_OAUTH_CLIENT_ID", "")
    monkeypatch.setenv("WIKIBASE_CLOUD_OAUTH_CLIENT_SECRET", "")
    monkeypatch.setenv("WIKIBASE_CLOUD_OAUTH_ACCESS_TOKEN", "")
    from app.settings import get_settings

    get_settings.cache_clear()
    with pytest.raises(HTTPException) as exc:
        build_server_wikibase_writer()
    assert exc.value.status_code == 503
    get_settings.cache_clear()


def test_verify_server_wikibase_auth_sync_not_configured() -> None:
    with patch(
        "app.services.wikibase_credentials.get_server_wikibase_auth",
        return_value=None,
    ):
        result = verify_server_wikibase_auth_sync()
    assert result.ok is False
    assert "not configured" in result.message.lower()


def test_verify_server_wikibase_auth_sync_success() -> None:
    auth = WikibaseCloudAuth(
        mode="oauth2",
        client_id="id",
        client_secret="secret",
        access_token="tok",
    )
    fake_writer = MagicMock()
    fake_writer.current_api_user.return_value = "mhm-pipeline-web"
    with patch(
        "app.services.wikibase_credentials.WikibaseCloudWriter",
        return_value=fake_writer,
    ):
        result = verify_server_wikibase_auth_sync(auth=auth)
    assert result.ok is True
    fake_writer.ensure_authenticated.assert_called_once()
    fake_writer._get_csrf_token.assert_called_once()
    fake_writer.current_api_user.assert_called_once()
