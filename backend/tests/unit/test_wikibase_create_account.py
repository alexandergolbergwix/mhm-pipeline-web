"""Unit tests for wiki account provisioning helpers."""

from __future__ import annotations

from unittest.mock import MagicMock

from converter.wikibase.cloud_client import CreateAccountOutcome, WikibaseCloudWriter


def test_create_local_account_exists_short_circuits() -> None:
    writer = MagicMock(spec=WikibaseCloudWriter)
    writer.wiki_user_exists.return_value = True
    writer.create_local_account = WikibaseCloudWriter.create_local_account.__get__(
        writer, WikibaseCloudWriter,
    )
    # Bind real method but mock wiki_user_exists via instance
    real_writer = object.__new__(WikibaseCloudWriter)
    real_writer._config = MagicMock()
    real_writer._config.base_url = "https://mhm-hmo.wikibase.cloud"
    real_writer._post_with_retry = MagicMock()
    real_writer._logged_in = True
    real_writer.ensure_authenticated = MagicMock()
    real_writer.wiki_user_exists = MagicMock(return_value=True)

    outcome = WikibaseCloudWriter.create_local_account(
        real_writer, "alice@example.com", "secret", email="alice@example.com",
    )
    assert outcome.status == "exists"
    assert outcome.ok is True


def test_create_local_account_success() -> None:
    real_writer = object.__new__(WikibaseCloudWriter)
    real_writer._config = MagicMock()
    real_writer._config.base_url = "https://mhm-hmo.wikibase.cloud"
    real_writer._logged_in = True
    real_writer.ensure_authenticated = MagicMock()
    real_writer.wiki_user_exists = MagicMock(return_value=False)
    real_writer._post_with_retry = MagicMock(side_effect=[
        {"query": {"tokens": {"createaccounttoken": "tok"}}},
        {"createaccount": {"status": "PASS"}},
    ])

    outcome = WikibaseCloudWriter.create_local_account(
        real_writer, "alice@example.com", "secret", email="alice@example.com",
    )
    assert outcome.status == "created"
    assert outcome.ok is True
