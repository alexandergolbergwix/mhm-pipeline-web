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
