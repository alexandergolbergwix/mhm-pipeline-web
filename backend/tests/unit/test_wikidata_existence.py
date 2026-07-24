"""Unit tests for smart Wikidata existence + foreign-accept helpers."""

from __future__ import annotations

from app.pipeline.wikidata_existence import (
    accept_allows_foreign_modify,
    classify_ownership_with_uploader,
    confirm_qid_alive,
)
from app.pipeline.wikidata_upload import ForeignAccept, PreparedItem, _apply_existence_and_ownership


def test_accept_allows_foreign_modify_requires_matching_qid():
    assert accept_allows_foreign_modify(
        existing_qid="Q42",
        accept_foreign_modify=True,
        accepted_foreign_qid="Q42",
    )
    assert not accept_allows_foreign_modify(
        existing_qid="Q42",
        accept_foreign_modify=True,
        accepted_foreign_qid="Q99",
    )
    assert not accept_allows_foreign_modify(
        existing_qid="Q42",
        accept_foreign_modify=False,
        accepted_foreign_qid="Q42",
    )


def test_classify_ownership_with_uploader():
    class _Own:
        def _is_our_item(self, qid: str) -> bool:
            return qid == "Q1"

    assert classify_ownership_with_uploader(_Own(), "Q1") == "own"
    assert classify_ownership_with_uploader(_Own(), "Q2") == "foreign"
    assert classify_ownership_with_uploader(object(), "Q1") == "unknown"


def test_confirm_qid_alive_parses_entities(monkeypatch):
    def _fake_get(url: str, *, timeout: float = 30.0) -> dict:
        assert "wbgetentities" in url
        if "Q404" in url:
            return {"entities": {"Q404": {"missing": ""}}}
        return {"entities": {"Q1": {"id": "Q1", "title": "Q1"}}}

    monkeypatch.setattr(
        "app.pipeline.wikidata_existence._get_json", _fake_get,
    )
    assert confirm_qid_alive("Q1") is True
    assert confirm_qid_alive("Q404") is False
    assert confirm_qid_alive("not-a-qid") is False


def _prepared(qid: str | None = "Q50") -> PreparedItem:
    return PreparedItem(
        item=object(),
        local_id="ms1",
        label="MS",
        entity_type="manuscript",
        existing_qid=qid,
        method="ledger",
        blocked=False,
        block_status="",
        block_message="",
    )


def test_apply_existence_blocks_foreign_without_accept(monkeypatch):
    monkeypatch.setattr(
        "app.pipeline.wikidata_existence.confirm_qid_alive",
        lambda qid, *, is_test=False: True,
    )

    class _Foreign:
        def _is_our_item(self, qid: str) -> bool:
            return False

    out = _apply_existence_and_ownership(
        _prepared(),
        accept=None,
        ownership_checker=_Foreign(),
        is_test=True,
    )
    assert out.blocked is True
    assert out.block_status == "skipped"
    assert out.ownership == "foreign"


def test_apply_existence_allows_own(monkeypatch):
    monkeypatch.setattr(
        "app.pipeline.wikidata_existence.confirm_qid_alive",
        lambda qid, *, is_test=False: True,
    )

    class _Own:
        def _is_our_item(self, qid: str) -> bool:
            return True

    out = _apply_existence_and_ownership(
        _prepared(),
        accept=None,
        ownership_checker=_Own(),
        is_test=True,
    )
    assert out.blocked is False
    assert out.ownership == "own"


def test_apply_existence_allows_foreign_with_qid_bound_accept(monkeypatch):
    monkeypatch.setattr(
        "app.pipeline.wikidata_existence.confirm_qid_alive",
        lambda qid, *, is_test=False: True,
    )

    class _Foreign:
        def _is_our_item(self, qid: str) -> bool:
            return False

    out = _apply_existence_and_ownership(
        _prepared("Q50"),
        accept=ForeignAccept(accept_foreign_modify=True, accepted_foreign_qid="Q50"),
        ownership_checker=_Foreign(),
        is_test=True,
    )
    assert out.blocked is False
    assert out.allow_foreign_modify is True


def test_apply_existence_accept_without_token(monkeypatch):
    monkeypatch.setattr(
        "app.pipeline.wikidata_existence.confirm_qid_alive",
        lambda qid, *, is_test=False: True,
    )
    out = _apply_existence_and_ownership(
        _prepared("Q50"),
        accept=ForeignAccept(accept_foreign_modify=True, accepted_foreign_qid="Q50"),
        ownership_checker=None,
        is_test=True,
    )
    assert out.blocked is False
    assert out.allow_foreign_modify is True


def test_apply_existence_blocks_missing_qid(monkeypatch):
    monkeypatch.setattr(
        "app.pipeline.wikidata_existence.confirm_qid_alive",
        lambda qid, *, is_test=False: False,
    )
    out = _apply_existence_and_ownership(
        _prepared("Q404"),
        accept=ForeignAccept(accept_foreign_modify=True, accepted_foreign_qid="Q404"),
        ownership_checker=None,
        is_test=True,
    )
    assert out.blocked is True
    assert out.block_status == "blocked"
