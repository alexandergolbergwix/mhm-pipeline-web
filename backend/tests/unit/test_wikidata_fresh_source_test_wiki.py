"""Fresh source namespaces survive Studio and test-wiki adaptation."""

import json
from copy import deepcopy

import pytest

from app.pipeline.wikidata_studio import build_items_for_run, validate_upload_reference_blocks
from app.routers.wikidata_studio import studio_dict_to_native_item
from converter.wikidata.item_models import WikidataItem, WikidataStatement
from converter.wikidata.property_mapping import (
    Q_KTIV,
    extend_source_reference,
    nli_authority_reference,
    nli_reference,
    viaf_reference,
)
from converter.wikidata.quickstatements import QuickStatementsExporter, validate_source_references
from converter.wikidata.test_wiki_compat import WikiTestAdaptStats, pid_map_key
from converter.wikidata.uploader import WikidataUploader
from tests.unit.test_wikidata_studio_works import _fake_marc_record


def _test_uploader(monkeypatch, source_qids):
    uploader = WikidataUploader(token="Fixture@Bot:not-a-credential", is_test=True)
    mappings = {
        ("P3959", "external-id"): "P1", ("P214", "external-id"): "P2",
        ("P248", "item"): "P3", ("P854", "url"): "P4", ("P813", "time"): "P5",
        ("P8189", "external-id"): "P6", ("P887", "item"): "P7",
    }
    uploader._test_pid_map = {pid_map_key(pid, kind): target
                              for (pid, kind), target in mappings.items()}
    uploader._test_qid_map = source_qids
    uploader._test_property_datatypes = {
        target: "wikibase-item" if kind == "item" else kind
        for (_pid, kind), target in mappings.items()
    }
    uploader._test_entity_exists = {qid: True for qid in source_qids.values()}
    monkeypatch.setattr(uploader, "_ensure_test_maps_for_item", lambda _, **_kwargs: WikiTestAdaptStats())
    monkeypatch.setattr(uploader, "_ensure_test_property_datatypes", lambda _: None)
    monkeypatch.setattr(uploader, "_ensure_test_entity_exists_flags", lambda _: None)
    return uploader


@pytest.mark.asyncio
async def test_fresh_nli_through_builder_studio_and_test_wiki(monkeypatch):
    result = await build_items_for_run(
        marc_records=[_fake_marc_record("990000000000000001")], approved_matches=[],
    )
    studio = next(item for item in result["items"] if item["entity_type"] == "manuscript")
    restored = studio_dict_to_native_item(json.loads(json.dumps(studio)))
    statement = next(s for s in restored.statements if s.property_id == "P3959")
    source = statement.reference_blocks[0][0]
    assert source["value"] == Q_KTIV and source["value_namespace"] == "wikidata"
    assert statement.references[0]["value_namespace"] == "wikidata"
    item = WikidataItem(statements=[statement])
    validate_upload_reference_blocks([item])
    assert f"S248\t{Q_KTIV}" in QuickStatementsExporter().export_item(item)

    live = WikidataUploader(token="Fixture@Bot:not-a-credential")
    live_ref = live._build_claim(statement).get_json()["references"][0]["snaks"]
    assert live_ref["P248"][0]["datavalue"]["value"]["id"] == Q_KTIV

    uploader = _test_uploader(monkeypatch, {Q_KTIV: "Q900"})
    adapted, outcome = uploader._adapt_item_for_test_wiki(item)
    assert outcome.skipped == []
    test_ref = uploader._build_claim(adapted.statements[0]).get_json()["references"][0]["snaks"]
    assert test_ref["P3"][0]["datavalue"]["value"]["id"] == "Q900"
    assert set(test_ref) == {"P3", "P1", "P4", "P5"}
    assert statement.reference_blocks[0][0]["value"] == Q_KTIV


@pytest.mark.parametrize("source,pid,live_qid", [
    (nli_authority_reference("987000000000000001"), "P8189", Q_KTIV),
    (viaf_reference("12345"), "P214", "Q54919"),
])
def test_fresh_authority_source_round_trip_and_remap(monkeypatch, source, pid, live_qid):
    statement = WikidataStatement(pid, "12345", "external-id", references=source)
    raw = json.loads(json.dumps({"statements": [{
        "property_id": pid, "value": "12345", "value_type": "external-id",
        "references": statement.references, "reference_blocks": statement.reference_blocks,
    }]}))
    item = studio_dict_to_native_item(raw)
    validate_upload_reference_blocks([item])
    assert item.statements[0].reference_blocks[0][0]["value_namespace"] == "wikidata"
    uploader = _test_uploader(monkeypatch, {live_qid: "Q900"})
    adapted, outcome = uploader._adapt_item_for_test_wiki(item)
    assert outcome.skipped == []
    claim = uploader._build_claim(adapted.statements[0]).get_json()
    assert claim["references"][0]["snaks"]["P3"][0]["datavalue"]["value"]["id"] == "Q900"


def test_fresh_heuristic_reference_has_live_origin_and_remaps(monkeypatch):
    source = extend_source_reference(nli_reference("990000000000000001"), [
        {"property": "P887", "value": "Q42", "type": "item"},
    ])
    assert source[-1]["value_namespace"] == "wikidata"
    statement = WikidataStatement("P3959", "990000000000000001", "external-id", references=source)
    item = WikidataItem(statements=[statement])
    validate_upload_reference_blocks([item])
    assert "S887\tQ42" in QuickStatementsExporter().export_item(item)
    uploader = _test_uploader(monkeypatch, {Q_KTIV: "Q900", "Q42": "Q901"})
    adapted, outcome = uploader._adapt_item_for_test_wiki(item)
    assert not outcome.skipped
    reference = uploader._build_claim(adapted.statements[0]).get_json()["references"][0]["snaks"]
    assert reference["P3"][0]["datavalue"]["value"]["id"] == "Q900"
    assert reference["P7"][0]["datavalue"]["value"]["id"] == "Q901"


def test_old_bare_reference_is_not_promoted_or_remapped(monkeypatch):
    source = deepcopy(list(nli_reference("990000000000000001")))
    source[0].pop("value_namespace")
    statement = WikidataStatement("P3959", "990000000000000001", "external-id",
                                  references=source, reference_blocks=[source])
    restored = studio_dict_to_native_item(json.loads(json.dumps({"statements": [{
        "property_id": statement.property_id, "value": statement.value,
        "value_type": statement.value_type, "references": statement.references,
        "reference_blocks": statement.reference_blocks,
    }]})))
    uploader = _test_uploader(monkeypatch, {Q_KTIV: "Q900"})
    _, outcome = uploader._adapt_item_for_test_wiki(restored)
    assert any("ambiguous wiki namespace" in issue for issue in outcome.skipped)
    assert "value_namespace" not in restored.statements[0].references[0]
    with pytest.raises(ValueError, match="Ambiguous flat references"):
        validate_source_references(source, [], require_proven_blocks=True)


@pytest.mark.parametrize("snak", [
    {"property": "P248", "value": "Q42", "type": "item", "value_namespace": "local"},
    {"property": "P248", "value": "Q42", "type": "item", "value_namespace": "mapped-test"},
    {"property": "P248", "value": "Q42", "type": "string", "value_namespace": "wikidata"},
    {"property": "P248", "value": "__LOCAL:x", "type": "item", "value_namespace": "wikidata"},
])
def test_live_reference_namespace_validation_fails_closed(snak):
    with pytest.raises(ValueError, match="namespace"):
        validate_source_references([snak], [[snak]], allow_local=True)
