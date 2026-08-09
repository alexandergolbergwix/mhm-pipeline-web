"""Wikidata auth token normalize / validate."""

from __future__ import annotations

from converter.wikidata.auth_token import (
    normalize_wikidata_auth_token,
    wikidata_auth_token_format_ok,
)


def test_normalize_joins_two_line_botpasswords_paste() -> None:
    raw = "Alexander Goldberg IL@MHMPipelineTest\nabcdefghijklmnop"
    assert normalize_wikidata_auth_token(raw) == (
        "Alexander Goldberg IL@MHMPipelineTest:abcdefghijklmnop"
    )
    assert wikidata_auth_token_format_ok(raw)


def test_bot_name_alone_is_invalid() -> None:
    assert not wikidata_auth_token_format_ok(
        "Alexander Goldberg IL@MHMPipelineTest",
    )


def test_password_alone_is_invalid() -> None:
    assert not wikidata_auth_token_format_ok("abcdefghijklmnop")


def test_full_bot_password_is_valid() -> None:
    assert wikidata_auth_token_format_ok(
        "Alexander Goldberg IL@MHMPipelineTest:abcdefghijklmnop",
    )
