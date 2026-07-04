"""Unit tests for WikibaseCloudWriter OAuth wiring (no live network)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from converter.wikibase.cloud_client import (
    WikibaseBotCredentials,
    WikibaseCloudAuth,
    WikibaseCloudWriter,
    WikibaseEndpointConfig,
)


def _writer_with_oauth() -> WikibaseCloudWriter:
    config = WikibaseEndpointConfig(base_url="https://example.wikibase.cloud")
    auth = WikibaseCloudAuth(
        mode="oauth2",
        client_id="cid",
        client_secret="csecret",
        access_token=None,
    )
    return WikibaseCloudWriter(config, auth)


def _writer_with_bot() -> WikibaseCloudWriter:
    config = WikibaseEndpointConfig(base_url="https://example.wikibase.cloud")
    auth = WikibaseBotCredentials(username="u", bot_name="b", password="p")
    return WikibaseCloudWriter(config, auth)


def test_init_wbi_oauth_uses_oauth2_login() -> None:
    writer = _writer_with_oauth()
    fake_login = MagicMock()
    fake_wbi = MagicMock()
    with (
        patch("wikibaseintegrator.WikibaseIntegrator", return_value=fake_wbi) as mock_wbi_cls,
        patch("wikibaseintegrator.wbi_login.OAuth2", return_value=fake_login) as mock_oauth,
    ):
        wbi = writer._init_wbi()  # noqa: SLF001
    mock_oauth.assert_called_once()
    mock_wbi_cls.assert_called_once_with(login=fake_login)
    assert wbi is fake_wbi


def test_init_wbi_bearer_uses_login_session() -> None:
    config = WikibaseEndpointConfig(base_url="https://example.wikibase.cloud")
    auth = WikibaseCloudAuth(
        mode="oauth2",
        client_id="cid",
        client_secret="csecret",
        access_token="jwt",
    )
    writer = WikibaseCloudWriter(config, auth)
    fake_login = MagicMock()
    with (
        patch("wikibaseintegrator.WikibaseIntegrator", return_value=MagicMock()),
        patch("wikibaseintegrator.wbi_login._Login", return_value=fake_login) as mock_login,  # noqa: SLF001
    ):
        writer._init_wbi()  # noqa: SLF001
    mock_login.assert_called_once()
    session = mock_login.call_args.kwargs["session"]
    assert session.headers["Authorization"] == "Bearer jwt"


def test_edit_page_oauth_omits_assert_bot() -> None:
    writer = _writer_with_oauth()
    captured: dict = {}

    def _post(params: dict) -> dict:
        captured.update(params)
        return {"edit": {"result": "Success", "pageid": 1, "newrevid": 2}}

    writer.read_page = MagicMock(return_value=None)  # type: ignore[method-assign]
    writer._get_csrf_token = MagicMock(return_value="csrf")  # type: ignore[method-assign]
    writer._post_with_retry = _post  # type: ignore[method-assign]

    outcome = writer.edit_page("IIIF:MS1", "{}", "summary")
    assert outcome.status == "created"
    assert "assert" not in captured
    assert "bot" not in captured


def test_edit_page_bot_includes_assert_bot() -> None:
    writer = _writer_with_bot()
    captured: dict = {}

    def _post(params: dict) -> dict:
        captured.update(params)
        return {"edit": {"result": "Success", "pageid": 1, "newrevid": 2}}

    writer.read_page = MagicMock(return_value=None)  # type: ignore[method-assign]
    writer._get_csrf_token = MagicMock(return_value="csrf")  # type: ignore[method-assign]
    writer._post_with_retry = _post  # type: ignore[method-assign]

    writer.edit_page("IIIF:MS1", "{}", "summary")
    assert captured.get("assert") == "bot"
    assert captured.get("bot") == "1"
