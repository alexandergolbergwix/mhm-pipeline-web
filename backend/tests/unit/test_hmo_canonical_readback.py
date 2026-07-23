from app.pipeline.hmo_canonical import canonical_snapshot_from_wikibase


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
