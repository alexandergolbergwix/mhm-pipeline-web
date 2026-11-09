"""Unit tests for smart Wikidata existence + foreign-accept helpers."""

from __future__ import annotations

import urllib.parse

import pytest

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
        "app.pipeline.wikidata_existence._fetch_json_throttled", _fake_get,
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


def test_apply_existence_foreign_on_test_clears_for_create(monkeypatch):
    monkeypatch.setattr(
        "app.pipeline.wikidata_existence.confirm_qid_alive",
        lambda qid, *, is_test=False, **kwargs: True,
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
    assert out.blocked is False
    assert out.existing_qid is None
    assert "cleared_foreign_on_test" in out.method
    assert out.ownership == "absent"


def test_apply_existence_blocks_foreign_without_accept_on_live(monkeypatch):
    monkeypatch.setattr(
        "app.pipeline.wikidata_existence.confirm_qid_alive",
        lambda qid, *, is_test=False, **kwargs: True,
    )

    class _Foreign:
        def _is_our_item(self, qid: str) -> bool:
            return False

    out = _apply_existence_and_ownership(
        _prepared(),
        accept=None,
        ownership_checker=_Foreign(),
        is_test=False,
    )
    assert out.blocked is True
    assert out.block_status == "skipped"
    assert out.ownership == "foreign"
    assert out.existing_qid == "Q50"


def test_apply_existence_allows_own(monkeypatch):
    monkeypatch.setattr(
        "app.pipeline.wikidata_existence.confirm_qid_alive",
        lambda qid, *, is_test=False, **kwargs: True,
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
        lambda qid, *, is_test=False, **kwargs: True,
    )

    class _Foreign:
        def _is_our_item(self, qid: str) -> bool:
            return False

    out = _apply_existence_and_ownership(
        _prepared("Q50"),
        accept=ForeignAccept(accept_foreign_modify=True, accepted_foreign_qid="Q50"),
        ownership_checker=_Foreign(),
        is_test=False,
    )
    assert out.blocked is False
    assert out.allow_foreign_modify is True


def test_apply_existence_accept_without_token(monkeypatch):
    monkeypatch.setattr(
        "app.pipeline.wikidata_existence.confirm_qid_alive",
        lambda qid, *, is_test=False, **kwargs: True,
    )
    out = _apply_existence_and_ownership(
        _prepared("Q50"),
        accept=ForeignAccept(accept_foreign_modify=True, accepted_foreign_qid="Q50"),
        ownership_checker=None,
        is_test=False,
    )
    assert out.blocked is False
    assert out.allow_foreign_modify is True


def test_apply_existence_foreign_accept_ignored_on_test(monkeypatch):
    monkeypatch.setattr(
        "app.pipeline.wikidata_existence.confirm_qid_alive",
        lambda qid, *, is_test=False, **kwargs: True,
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
    assert out.existing_qid is None
    assert not out.allow_foreign_modify


def test_apply_existence_blocks_missing_qid(monkeypatch):
    monkeypatch.setattr(
        "app.pipeline.wikidata_existence.confirm_qid_alive",
        lambda qid, *, is_test=False, **kwargs: False,
    )
    out = _apply_existence_and_ownership(
        _prepared("Q404"),
        accept=ForeignAccept(accept_foreign_modify=True, accepted_foreign_qid="Q404"),
        ownership_checker=None,
        is_test=False,
    )
    assert out.blocked is True
    assert out.block_status == "blocked"


def test_confirm_qids_alive_batches_and_parses(monkeypatch):
    calls: list[str] = []

    def _fake_get(url: str, *, timeout: float = 45.0) -> dict:
        calls.append(url)
        ids = []
        if "ids=" in url:
            qs = url.split("ids=", 1)[1].split("&", 1)[0]
            ids = urllib.parse.unquote(qs).split("|")
        entities = {
            qid: {"id": qid, "title": qid} for qid in ids if qid != "Q404"
        }
        if "Q404" in ids:
            entities["Q404"] = {"missing": ""}
        return {"entities": entities}

    monkeypatch.setattr(
        "app.pipeline.wikidata_existence._fetch_json_throttled", _fake_get,
    )
    from app.pipeline.wikidata_existence import confirm_qids_alive  # noqa: PLC0415

    result = confirm_qids_alive(["Q1", "Q404", "Q2"], is_test=True)
    assert result["Q1"] is True
    assert result["Q2"] is True
    assert result["Q404"] is False
    assert len(calls) == 1


def test_confirm_qid_alive_retries_after_none(monkeypatch):
    state = {"n": 0}

    def _batch(ids, *, is_test=False):
        state["n"] += 1
        if state["n"] == 1:
            return {ids[0]: None}
        return {ids[0]: True}

    monkeypatch.setattr(
        "app.pipeline.wikidata_existence.confirm_qids_alive", _batch,
    )
    monkeypatch.setattr("app.pipeline.wikidata_existence.time.sleep", lambda *_a: None)
    assert confirm_qid_alive("Q1", is_test=True, retries=3) is True
    assert state["n"] == 2


def test_apply_existence_uses_cache_before_live_probe(monkeypatch):
    calls: list[str] = []

    def _probe(qid: str, *, is_test: bool = False, retries: int = 2) -> bool | None:
        calls.append(qid)
        return True

    monkeypatch.setattr(
        "app.pipeline.wikidata_existence.confirm_qid_alive", _probe,
    )

    class _Own:
        def _is_our_item(self, qid: str) -> bool:
            return True

    out = _apply_existence_and_ownership(
        _prepared("Q50"),
        accept=None,
        ownership_checker=_Own(),
        is_test=True,
        existence_cache={"Q50": True},
    )
    assert out.blocked is False
    assert calls == []


def test_apply_existence_test_missing_qid_clears_for_create(monkeypatch):
    monkeypatch.setattr(
        "app.pipeline.wikidata_existence.confirm_qid_alive",
        lambda qid, *, is_test=False, retries=2: False,
    )
    out = _apply_existence_and_ownership(
        _prepared("Q6087391"),
        accept=None,
        ownership_checker=None,
        is_test=True,
        existence_cache={"Q6087391": False},
    )
    assert out.blocked is False
    assert out.existing_qid is None


def test_prefetch_entities_for_upload_populates_labels_p31_and_metadata(monkeypatch):
    from app.pipeline.wikidata_existence import (
        fetch_entity_duplicate_metadata,
        fetch_entity_labels,
        fetch_entity_p31,
        prefetch_entities_for_upload,
    )

    calls: list[str] = []

    def _fake_get(url: str, *, timeout: float = 45.0) -> dict:
        calls.append(url)
        return {
            "entities": {
                "Q777777": {
                    "id": "Q777777",
                    "title": "Q777777",
                    "labels": {"he": {"value": "דרושים"}, "en": {"value": "Derushim"}},
                    "descriptions": {"en": {"value": "Work by Cantarini"}},
                    "claims": {
                        "P31": [
                            {
                                "mainsnak": {
                                    "snaktype": "value",
                                    "datavalue": {"value": {"id": "Q47461344"}},
                                }
                            }
                        ],
                        "P2888": [
                            {
                                "mainsnak": {
                                    "snaktype": "value",
                                    "datavalue": {"value": "https://mhm.example/Item:Q1"},
                                }
                            }
                        ],
                    },
                }
            }
        }

    monkeypatch.setattr(
        "app.pipeline.wikidata_existence._fetch_json_throttled", _fake_get,
    )
    alive, entities = prefetch_entities_for_upload(["Q777777"], is_test=False)
    assert alive["Q777777"] is True
    assert "Q777777" in entities
    assert len(calls) == 1

    # Subsequent calls hit _PREFETCHED_ENTITIES with zero additional HTTP calls.
    assert fetch_entity_labels(["Q777777"], is_test=False) == {
        "Q777777": {"he": "דרושים", "en": "Derushim"}
    }
    assert fetch_entity_p31(["Q777777"], is_test=False) == {"Q777777": ["Q47461344"]}
    meta = fetch_entity_duplicate_metadata(["Q777777"], is_test=False)
    assert meta["Q777777"]["p2888"] == ["https://mhm.example/Item:Q1"]
    assert len(calls) == 1


@pytest.mark.parametrize("claims", [
    None, "absent", "bad", [1], {"P31": "bad"},
    {"P31": [{"mainsnak": {"datavalue": {"value": {"id": "Q1"}}}}]},
])
def test_fetch_p31_omits_incomplete_or_unparseable_claims(monkeypatch, claims):
    from app.pipeline import wikidata_existence as existence

    entity = {"id": "Q123"}
    if claims != "absent":
        entity["claims"] = claims
    monkeypatch.setattr(existence, "_PREFETCHED_ENTITIES", {})
    monkeypatch.setattr(
        existence, "_fetch_json_throttled", lambda *a, **kw: {"entities": {"Q123": entity}},
    )
    assert existence.fetch_entity_p31(["Q123"], require_complete=True) == {}


@pytest.mark.parametrize("snaktype", ["somevalue", "novalue"])
def test_p31_preserves_known_values_when_unknown_snaks_exist(monkeypatch, snaktype):
    from app.pipeline import wikidata_existence as existence

    entity = {"id": "Q123", "claims": {"P31": [
        {"mainsnak": {"snaktype": snaktype}},
        {"mainsnak": {"snaktype": "value", "datavalue": {"value": {"id": "Q5"}}}},
    ]}}
    monkeypatch.setattr(existence, "_PREFETCHED_ENTITIES", {})
    monkeypatch.setattr(
        existence, "_fetch_json_throttled", lambda *a, **kw: {"entities": {"Q123": entity}},
    )
    assert existence.fetch_entity_p31(["Q123"]) == {"Q123": ["Q5"]}
    assert existence.fetch_entity_p31(["Q123"], require_complete=True) == {}


@pytest.mark.parametrize("helper", ["p31", "metadata"])
@pytest.mark.parametrize("bad_claims", [None, {"P31": "invalid"}])
def test_invalid_cache_retries_entity_fetch(monkeypatch, helper, bad_claims):
    from app.pipeline import wikidata_existence as existence

    cached = {"id": "Q123", "claims": bad_claims, "descriptions": {}}
    valid = {"id": "Q123", "claims": {}, "descriptions": {}}
    monkeypatch.setattr(existence, "_PREFETCHED_ENTITIES", {(False, "Q123"): cached})
    calls = []

    def fetch(*args, **kwargs):
        calls.append(True)
        return {"entities": {"Q123": valid}}

    monkeypatch.setattr(existence, "_fetch_json_throttled", fetch)
    if helper == "p31":
        assert existence.fetch_entity_p31(["Q123"], require_complete=True) == {"Q123": []}
    else:
        assert existence.fetch_entity_duplicate_metadata(["Q123"])["Q123"]["authors"] == []
    assert calls == [True]


@pytest.mark.parametrize("claims", [{}, []])
@pytest.mark.parametrize("cached", [False, True])
def test_empty_claims_are_known_for_p31_and_metadata(monkeypatch, claims, cached):
    from app.pipeline import wikidata_existence as existence

    entity = {"id": "Q123", "claims": claims, "descriptions": {}}
    monkeypatch.setattr(
        existence, "_PREFETCHED_ENTITIES", {(False, "Q123"): entity} if cached else {},
    )
    monkeypatch.setattr(
        existence, "_fetch_json_throttled", lambda *a, **kw: {"entities": {"Q123": entity}},
    )
    assert existence.fetch_entity_p31(["Q123"]) == {"Q123": []}
    assert existence.fetch_entity_duplicate_metadata(["Q123"])["Q123"]["author_qids"] == []


@pytest.mark.parametrize("claims", [None, "absent", "bad", [1]])
@pytest.mark.parametrize("cached", [False, True])
def test_duplicate_metadata_omits_incomplete_claims(monkeypatch, claims, cached):
    from app.pipeline import wikidata_existence as existence

    entity = {"id": "Q123", "descriptions": {}}
    if claims != "absent":
        entity["claims"] = claims
    monkeypatch.setattr(
        existence, "_PREFETCHED_ENTITIES", {(False, "Q123"): entity} if cached else {},
    )
    monkeypatch.setattr(
        existence, "_fetch_json_throttled", lambda *a, **kw: {"entities": {"Q123": entity}},
    )
    assert existence.fetch_entity_duplicate_metadata(["Q123"]) == {}
