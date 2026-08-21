"""Claim-complete writes (Rule W-191)."""

from __future__ import annotations

from converter.wikidata.item_models import WikidataItem, WikidataStatement
from converter.wikidata.uploader import (
    ClaimBuildError,
    partition_unresolved_local,
    resolve_local_statement_refs,
    sort_items_for_upload,
)


def test_sort_items_for_upload_works_persons_manuscripts() -> None:
    items = [
        WikidataItem(local_id="m1", entity_type="manuscript"),
        WikidataItem(local_id="w1", entity_type="work"),
        WikidataItem(local_id="p1", entity_type="person"),
    ]
    ordered = sort_items_for_upload(items)
    assert [it.entity_type for it in ordered] == ["work", "person", "manuscript"]


def test_resolve_local_statement_refs_rewrites_and_reports_leftovers() -> None:
    item = WikidataItem(
        local_id="ms1",
        entity_type="manuscript",
        statements=[
            WikidataStatement(
                property_id="P1574",
                value="__LOCAL:work:a",
                value_type="item",
            ),
            WikidataStatement(
                property_id="P50",
                value="__LOCAL:person:missing",
                value_type="item",
            ),
        ],
    )
    leftover = resolve_local_statement_refs(item, {"work:a": "Q100"})
    assert item.statements[0].value == "Q100"
    assert leftover == ["__LOCAL:person:missing"]


def test_build_claim_accepts_wikibase_item_value_type() -> None:
    from converter.wikidata.uploader import WikidataUploader

    up = WikidataUploader(
        token="user@bot:xxxxxxxxxxxxxxxx",
        is_test=True,
        allow_live=False,
    )
    stmt = WikidataStatement(
        property_id="P15",
        value="Q248937",
        value_type="wikibase-item",
    )
    claim = up._build_claim(stmt)
    assert claim is not None
    assert claim.mainsnak.datavalue["value"]["id"] == "Q248937"


def test_upload_item_blocks_unresolved_local_when_not_partitioned(monkeypatch) -> None:
    from converter.wikidata.uploader import WikidataUploader

    up = WikidataUploader(
        token="user@bot:xxxxxxxxxxxxxxxx",
        is_test=True,
        allow_live=False,
    )
    monkeypatch.setattr(up, "_check_moratorium_for_live", lambda: None)
    monkeypatch.setattr(up, "_init_wbi", lambda: None)
    item = WikidataItem(
        local_id="ms1",
        entity_type="manuscript",
        statements=[
            WikidataStatement(
                property_id="P1574",
                value="__LOCAL:work:gone",
                value_type="item",
            ),
        ],
    )
    result = up.upload_item(item, created_qids={})
    assert result.status == "blocked"
    assert "W-191" in result.message


def test_upload_item_blocks_claim_build_error(monkeypatch) -> None:
    from converter.wikidata.uploader import ClaimBuildError, WikidataUploader

    up = WikidataUploader(
        token="user@bot:xxxxxxxxxxxxxxxx",
        is_test=False,
        allow_live=True,
    )
    monkeypatch.setattr(up, "_check_moratorium_for_live", lambda: None)
    monkeypatch.setattr(up, "_init_wbi", lambda: None)
    monkeypatch.setattr(up, "_rate_limit", lambda: None)
    monkeypatch.setattr(up, "_assert_modifiable", lambda *_a, **_k: None)
    monkeypatch.setattr(
        up,
        "_build_wbi_item",
        lambda _item: (_ for _ in ()).throw(ClaimBuildError(["P1559=x build-failed"])),
    )
    item = WikidataItem(
        local_id="p1",
        entity_type="person",
        statements=[
            WikidataStatement(property_id="P1559", value="n", value_type="monolingualtext"),
        ],
    )
    result = up.upload_item(item)
    assert result.status == "blocked"
    assert "W-191" in result.message

    err = ClaimBuildError(["P1559=x build-failed"])
    assert "P1559" in str(err)


def test_partitioned_write_item_has_no_local_leftover() -> None:
    item = WikidataItem(
        local_id="w1",
        entity_type="work",
        statements=[
            WikidataStatement(property_id="P31", value="Q47461344", value_type="item"),
            WikidataStatement(
                property_id="P50", value="__LOCAL:person:gone", value_type="item",
            ),
        ],
    )
    write, deferred = partition_unresolved_local(item, {})
    leftover = resolve_local_statement_refs(write, {})
    assert leftover == []
    assert deferred[0].value == "__LOCAL:person:gone"
