"""Authority merge stamps IDs onto work titles and corporate entities."""

from __future__ import annotations

from app.pipeline.rdf_enrichment import merge_approved_authority


def test_work_authority_merges_into_contents() -> None:
    rec = {
        "contents": [{"title": "תלמוד בבלי", "source": "505"}],
    }
    merge_approved_authority(rec, [{
        "entity_text": "תלמוד בבלי",
        "entity_kind": "work",
        "role": "contained_work",
        "wikidata_qid": "Q192043",
        "mazal_id": "MAZAL_WORK",
        "viaf_id": "316751234",
        "payload": {},
    }])
    content = rec["contents"][0]
    assert content.get("wikidata_id") == "Q192043"
    assert content.get("mazal_id") == "MAZAL_WORK"
    assert content.get("viaf_id") == "316751234"


def test_corporate_authority_merges_into_contributor() -> None:
    rec = {
        "contributors": [{
            "name": "The National Library of Israel",
            "role": "institution",
            "field": "710",
        }],
    }
    merge_approved_authority(rec, [{
        "entity_text": "The National Library of Israel",
        "entity_kind": "corporate",
        "role": "institution",
        "wikidata_qid": "Q23308",
        "mazal_id": "MAZAL_CORP",
        "payload": {},
    }])
    contrib = rec["contributors"][0]
    assert contrib.get("wikidata_id") == "Q23308"
    assert contrib.get("authority_id") == "MAZAL_CORP"
