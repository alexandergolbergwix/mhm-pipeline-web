"""Reference provenance and statement ownership regressions."""

from __future__ import annotations

import json
from copy import copy, deepcopy
from dataclasses import asdict, replace

import pytest

from converter.wikidata.item_models import WikidataItem, WikidataStatement
from converter.wikidata.property_mapping import (
    FreshSourceReference,
    extend_source_reference,
    nli_reference,
)


def _blocks() -> list[list[dict[str, object]]]:
    return [
        [{"property": "P248", "value": "Q42", "type": "item"},
         {"property": "P854", "value": "https://example.org/one", "type": "url"}],
        [{"property_id": "P248", "value": "Q42", "value_type": "wikibase-item",
          "value_label": "source"},
         {"property": "P854", "value": "https://example.org/two", "type": "url"}],
    ]


def test_fresh_marker_rejects_direct_wrapping_of_old_flat_data() -> None:
    old_flat = [snak for block in _blocks() for snak in block]
    with pytest.raises((TypeError, ValueError), match="source constructor"):
        FreshSourceReference(old_flat)


def test_fresh_marker_cannot_be_forged_with_a_subclass() -> None:
    with pytest.raises(TypeError, match="cannot be subclassed"):
        class ForgedReference(FreshSourceReference):
            pass


def test_fresh_marker_cannot_extend_with_an_old_flat_source() -> None:
    old_flat = [snak for block in _blocks() for snak in block]
    with pytest.raises(ValueError, match="heuristic"):
        extend_source_reference(nli_reference("990001"), old_flat)


def test_source_constructor_copy_and_heuristic_extension_remain_fresh() -> None:
    source = nli_reference("990001")
    annotation = {"property": "P887", "value": "Q42", "type": "item"}
    refs = (source, source.copy(), deepcopy(source), extend_source_reference(source, [annotation]))
    for ref in refs:
        statement = WikidataStatement("P31", "Q5", "item", references=ref)
        assert statement.reference_blocks == [list(ref)]


def test_fresh_source_cannot_replace_legacy_reference_evidence() -> None:
    legacy = [snak for block in _blocks() for snak in block]
    statement = WikidataStatement("P31", "Q5", "item", references=legacy)
    with pytest.raises(ValueError, match="Cannot replace existing reference evidence"):
        statement.set_fresh_source(nli_reference("990001"))
    assert statement.references == legacy
    assert statement.reference_blocks == []


def test_statements_do_not_share_source_or_qualifier_containers() -> None:
    blocks = _blocks()
    flat = [snak for block in blocks for snak in block]
    qualifiers = [{"property": "P958", "value": "1r", "type": "string"}]
    first = WikidataStatement("P50", "Q42", "item", qualifiers=qualifiers,
                              references=flat, reference_blocks=blocks)
    second = WikidataStatement("P50", "Q42", "item", qualifiers=qualifiers,
                               references=flat, reference_blocks=blocks)
    first.reference_blocks[0][0]["value"] = "Q99"
    first.references[0]["value"] = "Q88"
    first.qualifiers[0]["value"] = "2r"
    assert blocks[0][0]["value"] == "Q42"
    assert flat[0]["value"] == "Q42"
    assert qualifiers[0]["value"] == "1r"
    assert second.reference_blocks[0][0]["value"] == "Q42"
    assert second.references[0]["value"] == "Q42"
    assert second.qualifiers[0]["value"] == "1r"


@pytest.mark.parametrize("clone", [copy, deepcopy, replace])
def test_statement_copies_do_not_share_nested_references(clone) -> None:
    blocks = _blocks()
    source = WikidataStatement("P50", "Q42", "item",
                               references=[snak for block in blocks for snak in block],
                               reference_blocks=blocks)
    other = clone(source)
    other.reference_blocks[0][0]["value"] = "Q99"
    other.references[0]["value"] = "Q88"
    assert source.reference_blocks[0][0]["value"] == "Q42"
    assert source.references[0]["value"] == "Q42"


def test_explicit_blocks_deduplicate_display_snaks_without_changing_blocks() -> None:
    blocks = _blocks()
    flat = [snak for block in blocks for snak in block]
    statement = WikidataStatement("P31", "Q5", "item", references=flat, reference_blocks=blocks)
    assert len(statement.references) == 3
    assert statement.references == [blocks[0][0], blocks[0][1], blocks[1][1]]
    assert statement.reference_blocks == blocks


def test_legacy_flat_references_remain_flat_and_keep_duplicates() -> None:
    legacy = [snak for block in _blocks() for snak in block]
    statement = WikidataStatement("P31", "Q5", "item", references=legacy)
    assert statement.reference_blocks == []
    assert statement.references == legacy
    assert statement.references is not legacy


def test_json_and_shard_round_trips_restore_deduplicated_display() -> None:
    from app.pipeline.wikidata_studio import native_item_from_payload, native_item_payload

    blocks = _blocks()
    statement = WikidataStatement("P31", "Q5", "item",
                                  references=[snak for block in blocks for snak in block],
                                  reference_blocks=blocks)
    direct = WikidataStatement(**json.loads(json.dumps(asdict(statement))))
    assert direct.reference_blocks == blocks
    assert direct.references == statement.references
    item = WikidataItem(local_id="ms:1", entity_type="manuscript", statements=[statement])
    payload = json.loads(json.dumps(native_item_payload(item)))
    restored = native_item_from_payload(payload).statements[0]
    assert restored.reference_blocks == blocks
    assert restored.references == statement.references
    assert payload["statements"][0]["references"] == statement.references
