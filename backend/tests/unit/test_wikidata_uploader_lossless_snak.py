"""Lossless native qualifier, quantity, and reference compilation."""

from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest
from wikibaseintegrator import WikibaseIntegrator

from converter.wikidata.item_models import WikidataItem, WikidataStatement
from converter.wikidata.property_mapping import nli_reference
from converter.wikidata.test_wiki_compat import WikiTestAdaptStats, pid_map_key
from converter.wikidata.uploader import ClaimBuildError, WikidataUploader, partition_unresolved_local


def _uploader(mode: str) -> WikidataUploader:
    return WikidataUploader(token="Fixture@Bot:not-a-credential", is_test=mode == "test", allow_live=mode != "test")


@pytest.mark.parametrize("mode", ["live", "owner", "test"])
def test_wbi_preserves_qualifier_datatypes_language_precision_and_reference_blocks(mode: str) -> None:
    refs = [
        [{"property": "P854", "value": "https://example.org/one", "type": "url"}],
        [{"property": "P854", "value": "https://example.org/two", "type": "url"}],
    ]
    stmt = WikidataStatement(
        "P217", "MS 1", "string",
        qualifiers=[
            {"property": "P1476", "value": "Title", "type": "monolingualtext", "language": "en"},
            {"property": "P580", "value": "+1500-00-00T00:00:00Z", "type": "time", "precision": 9},
            {"property": "P244", "value": "abc", "type": "external-id"},
            {"property": "P1932", "value": "Name", "type": "string"},
        ],
        references=[copy.deepcopy(snak) for block in refs for snak in block], reference_blocks=refs,
    )
    claim = _uploader(mode)._build_claim(stmt, owner_only=mode == "owner")
    data = claim.get_json()
    qualifiers = data["qualifiers"]
    assert qualifiers["P1476"][0]["datatype"] == "monolingualtext"
    assert qualifiers["P1476"][0]["datavalue"]["value"] == {"text": "Title", "language": "en"}
    assert qualifiers["P580"][0]["datatype"] == "time"
    assert qualifiers["P580"][0]["datavalue"]["value"]["precision"] == 9
    assert qualifiers["P244"][0]["datatype"] == "external-id"
    assert qualifiers["P1932"][0]["datatype"] == "string"
    assert len(data["references"]) == 2


@pytest.mark.parametrize("mode", ["live", "owner"])
def test_wbi_preserves_explicit_qualifier_calendar_and_uncertainty(mode: str) -> None:
    qualifier = {"property": "P580", "value": "+1500-02-29T00:00:00Z", "type": "time", "precision": 11,
                 "calendarmodel": "http://www.wikidata.org/entity/Q1985786", "before": 2, "after": 3, "timezone": 60}
    uploader = _uploader(mode)
    if mode == "owner":
        uploader._owner_only_session = object()
    claim = uploader._build_claim(WikidataStatement("P217", "MS 1", "string", qualifiers=[qualifier]),
                                  owner_only=mode == "owner")
    value = claim.get_json()["qualifiers"]["P580"][0]["datavalue"]["value"]
    assert value["time"] == qualifier["value"]
    assert value["calendarmodel"] == qualifier["calendarmodel"]
    assert (value["precision"], value["before"], value["after"], value["timezone"]) == (11, 2, 3, 60)


def test_test_wiki_rejects_qualifier_calendar_without_remap() -> None:
    qualifier = {"property": "P580", "value": "+1500-02-29T00:00:00Z", "type": "time", "precision": 11,
                 "calendarmodel": "http://www.wikidata.org/entity/Q1985786"}
    with pytest.raises(ClaimBuildError, match="qualifier calendar"):
        _uploader("test")._build_claim(WikidataStatement("P217", "MS 1", "string", qualifiers=[qualifier]))


def test_test_wiki_wbi_preserves_verified_item_qualifier_after_remap(monkeypatch: pytest.MonkeyPatch) -> None:
    uploader = _uploader("test")
    uploader._test_qid_map = {"Q900": "Q901"}
    uploader._test_pid_map = {pid_map_key("P217", "string"): "P217", pid_map_key("P1480", "item"): "P1480"}
    uploader._test_property_datatypes = {"P217": "string", "P1480": "wikibase-item"}
    uploader._test_entity_exists = {"Q901": True}
    monkeypatch.setattr(uploader, "_ensure_test_maps_for_item", lambda _: WikiTestAdaptStats())
    item = WikidataItem(statements=[WikidataStatement("P217", "MS 1", "string", qualifiers=[
        {"property": "P1480", "value": "Q900", "type": "item", "value_namespace": "wikidata"},
    ])])
    mapped, result = uploader._adapt_item_for_test_wiki(item)
    assert not result.skipped
    claim = uploader._build_claim(mapped.statements[0])
    qualifier = claim.get_json()["qualifiers"]["P1480"][0]
    assert qualifier["datatype"] == "wikibase-item"
    assert qualifier["datavalue"]["value"]["id"] == "Q901"


@pytest.mark.parametrize("mode", ["live", "owner", "test"])
@pytest.mark.parametrize("qualifier", [
    {"property": "P1480", "value": "not-a-qid", "type": "item"},
    {"property": "P1480", "property_id": "", "value": "Q1", "type": "item"},
    {"property": "P1480", "value": "Q1", "type": "wikibase-property"},
    {"property": "P1476", "value": "Title", "type": "monolingualtext"},
    {"property": "P1476", "value": "Title", "type": "monolingualtext", "language": ""},
    {"property": "P1476", "value": "Title", "type": "monolingualtext", "language": "en", "extra": "lost"},
    {"property": "P580", "value": "+1500-00-00T00:00:00Z", "type": "time", "precision": 0},
    {"property": "P580", "value": "+1500-00-00T00:00:00Z", "type": "time", "precision": 12},
    {"property": "P1932", "value": "Name", "type": "string", "language": "en"},
    {"property": "P1104", "value": "12", "type": "quantity", "unit": "furlong"},
    {"property": "P1932", "value": "Name", "type": "string", "snaktype": "novalue"},
    {"property": "P1932", "value": "Name", "type": "string", "value_type": "item"},
    {"property": "P1480", "value": {"id": "Q1", "numeric-id": 2}, "type": "item"},
])
def test_invalid_qualifier_fails_direct_compile_and_native_entry(
    monkeypatch: pytest.MonkeyPatch, mode: str, qualifier: dict[str, object],
) -> None:
    uploader = _uploader(mode)
    stmt = WikidataStatement("P217", "MS 1", "string", qualifiers=[qualifier])
    with pytest.raises(ClaimBuildError, match="qualifier"):
        uploader._build_claim(stmt, owner_only=mode == "owner")
    monkeypatch.setattr(uploader, "_check_moratorium_for_live", lambda: None)
    initialized: list[bool] = []
    monkeypatch.setattr(uploader, "_init_wbi", lambda: initialized.append(True))
    item = WikidataItem(local_id="QDraft_MS_10001", entity_type="manuscript", existing_qid="Q123", records=["10001"],
                        statements=[WikidataStatement("P3959", "10001", "external-id"), stmt])
    outcome = uploader.upload_item(item, owner_only_qid="Q123" if mode == "owner" else None)
    assert outcome.status == "blocked"
    assert "qualifier" in outcome.message
    assert initialized == []


@pytest.mark.parametrize("mode", ["live", "owner", "test"])
@pytest.mark.parametrize("unit", ["furlong", "Q0", "http://evil.example/entity/Q174789", " mm", False, 0])
def test_unsupported_quantity_unit_fails_compile_and_native_entry(
    monkeypatch: pytest.MonkeyPatch, mode: str, unit: object,
) -> None:
    uploader = _uploader(mode)
    stmt = WikidataStatement("P1104", "12", "quantity", unit=unit)
    with pytest.raises(ClaimBuildError, match="[Uu][Nn][Ii][Tt]"):
        uploader._build_claim(stmt, owner_only=mode == "owner")
    monkeypatch.setattr(uploader, "_check_moratorium_for_live", lambda: None)
    initialized: list[bool] = []
    monkeypatch.setattr(uploader, "_init_wbi", lambda: initialized.append(True))
    item = WikidataItem(local_id="QDraft_MS_10001", entity_type="manuscript", existing_qid="Q123", records=["10001"],
                        statements=[WikidataStatement("P3959", "10001", "external-id"), stmt])
    outcome = uploader.upload_item(item, owner_only_qid="Q123" if mode == "owner" else None)
    assert outcome.status == "blocked"
    assert "unit" in outcome.message.lower()
    assert initialized == []


@pytest.mark.parametrize("mode", ["live", "owner", "test"])
def test_wbi_preserves_supported_quantity_unit(mode: str) -> None:
    uploader = _uploader(mode)
    expected = "http://www.wikidata.org/entity/Q174789"
    if mode == "test":
        uploader._test_qid_map = {"Q174789": "Q900", "Q900": "Q901"}
        uploader._test_entity_exists = {"Q900": True, "Q901": True}
        expected = "http://test.wikidata.org/entity/Q900"
    claim = uploader._build_claim(WikidataStatement("P2048", "12", "quantity", unit="mm"), owner_only=mode == "owner")
    assert claim.get_json()["mainsnak"]["datavalue"]["value"]["unit"] == expected


def test_direct_test_claim_rejects_unmapped_known_unit() -> None:
    with pytest.raises(ClaimBuildError, match="quantity unit"):
        _uploader("test")._build_claim(WikidataStatement("P2048", "12", "quantity", unit="mm"))


@pytest.mark.parametrize("mode", ["live", "owner", "test"])
def test_ambiguous_flat_reference_fails_before_write(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    uploader = _uploader(mode)
    refs = [{"property": "P854", "value": "https://example.org/one", "type": "url"},
            {"property": "P854", "value": "https://example.org/two", "type": "url"}]
    stmt = WikidataStatement("P217", "MS 1", "string", references=refs)
    with pytest.raises(ClaimBuildError, match="Ambiguous flat references"):
        uploader._build_claim(stmt, owner_only=mode == "owner")
    monkeypatch.setattr(uploader, "_check_moratorium_for_live", lambda: None)
    initialized: list[bool] = []
    monkeypatch.setattr(uploader, "_init_wbi", lambda: initialized.append(True))
    item = WikidataItem(local_id="QDraft_MS_10001", entity_type="manuscript", existing_qid="Q123", records=["10001"],
                        statements=[WikidataStatement("P3959", "10001", "external-id"), stmt])
    outcome = uploader.upload_item(item, owner_only_qid="Q123" if mode == "owner" else None)
    assert outcome.status == "blocked"
    assert "Ambiguous flat references" in outcome.message
    assert initialized == []


@pytest.mark.parametrize("field", ["qualifiers", "references"])
def test_deferred_partition_does_not_erase_invalid_native_shape(field: str) -> None:
    stmt = WikidataStatement("P217", "MS 1", "string")
    setattr(stmt, field, None)
    item, deferred = partition_unresolved_local(WikidataItem(statements=[stmt]), {})
    assert not deferred
    assert getattr(item.statements[0], field) is None
    with pytest.raises(ClaimBuildError):
        _uploader("live")._build_claim(item.statements[0])


def test_fresh_nli_source_remains_one_explicit_reference() -> None:
    source = nli_reference("990000000000000001")
    stmt = WikidataStatement("P217", "MS 1", "string", references=source)
    assert len(stmt.reference_blocks) == 1
    claim = _uploader("live")._build_claim(stmt)
    reference = claim.get_json()["references"][0]["snaks"]
    assert set(reference) == {"P248", "P3959", "P854", "P813"}


def test_owned_update_keeps_qualifier_and_reference_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    uploader = _uploader("live")
    original = WikidataStatement("P217", "MS 1", "string", qualifiers=[
        {"property": "P1476", "value": "Title", "type": "monolingualtext", "language": "en"},
    ], references=[{"property": "P854", "value": "https://example.org/old", "type": "url"}])
    claim = uploader._build_claim(original)
    data = copy.deepcopy(claim.get_json())
    data["id"] = "Q123$00000000-0000-0000-0000-000000000001"
    data["references"][0]["hash"] = "a" * 40
    existing = WikibaseIntegrator().item.new().from_json({
        "id": "Q123", "type": "item", "lastrevid": 1,
        "labels": {}, "descriptions": {}, "aliases": {}, "claims": {"P217": [data]}, "sitelinks": {},
    })
    monkeypatch.setattr(uploader, "_init_wbi", lambda: SimpleNamespace(item=SimpleNamespace(get=lambda *a, **k: existing)))
    uploader._is_our_item_cache["Q123"] = True
    updated = copy.deepcopy(original)
    updated.references = [{"property": "P854", "value": "https://example.org/new", "type": "url"}]
    result, _, _ = uploader._build_wbi_item(WikidataItem(existing_qid="Q123", statements=[updated]), read_only=True)
    written = result.claims.get("P217")[0].get_json()
    assert written["qualifiers"]["P1476"][0]["datavalue"]["value"] == {"text": "Title", "language": "en"}
    assert len(written["references"]) == 2
