"""Authority merge stamps wikidata_id onto MARC 650 topic subjects."""

from __future__ import annotations

from app.pipeline.rdf_enrichment import merge_approved_authority


def test_topic_authority_merges_into_subjects() -> None:
    rec = {
        "subjects": [{"term": "מקרא", "type": "topic", "field": "650"}],
    }
    merge_approved_authority(rec, [{
        "entity_text": "מקרא",
        "entity_kind": "topic",
        "role": "subject",
        "wikidata_qid": "Q1845",
        "mazal_id": "123",
        "payload": {},
        "approved": True,
    }])
    subj = rec["subjects"][0]
    assert subj.get("wikidata_id") == "Q1845"
    assert subj.get("mazal_id") == "123"
