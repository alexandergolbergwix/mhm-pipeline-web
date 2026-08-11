"""Studio-cache dict → native WikidataItem (Rule W-181)."""

from __future__ import annotations

from app.routers.wikidata_studio import studio_dict_to_native_item


def test_studio_dict_to_native_item_round_trip_fields() -> None:
    item = studio_dict_to_native_item({
        "local_id": "QDraft_Work_1",
        "entity_type": "work",
        "semantic_type": "",
        "existing_qid": "Q2740944",
        "labels": {"he": "פירוש", "en": "Commentary"},
        "descriptions": {"en": "work"},
        "aliases": {"he": ["פי׳"]},
        "records": ["990000001"],
        "statements": [
            {
                "property_id": "P31",
                "value": "Q47461344",
                "value_type": "wikibase-item",
                "qualifiers": [],
                "references": [],
            },
            {
                "property": "P1476",
                "value": "פירוש",
                "value_type": "monolingualtext",
                "language": "he",
            },
        ],
        "authority_evidence": [{"source": "viaf"}],
        "work_candidate_evidence": [{"reason": "245"}],
    })
    assert item.local_id == "QDraft_Work_1"
    assert item.entity_type == "work"
    assert item.existing_qid == "Q2740944"
    assert item.labels["he"] == "פירוש"
    assert item.aliases["he"] == ["פי׳"]
    assert len(item.statements) == 2
    assert item.statements[0].property_id == "P31"
    assert item.statements[1].property_id == "P1476"
    assert item.records == ["990000001"]
