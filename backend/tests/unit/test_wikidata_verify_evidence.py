"""Multi-source evidence packs for Wikidata Studio AI verify."""

from __future__ import annotations

from app.pipeline.wikidata_verify_evidence import (
    build_verify_evidence_pack,
    enrich_items_with_verify_evidence,
)


def test_build_verify_evidence_pack_partitions_channels() -> None:
    item = {
        "local_id": "person:1",
        "entity_type": "person",
        "record_ids": ['"990000000000000001"'],
        "hmo_wikibase_id": "Q42",
        "source_uri": "https://w3id.org/mhm/ontology#Person_1",
        "existing_qid": "Q1339",
        "projection_source": "hmo_wikibase",
        "authority_evidence": [
            {"kind": "viaf", "identifier": "123", "accepted": True},
            {"kind": "mazal", "identifier": "987012345", "accepted": True},
            {"kind": "wikidata", "identifier": "Q1339", "accepted": True},
        ],
        "statements": [
            {"property": "P214", "value": "123"},
            {"property": "P8189", "value": "987012345"},
            {
                "property": "P2888",
                "value": "https://mhm-hmo.wikibase.cloud/wiki/Item:Q42",
            },
        ],
    }
    marc_records = [
        {
            "_control_number": "990000000000000001",
            "title": "Test MS",
            "authors": ["Author"],
        },
    ]
    pack = build_verify_evidence_pack(item, marc_records)
    assert pack["marc_present"] is True
    assert "Test MS" in str(pack["marc"].get("title") or "")
    assert pack["viaf"]["authority_rows"][0]["identifier"] == "123"
    assert pack["viaf"]["from_statements"][0]["value"] == "123"
    assert pack["mazal"]["authority_rows"][0]["identifier"] == "987012345"
    assert pack["wikidata_existing"]["existing_qid"] == "Q1339"
    assert pack["hmo_wikibase"]["page_url"] == (
        "https://mhm-hmo.wikibase.cloud/wiki/Item:Q42"
    )
    assert pack["hmo_wikibase"]["bridge_statements"][0]["host"] == "hmo_wikibase"
    assert "WikiProject_Manuscripts/Data_Model" in pack["wpm_data_model_url"]


def test_enrich_items_attaches_verify_evidence_and_marc_context() -> None:
    items = [
        {
            "local_id": "ms:1",
            "entity_type": "manuscript",
            "records": ["990000000000000002"],
            "hmo_wikibase_id": "Q7",
            "statements": [],
            "authority_evidence": [],
        },
    ]
    marc_records = [
        {"_control_number": '"990000000000000002"', "title": "Quoted CN MS"},
    ]
    enrich_items_with_verify_evidence(items, marc_records)
    assert items[0]["verify_evidence"]["marc_present"] is True
    assert items[0]["_marc_context"] == items[0]["verify_evidence"]["marc"]
