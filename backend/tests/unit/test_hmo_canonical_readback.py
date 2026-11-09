import pytest

from app.pipeline.hmo_canonical import canonical_snapshot_from_wikibase, normalize_live_entity
from app.pipeline.hmo_canonical_wikidata import native_items_from_hmo


def test_raw_wikibase_readback_is_normalized_to_canonical_shape() -> None:
    raw = {
        "id": "Q42",
        "labels": {"en": {"language": "en", "value": "Douglas Adams"}},
        "descriptions": {"en": {"language": "en", "value": "writer"}},
        "aliases": {"en": [{"language": "en", "value": "D. Adams"}]},
        "claims": {
            "P1": [{
                "mainsnak": {
                    "snaktype": "value",
                    "datatype": "string",
                    "datavalue": {"value": "example", "type": "string"},
                }
            }],
            "P2": [{
                "mainsnak": {
                    "snaktype": "value",
                    "datatype": "wikibase-item",
                    "datavalue": {"value": {"id": "Q43", "numeric-id": 43}, "type": "wikibase-entityid"},
                }
            }],
        },
    }
    snapshot = canonical_snapshot_from_wikibase(
        raw,
        local_id="Person_Adams",
        source_uri="https://w3id.org/mhm/ontology#Person_Adams",
        authority_evidence=[],
        entity_type="person",
        control_numbers=["1"],
        property_uris={"P1": "https://w3id.org/mhm/ontology#name", "P2": "https://w3id.org/mhm/ontology#related"},
        target_uris={"Q43": "https://w3id.org/mhm/ontology#Person_Related"},
    )
    assert snapshot["labels"] == {"en": "Douglas Adams"}
    assert snapshot["descriptions"] == {"en": "writer"}
    assert snapshot["aliases"] == {"en": ["D. Adams"]}
    assert snapshot["claims"] == [
        {"property_uri": "https://w3id.org/mhm/ontology#name", "property_id": "P1", "datatype": "string", "value": "example"},
        {"property_uri": "https://w3id.org/mhm/ontology#related", "target_uri": "https://w3id.org/mhm/ontology#Person_Related", "property_id": "P2"},
    ]
    assert snapshot["wikibase_id"] == "Q42"


_INVENTORY_URI = "https://w3id.org/mhm/ontology#inventory_number"
_SOURCE_NLI_ID = {
    "snaktype": "value",
    "property": "P3959",
    "datatype": "external-id",
    "datavalue": {"value": "990001", "type": "string"},
}


def _manuscript_readback(statement: dict[str, object]) -> dict[str, object]:
    return {
        "id": "Q1252",
        "labels": {"en": {"language": "en", "value": "A manuscript"}},
        "claims": {"P217": [statement]},
    }


def _readback_snapshot(raw: dict[str, object]) -> dict[str, object]:
    return canonical_snapshot_from_wikibase(
        raw,
        local_id="Manuscript_A",
        source_uri="https://w3id.org/mhm/ontology#Manuscript_A",
        authority_evidence=[],
        entity_type="F4_Manifestation_Singleton",
        control_numbers=["990001"],
        property_uris={"P217": _INVENTORY_URI},
        target_uris={},
    )


def test_preferred_sourced_p217_keeps_rank_and_source_through_readback() -> None:
    """Regression — readback dropped rank and references of an inventory number."""
    raw = _manuscript_readback({
        "mainsnak": {
            "snaktype": "value",
            "property": "P217",
            "datatype": "string",
            "datavalue": {"value": "F 1", "type": "string"},
        },
        "rank": "preferred",
        "references": [{"hash": "h1", "snaks": {"P3959": [_SOURCE_NLI_ID]}}],
    })
    snapshot = _readback_snapshot(raw)
    assert snapshot["claims"] == [{
        "property_uri": _INVENTORY_URI,
        "property_id": "P217",
        "datatype": "string",
        "value": "F 1",
        "rank": "preferred",
        "reference_blocks": [[{"property": "P3959", "type": "external-id", "value": "990001"}]],
    }]

    entity = normalize_live_entity({**snapshot, "wikibase_id": "Q1252"})
    items = native_items_from_hmo([entity])
    statements = [s for s in items[0].statements if s.property_id == "P217"]
    assert len(statements) == 1
    assert statements[0].rank == "preferred"
    assert statements[0].references == [{"property": "P3959", "type": "external-id", "value": "990001"}]
    assert statements[0].reference_blocks == [[{"property": "P3959", "type": "external-id", "value": "990001"}]]


def test_unsupported_reference_snak_fails_closed_instead_of_dropping() -> None:
    item_reference = {
        "snaktype": "value",
        "property": "P248",
        "datatype": "wikibase-item",
        "datavalue": {"value": {"id": "Q43", "numeric-id": 43}, "type": "wikibase-entityid"},
    }
    raw = _manuscript_readback({
        "mainsnak": {
            "snaktype": "value",
            "property": "P217",
            "datatype": "string",
            "datavalue": {"value": "F 1", "type": "string"},
        },
        "references": [{"snaks": {"P248": [item_reference]}}],
    })
    with pytest.raises(ValueError, match="HMO readback P217: unsupported wikibase-item snak on P248"):
        _readback_snapshot(raw)


def test_unknown_rank_fails_closed() -> None:
    raw = _manuscript_readback({
        "mainsnak": {
            "snaktype": "value",
            "property": "P217",
            "datatype": "string",
            "datavalue": {"value": "F 1", "type": "string"},
        },
        "rank": "best",
    })
    with pytest.raises(ValueError, match="unsupported rank 'best'"):
        _readback_snapshot(raw)
