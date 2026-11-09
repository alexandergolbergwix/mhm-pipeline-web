"""Claim-complete writes (Rule W-191)."""

from __future__ import annotations

from converter.wikidata.item_models import WikidataItem, WikidataStatement
from converter.wikidata.uploader import (
    ClaimBuildError,
    partition_unresolved_local,
    resolve_local_statement_refs,
    sort_items_for_upload,
)
import pytest


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
        is_test=False,
        allow_live=True,
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


def test_named_unknown_scribe_has_a_valid_wikibase_snak() -> None:
    from converter.wikidata.uploader import WikidataUploader

    uploader = WikidataUploader(
        token="Fixture@Bot:not-a-credential", is_test=False, allow_live=True,
    )
    claim = uploader._build_claim(WikidataStatement(
        property_id="P11603", value=None, value_type="somevalue",
        qualifiers=[{"property": "P1932", "value": "שמואל(?)", "type": "string"},
                    {"property": "P1480", "value": "Q30230067", "type": "item"}],
        references=[{"property": "P3959", "value": "990000802880205171", "type": "external-id"}],
    ))
    assert claim is not None
    data = claim.get_json()
    assert data["mainsnak"]["snaktype"] == "somevalue"
    assert "datavalue" not in data["mainsnak"]
    assert data["qualifiers"]["P1932"][0]["datavalue"]["value"] == "שמואל(?)"
    assert data["references"]


def test_owned_update_deduplicates_references_without_losing_a_distinct_source(monkeypatch) -> None:
    import copy
    from types import SimpleNamespace

    from wikibaseintegrator import WikibaseIntegrator

    from converter.wikidata.uploader import WikidataUploader

    uploader = WikidataUploader(
        token="Fixture@Bot:not-a-credential", is_test=False, allow_live=True,
    )
    native = WikidataStatement(
        property_id="P571", value="+1650-00-00T00:00:00Z", value_type="time",
        references=[{"property": "P854", "value": "https://example.org/catalog", "type": "url"}],
    )
    current_claim = uploader._build_claim(native)
    assert current_claim is not None
    data = current_claim.get_json()
    data["id"] = "Q123$00000000-0000-0000-0000-000000000001"
    reference = data["references"][0]
    reference["hash"] = "a" * 40
    second_reference = copy.deepcopy(reference)
    second_reference["hash"] = "b" * 40
    second_reference["snaks"]["P854"][0]["datavalue"]["value"] = "https://example.org/second-source"
    data["references"] = [reference, copy.deepcopy(reference), second_reference]
    wbi = WikibaseIntegrator()
    existing = wbi.item.new().from_json({
        "id": "Q123", "type": "item", "lastrevid": 1,
        "labels": {}, "descriptions": {}, "aliases": {},
        "claims": {"P571": [data]}, "sitelinks": {},
    })
    monkeypatch.setattr(
        uploader, "_init_wbi",
        lambda: SimpleNamespace(item=SimpleNamespace(get=lambda *args, **kwargs: existing)),
    )
    uploader._is_our_item_cache["Q123"] = True
    item = WikidataItem(existing_qid="Q123", entity_type="manuscript", statements=[
        WikidataStatement(property_id="P31", value="Q87167", value_type="item"), native,
    ])
    updated, _, properties = uploader._build_wbi_item(item, read_only=True)
    references = updated.claims.get("P571")[0].get_json()["references"]
    assert len(references) == 2
    assert {r["snaks"]["P854"][0]["datavalue"]["value"] for r in references} == {
        "https://example.org/catalog", "https://example.org/second-source",
    }
    assert "P571" in properties


def test_reference_compiler_ignores_display_metadata_when_it_removes_duplicates() -> None:
    from converter.wikidata.uploader import WikidataUploader

    uploader = WikidataUploader(
        token="Fixture@Bot:not-a-credential", is_test=True, allow_live=False,
    )
    source = {"property": "P854", "value": "https://example.org/catalog", "type": "url"}
    claim = uploader._build_claim(WikidataStatement(
        property_id="P11603", value=None, value_type="somevalue",
        references=[source, {**source, "property_id": "P854", "property_label": "reference URL"}],
        reference_blocks=[[source, {**source, "property_id": "P854", "property_label": "reference URL"}]],
    ))
    assert claim is not None
    assert len(claim.get_json()["references"][0]["snaks"]["P854"]) == 1


def test_owned_item_label_conflict_preserves_its_existing_label() -> None:
    from types import SimpleNamespace

    from wikibaseintegrator.wbi_exceptions import MWApiError

    from converter.wikidata.uploader import WikidataUploader

    uploader = WikidataUploader(
        token="Fixture@Bot:not-a-credential", is_test=False, allow_live=True,
    )
    uploader._is_our_item_cache["Q123"] = True
    uploader._existing_label_snapshot["Q123"] = {
        "he": "כתב יד עברי, הספרייה הלאומית, Ms. 6988=4",
    }
    seen_labels: list[str] = []

    class FakeWbiItem:
        id = "Q123"

        def write(self, **kwargs):
            if current["item"].labels["he"] == "תכלאל":
                raise MWApiError({
                    "code": "modification-failed",
                    "info": 'Item [[Q141746840|Q141746840]] already has label "תכלאל" '
                    "associated with the same description text, using language code he.",
                })
            return self

    current: dict[str, WikidataItem] = {}

    def build(item: WikidataItem):
        current["item"] = item
        seen_labels.append(item.labels["he"])
        return FakeWbiItem(), 1, ["P217"]

    uploader._check_moratorium_for_live = lambda: None
    uploader._init_wbi = lambda: SimpleNamespace()
    uploader._assert_modifiable = lambda *args, **kwargs: None
    uploader._bot_excluded = lambda qid: False
    uploader._build_wbi_item = build
    result = uploader.upload_item(WikidataItem(
        entity_type="manuscript", local_id="990000413130205171",
        existing_qid="Q123", labels={"he": "תכלאל", "en": "Holder, Ms. 1"},
        descriptions={"en": "Hebrew manuscript, 1658"},
    ))
    assert result.status == "updated"
    assert seen_labels == [
        "תכלאל", "כתב יד עברי, הספרייה הלאומית, Ms. 6988=4",
    ]
    assert "תכלאל" in current["item"].aliases["he"]


def test_foreign_item_label_conflict_does_not_retry_a_mutation() -> None:
    from types import SimpleNamespace

    from wikibaseintegrator.wbi_exceptions import MWApiError

    from converter.wikidata.uploader import WikidataUploader

    uploader = WikidataUploader(
        token="Fixture@Bot:not-a-credential", is_test=False, allow_live=True,
    )
    attempts = []

    class FakeWbiItem:
        id = "Q123"

        def write(self, **kwargs):
            attempts.append(1)
            raise MWApiError({
                "code": "modification-failed",
                "info": 'Item [[Q124|Q124]] already has label "Title" associated with '
                "the same description text, using language code he.",
            })

    uploader._check_moratorium_for_live = lambda: None
    uploader._init_wbi = lambda: SimpleNamespace()
    uploader._assert_modifiable = lambda *args, **kwargs: None
    uploader._bot_excluded = lambda qid: False
    uploader._is_our_item = lambda qid, **kwargs: True
    uploader._is_our_item_cache["Q123"] = False
    uploader._existing_label_snapshot["Q123"] = {"he": "Old label"}
    uploader._build_wbi_item = lambda item: (FakeWbiItem(), 1, ["P1476"])
    result = uploader.upload_item(WikidataItem(
        entity_type="manuscript", existing_qid="Q123", local_id="ms-1",
        labels={"he": "Title"}, descriptions={"en": "Description"},
        statements=[WikidataStatement("P31", "Q87167", "item")],
    ))
    assert result.status == "failed"
    assert len(attempts) == 1


def test_same_label_description_value_is_removed_from_owned_update(monkeypatch) -> None:
    from types import SimpleNamespace

    from wikibaseintegrator import WikibaseIntegrator

    from converter.wikidata.uploader import WikidataUploader

    uploader = WikidataUploader(
        token="Fixture@Bot:not-a-credential", is_test=False, allow_live=True,
    )
    entity = WikibaseIntegrator().item.new().from_json({
        "id": "Q123", "type": "item", "lastrevid": 1,
        "labels": {"he": {"language": "he", "value": "A title"}},
        "descriptions": {"he": {"language": "he", "value": "A title"}},
        "aliases": {}, "claims": {}, "sitelinks": {},
    })
    monkeypatch.setattr(
        uploader, "_init_wbi",
        lambda: SimpleNamespace(item=SimpleNamespace(new=lambda: WikibaseIntegrator().item.new())),
    )
    uploader._prefetched_entities["Q123"] = {**entity.get_json(), "lastrevid": 1}
    uploader._is_our_item_cache["Q123"] = True
    updated, _, _ = uploader._build_wbi_item(WikidataItem(
        entity_type="manuscript", existing_qid="Q123", labels={"he": "A title"},
        descriptions={"he": "A title"},
        statements=[WikidataStatement("P31", "Q87167", "item")],
    ), read_only=True)
    assert updated.descriptions.get("he").removed is True
    assert uploader._existing_label_snapshot["Q123"]["he"] == "A title"


def test_create_label_description_conflict_reports_unreproduced_pair_once() -> None:
    from types import SimpleNamespace

    from wikibaseintegrator.wbi_exceptions import MWApiError

    from converter.wikidata.uploader import WikidataUploader

    uploader = WikidataUploader(
        token="Fixture@Bot:not-a-credential", is_test=False, allow_live=True,
    )
    attempts = []

    class FakeWbiItem:
        id = None

        def write(self, **kwargs):
            attempts.append(1)
            raise MWApiError({
                "code": "modification-failed",
                "info": "Label and description for language code he can not have the same value.",
            })

    uploader._check_moratorium_for_live = lambda: None
    uploader._init_wbi = lambda: SimpleNamespace()
    uploader._build_wbi_item = lambda item: (FakeWbiItem(), 1, ["P31"])
    result = uploader.upload_item(WikidataItem(
        entity_type="work", local_id="work:מדרש_איכה", labels={"he": "מדרש איכה"},
        descriptions={"en": "Work preserved in a Hebrew manuscript"},
        statements=[WikidataStatement("P31", "Q47461344", "item")],
    ))
    assert result.status == "failed"
    assert "local item has no equal pair" in result.message
    assert len(attempts) == 1


def test_write_boundary_refuses_a_source_scoped_work_from_another_record(monkeypatch) -> None:
    from types import SimpleNamespace

    from wikibaseintegrator import WikibaseIntegrator

    from converter.wikidata.uploader import WikidataUploader

    first = "990000885680205171"
    second = "990000802880205171"
    existing = WikibaseIntegrator().item.new().from_json({
        "id": "Q123", "type": "item", "lastrevid": 1,
        "labels": {}, "descriptions": {}, "aliases": {}, "sitelinks": {},
        "claims": {"P1476": [{
            "id": "Q123$00000000-0000-0000-0000-000000000001", "type": "statement",
            "rank": "normal", "mainsnak": {
                "snaktype": "value", "property": "P1476", "datatype": "monolingualtext",
                "datavalue": {"value": {"text": "דרשה", "language": "he"}, "type": "monolingualtext"},
            }, "references": [{
                "hash": "a" * 40, "snaks-order": ["P3959"], "snaks": {"P3959": [{
                    "snaktype": "value", "property": "P3959", "datatype": "external-id",
                    "datavalue": {"value": first, "type": "string"},
                }]},
            }],
        }]},
    })
    uploader = WikidataUploader(
        token="Fixture@Bot:not-a-credential", is_test=False, allow_live=True,
    )
    monkeypatch.setattr(
        uploader, "_init_wbi",
        lambda: SimpleNamespace(item=SimpleNamespace(get=lambda *args, **kwargs: existing)),
    )
    uploader._is_our_item_cache["Q123"] = True
    proposed = WikidataItem(
        entity_type="work", existing_qid="Q123", records=[second],
        work_candidate_evidence=[{"accepted": True, "identity_scope": "record"}],
    )
    with pytest.raises(ClaimBuildError, match="WORK_IDENTITY"):
        uploader._build_wbi_item(proposed, read_only=True)
