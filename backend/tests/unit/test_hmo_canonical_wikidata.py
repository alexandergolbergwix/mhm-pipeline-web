from app.pipeline.hmo_canonical import normalize_live_entity
from app.pipeline.hmo_canonical_wikidata import wikidata_candidates_from_hmo


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
