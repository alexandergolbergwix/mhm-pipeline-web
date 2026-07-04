"""Wikibase Cloud bot credential verification in Settings."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.services.wikibase_credentials import (
    WikibaseCredentialVerifyResult,
    verify_wikibase_bot_credentials_sync,
)
from converter.wikibase.cloud_client import WikibaseBotCredentials


def test_verify_rejects_username_with_at_sign() -> None:
    result = verify_wikibase_bot_credentials_sync(
        WikibaseBotCredentials(username="Alex@mhm-pipeline", bot_name="mhm-pipeline", password="x"),
    )
    assert result.ok is False
    assert "account name only" in result.message


@pytest.mark.asyncio
async def test_verify_endpoint_requires_auth(async_client) -> None:
    response = await async_client.post("/api/me/api-keys/wikibase-cloud/verify", json={})
    assert response.status_code in (401, 403)


@pytest.mark.asyncio
async def test_verify_reports_missing_credentials(auth_user) -> None:
    _user, client = auth_user
    response = await client.post("/api/me/api-keys/wikibase-cloud/verify", json={})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "username and password" in body["message"].lower()


@pytest.mark.asyncio
async def test_verify_returns_login_result(
    auth_user, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _user, client = auth_user
    monkeypatch.setattr(
        "app.routers.api_keys.verify_wikibase_bot_credentials",
        AsyncMock(return_value=WikibaseCredentialVerifyResult(
            ok=True,
            message="Login successful",
            login_name="curator@mhm-pipeline",
        )),
    )
    response = await client.post("/api/me/api-keys/wikibase-cloud/verify", json={})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["login_name"] == "curator@mhm-pipeline"


@pytest.mark.asyncio
async def test_set_password_rejects_bad_login(
    auth_user, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _user, client = auth_user

    username_resp = await client.put(
        "/api/me/api-keys/wikibase_cloud_bot_username",
        json={"value": "curator"},
    )
    assert username_resp.status_code == 200

    monkeypatch.setattr(
        "app.routers.api_keys.verify_wikibase_bot_credentials_sync",
        lambda _creds: WikibaseCredentialVerifyResult(
            ok=False,
            message="Incorrect username or password entered. Please try again.",
        ),
    )

    password_resp = await client.put(
        "/api/me/api-keys/wikibase_cloud_bot_password",
        json={"value": "wrong-password"},
    )
    assert password_resp.status_code == 400
    assert "Incorrect username or password" in password_resp.json()["detail"]


@pytest.mark.asyncio
async def test_set_password_succeeds_when_login_verifies(
    auth_user, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _user, client = auth_user

    await client.put(
        "/api/me/api-keys/wikibase_cloud_bot_username",
        json={"value": "curator"},
    )

    monkeypatch.setattr(
        "app.routers.api_keys.verify_wikibase_bot_credentials_sync",
        lambda _creds: WikibaseCredentialVerifyResult(
            ok=True,
            message="Login successful",
            login_name="curator@mhm-pipeline",
        ),
    )

    password_resp = await client.put(
        "/api/me/api-keys/wikibase_cloud_bot_password",
        json={"value": "good-password"},
    )
    assert password_resp.status_code == 200
    assert password_resp.json()["set"] is True


@pytest.mark.asyncio
async def test_set_username_without_password_skips_verify(
    auth_user, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _user, client = auth_user
    called = {"verify": False}

    def _fail_verify(_creds):
        called["verify"] = True
        return WikibaseCredentialVerifyResult(ok=False, message="should not run")

    monkeypatch.setattr(
        "app.routers.api_keys.verify_wikibase_bot_credentials_sync",
        _fail_verify,
    )

    response = await client.put(
        "/api/me/api-keys/wikibase_cloud_bot_username",
        json={"value": "curator"},
    )
    assert response.status_code == 200
    assert called["verify"] is False
