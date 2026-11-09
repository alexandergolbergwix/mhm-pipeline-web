"""Local targets retain evidence and target-wiki identity."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import asdict

import pytest

from app.pipeline.wikidata_live_native_hygiene import (
    rewrite_local_to_live_qids,
    sanitize_studio_items_for_live,
)
from app.pipeline.wikidata_local_refs import resolve_local_references
from converter.wikidata.item_models import WikidataItem, WikidataStatement


@pytest.mark.parametrize("resolver", [
    resolve_local_references, sanitize_studio_items_for_live, rewrite_local_to_live_qids,
])
@pytest.mark.parametrize("pid", ["P127", "P1574"])
def test_missing_local_target_does_not_drop_or_degrade_sourced_claim(resolver, pid: str) -> None:
    source = [{"property": "P854", "value": "https://example.org/one", "type": "url"}]
    claim = WikidataStatement(pid, "__LOCAL:missing", "item", references=source,
                              reference_blocks=[source])
    item = WikidataItem(local_id="ms:1", entity_type="manuscript", statements=[claim])
    original = deepcopy(item)

    with pytest.raises(ValueError, match="DANGLING_LOCAL_REFERENCE"):
        resolver([item])
    assert item == original


@pytest.mark.parametrize("resolver", [
    resolve_local_references, sanitize_studio_items_for_live, rewrite_local_to_live_qids,
])
@pytest.mark.parametrize("pid", ["P127", "P1574"])
def test_missing_local_target_does_not_drop_or_degrade_qualified_claim(resolver, pid: str) -> None:
    qualifier = {"property": "P1932", "value": "Name", "type": "string"}
    claim = WikidataStatement(pid, "__LOCAL:missing", "item", qualifiers=[qualifier])
    item = WikidataItem(local_id="ms:1", entity_type="manuscript", statements=[claim])
    original = deepcopy(item)

    with pytest.raises(ValueError, match="DANGLING_LOCAL_REFERENCE"):
        resolver([item])
    assert item == original


@pytest.mark.parametrize("resolver", [
    resolve_local_references, sanitize_studio_items_for_live, rewrite_local_to_live_qids,
])
@pytest.mark.parametrize("sourced", [False, True])
def test_unresolved_non_author_qualifier_blocks_without_losing_evidence(
    resolver, sourced: bool,
) -> None:
    source = [{"property": "P854", "value": "https://example.org/one", "type": "url"}]
    qualifier = {"property": "P3831", "value": "__LOCAL:missing", "type": "item"}
    claim = WikidataStatement("P127", "Q42", "item", qualifiers=[qualifier],
                              references=source if sourced else [],
                              reference_blocks=[source] if sourced else [])
    item = WikidataItem(local_id="ms:1", entity_type="manuscript", statements=[claim])
    original = deepcopy(item)

    with pytest.raises(ValueError, match="DANGLING_LOCAL_REFERENCE"):
        resolver([item])
    assert item == original


@pytest.mark.parametrize("resolver", [resolve_local_references, sanitize_studio_items_for_live])
def test_unresolved_non_author_reference_blocks(resolver) -> None:
    source = [{"property": "P248", "value": "__LOCAL:missing", "type": "item"}]
    claim = WikidataStatement("P127", "Q42", "item", references=source, reference_blocks=[source])
    item = WikidataItem(local_id="ms:1", entity_type="manuscript", statements=[claim])
    original = deepcopy(item)

    with pytest.raises(ValueError, match="DANGLING_LOCAL_REFERENCE"):
        resolver([item])
    assert item == original


@pytest.mark.parametrize("resolver", [
    resolve_local_references, sanitize_studio_items_for_live, rewrite_local_to_live_qids,
])
@pytest.mark.parametrize("location", ["qualifier", "reference"])
def test_known_local_inside_nested_snak_blocks_until_writer_supports_it(
    resolver, location: str,
) -> None:
    snak = {
        "property": "P248", "datatype": "wikibase-item", "snaktype": "value",
        "datavalue": {"type": "wikibase-entityid", "value": {"id": "__LOCAL:source"}},
    }
    source = [snak] if location == "reference" else []
    claim = WikidataStatement("P127", "Q42", "item", qualifiers=[snak] if not source else [],
                              references=source, reference_blocks=[source] if source else [])
    item = WikidataItem(local_id="ms:1", entity_type="manuscript", statements=[claim])
    target = WikidataItem(local_id="source", entity_type="person", existing_qid="Q900")
    original = deepcopy(item)
    with pytest.raises(ValueError, match="UNSUPPORTED_LOCAL_REFERENCE"):
        resolver([item, target])
    assert item == original


def test_known_local_claim_and_qualifier_keep_ids_before_target_wiki_resolution() -> None:
    person = WikidataItem(local_id="author", entity_type="person", existing_qid="Q900")
    qualifier = {"property": "P3831", "value": "__LOCAL:author", "type": "item"}
    source = [{"property": "P248", "value": "__LOCAL:author", "type": "item"}]
    claim = WikidataStatement("P50", "__LOCAL:author", "item", qualifiers=[qualifier],
                              references=source, reference_blocks=[source])
    work = WikidataItem(local_id="work", entity_type="work", statements=[claim])

    resolve_local_references([work, person])
    sanitize_studio_items_for_live([work, person])
    raw = json.loads(json.dumps(asdict(work)))
    assert raw["statements"][0]["value"] == "__LOCAL:author"
    assert raw["statements"][0]["qualifiers"][0]["value"] == "__LOCAL:author"
    assert raw["statements"][0]["reference_blocks"][0][0]["value"] == "__LOCAL:author"


def test_quickstatements_reports_a_sourced_local_main_value() -> None:
    from converter.wikidata.quickstatements import QuickStatementsExporter

    source = [{"property": "P854", "value": "https://example.org/source", "type": "url"}]
    claim = WikidataStatement("P50", "__LOCAL:author", "item", references=source,
                              reference_blocks=[source])
    work = WikidataItem(local_id="work", entity_type="work", statements=[claim])
    output = QuickStatementsExporter().export_item(work)
    assert "Statement omitted" in output


def test_quickstatements_does_not_emit_an_unresolved_local_qualifier() -> None:
    from converter.wikidata.quickstatements import QuickStatementsExporter

    qualifier = {"property": "P3831", "value": "__LOCAL:author", "type": "item"}
    claim = WikidataStatement("P127", "Q42", "item", qualifiers=[qualifier])
    item = WikidataItem(local_id="ms:1", entity_type="manuscript", statements=[claim])
    output = QuickStatementsExporter().export_item(item)
    assert "__LOCAL:author" not in output
    assert "Statement omitted" in output


def test_known_local_qualifier_on_a_nonlocal_claim_keeps_its_id() -> None:
    person = WikidataItem(local_id="author", entity_type="person", existing_qid="Q900")
    qualifier = {"property": "P3831", "value": "__LOCAL:author", "type": "item"}
    claim = WikidataStatement("P127", "Q42", "item", qualifiers=[qualifier])
    item = WikidataItem(local_id="ms:1", entity_type="manuscript", statements=[claim])
    sanitize_studio_items_for_live([item, person])
    assert claim.qualifiers == [qualifier]


def test_live_resolution_uses_live_qid_only_after_hygiene() -> None:
    from app.pipeline.wikidata_upload_job import _resolve_existing_qid_map
    from converter.wikidata.uploader import resolve_statement_locals

    person = WikidataItem(local_id="author", entity_type="person", existing_qid="Q900")
    claim = WikidataStatement("P50", "__LOCAL:author", "item")
    work = WikidataItem(local_id="work", entity_type="work", statements=[claim])
    sanitize_studio_items_for_live([work, person])
    assert claim.value == "__LOCAL:author"
    targets = _resolve_existing_qid_map([work, person], {}, is_test=False)
    resolved, remaining = resolve_statement_locals(claim, targets)
    assert not remaining
    assert resolved.value == "Q900"
    assert claim.value == "__LOCAL:author"


def test_test_ledger_collision_does_not_confuse_live_and_local_qids(monkeypatch) -> None:
    from app.pipeline.wikidata_studio import _serialise_item
    from app.pipeline.wikidata_upload_job import _resolve_existing_qid_map
    from app.routers.wikidata_studio import studio_dict_to_native_item
    from converter.wikidata.test_wiki_compat import WikiTestAdaptStats, pid_map_key
    from converter.wikidata.uploader import WikidataUploader, resolve_statement_locals

    person = WikidataItem(local_id="author", entity_type="person", existing_qid="Q900")
    qualifier = {"property": "P3831", "value": "__LOCAL:author", "type": "item"}
    source = [
        {"property": "P248", "value": "__LOCAL:author", "type": "item"},
        {"property": "P50", "value": "http://www.wikidata.org/entity/Q900", "type": "item"},
    ]
    claim = WikidataStatement("P50", "__LOCAL:author", "item", qualifiers=[qualifier],
                              references=source, reference_blocks=[source])
    work = WikidataItem(local_id="work", entity_type="work", statements=[claim])
    sanitize_studio_items_for_live([work, person])

    ledger = {"wikidata-test:person:author": "Q900"}
    targets = _resolve_existing_qid_map([work, person], ledger, is_test=True)
    assert targets == {"author": "Q900"}
    cached = json.loads(json.dumps(_serialise_item(work)))
    restored = studio_dict_to_native_item(cached).statements[0]
    resolved, remaining = resolve_statement_locals(restored, targets, test_namespace=True)
    assert not remaining
    uploader = WikidataUploader(token="Fixture@Bot:not-a-credential", is_test=True)
    uploader.register_test_ledger_target(person, ledger)
    uploader._test_qid_map = {"Q900": "Q999"}
    uploader._test_pid_map = {pid_map_key(pid, "item"): pid for pid in ("P50", "P248", "P3831")}
    uploader._test_property_datatypes = {pid: "wikibase-item" for pid in ("P50", "P248", "P3831")}
    uploader._test_entity_exists = {"Q900": True, "Q999": True}
    monkeypatch.setattr(uploader, "_ensure_test_maps_for_item", lambda _: WikiTestAdaptStats())
    monkeypatch.setattr(uploader, "_ensure_test_entity_exists_flags", lambda _: None)
    mapped, adaptation = uploader._adapt_item_for_test_wiki(
        WikidataItem(local_id="work", entity_type="work", statements=[resolved]),
    )
    assert not adaptation.skipped
    result = uploader._build_claim(mapped.statements[0]).get_json()
    assert result["mainsnak"]["datavalue"]["value"]["id"] == "Q900"
    assert result["qualifiers"]["P3831"][0]["datavalue"]["value"]["id"] == "Q900"
    snaks = result["references"][0]["snaks"]
    assert snaks["P248"][0]["datavalue"]["value"]["id"] == "Q900"
    assert snaks["P50"][0]["datavalue"]["value"]["id"] == "Q999"


@pytest.mark.parametrize("ledger", [
    {},
    {"wikidata:person:author": "Q900"},
    {"wikidata-test:person:author": "Q900", "wikidata-test:person:other": "Q900"},
])
def test_missing_or_ambiguous_test_ledger_keeps_the_statement_deferred(ledger) -> None:
    from app.pipeline.wikidata_upload_job import _resolve_existing_qid_map
    from converter.wikidata.uploader import partition_unresolved_local

    person = WikidataItem(local_id="author", entity_type="person", existing_qid="Q900")
    claim = WikidataStatement("P50", "__LOCAL:author", "item")
    work = WikidataItem(local_id="work", entity_type="work", statements=[claim])
    sanitize_studio_items_for_live([work, person])
    targets = _resolve_existing_qid_map([work, person], ledger, is_test=True)
    write, deferred = partition_unresolved_local(work, targets)
    assert not write.statements
    assert deferred[0].value == "__LOCAL:author"
