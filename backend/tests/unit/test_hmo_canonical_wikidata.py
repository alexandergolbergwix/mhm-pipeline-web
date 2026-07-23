from app.pipeline.hmo_canonical import normalize_live_entity
from app.pipeline.hmo_canonical_wikidata import canonical_wikidata_fingerprint, wikidata_candidates_from_hmo


def test_wikidata_projection_is_grounded_in_hmo_and_filters_unaccepted_evidence() -> None:
    entity = normalize_live_entity({
        "local_id": "Person_A",
        "source_uri": "https://w3id.org/mhm/ontology#Person_A",
        "wikibase_id": "Q1252",
        "labels": {"en": "A"},
        "authority_evidence": [
            {"kind": "wikidata", "value": "Q42", "accepted": True},
            {"kind": "wikidata", "value": "Q43", "accepted": False},
        ],
    })
    result = wikidata_candidates_from_hmo([entity])
    assert result[0]["projection_source"] == "hmo_wikibase"
    assert result[0]["hmo_wikibase_id"] == "Q1252"
    assert [row["value"] for row in result[0]["authority_evidence"]] == ["Q42"]


def test_canonical_wikidata_fingerprint_changes_with_live_claims() -> None:
    base = normalize_live_entity({"local_id": "Person_A", "source_uri": "https://w3id.org/mhm/ontology#Person_A", "wikibase_id": "Q1252"})
    changed = normalize_live_entity({"local_id": "Person_A", "source_uri": "https://w3id.org/mhm/ontology#Person_A", "wikibase_id": "Q1252", "claims": [{"property_uri": "https://w3id.org/mhm/ontology#viaf_id", "value": "123"}]})
    assert canonical_wikidata_fingerprint([base]) != canonical_wikidata_fingerprint([changed])


def test_full_canonical_chain_has_no_legacy_authority_dependency() -> None:
    from app.pipeline.hmo_canonical_rdf import graph_from_canonical_entities
    from app.pipeline.hmo_canonical_wikidata import native_wikidata_claims, quickstatements_from_canonical

    entity = normalize_live_entity({
        "local_id": "Place_Jerusalem",
        "source_uri": "https://w3id.org/mhm/ontology#Place_Jerusalem",
        "wikibase_id": "Q1389",
        "labels": {"en": "Jerusalem", "he": "ירושלים"},
        "descriptions": {"en": "Historic place"},
        "authority_evidence": [
            {"kind": "wikidata", "identifier": "Q1218", "accepted": True},
            {"kind": "mazal", "identifier": "987007270341205171", "accepted": True},
        ],
        "claims": [
            {"wikidata_property": "P31", "value_type": "wikibase-item", "target_qid": "Q515"},
        ],
    })
    graph = graph_from_canonical_entities([entity])
    candidates = wikidata_candidates_from_hmo([entity])
    assert len(graph) > 0
    assert candidates[0]["hmo_wikibase_id"] == "Q1389"
    assert len(native_wikidata_claims(entity)) == 1
    assert "P31\tQ515" in quickstatements_from_canonical([entity])
