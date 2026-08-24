"""Helpers for the test.wikidata.org LLM live-readiness judge."""

from __future__ import annotations

from scripts.judge_test_wikidata_live_ready import (
    compact_wikibase_entity,
    merge_live_ready,
    summarise_datavalue,
)


def test_quantity_and_item_datavalues_compact() -> None:
    labels = {"P1104": "number of pages", "Q11573": "metre"}
    assert summarise_datavalue({"id": "Q5"}, {"Q5": "human"}) == "Q5 (human)"
    assert "11" in summarise_datavalue(
        {"amount": "+11", "unit": "http://www.wikidata.org/entity/Q11573"},
        labels,
    )


def test_compact_wikibase_entity_extracts_claims() -> None:
    entity = {
        "id": "Q247996",
        "labels": {"en": {"value": "Ms. F 18702"}},
        "descriptions": {"en": {"value": "Hebrew manuscript"}},
        "claims": {
            "P31": [{
                "mainsnak": {
                    "datatype": "wikibase-item",
                    "datavalue": {"value": {"id": "Q87167"}},
                },
            }],
            "P1104": [{
                "mainsnak": {
                    "datatype": "quantity",
                    "datavalue": {"value": {"amount": "+11", "unit": "1"}},
                },
            }],
        },
    }
    snap = compact_wikibase_entity(
        entity,
        {"P31": "instance of", "Q87167": "manuscript", "P1104": "number of pages"},
        wiki="test",
    )
    assert snap["qid"] == "Q247996"
    assert snap["wiki"] == "test"
    assert snap["claim_count"] == 2
    values = {c["property"]: c["value"] for c in snap["claims"]}
    assert "Q87167" in values["P31"]
    assert "11" in values["P1104"]
    assert "P31" in snap["ref_ids"]


def test_merge_blockers_override_llm_full() -> None:
    audit = {
        "local_id": "manuscript:1",
        "status": "updated",
        "blockers": ["live_wikidata_uri_on_test"],
        "ready_for_live": False,
    }
    merged = merge_live_ready(
        audit=audit,
        llm={"overall": "full", "reasoning": "looks fine"},
        judged=True,
    )
    assert merged["live_ready"] is False
    assert merged["gate"] == "deterministic_blockers"
    assert merged["copy_test_ids_to_live"] is False


def test_merge_llm_full_without_blockers_is_live_ready() -> None:
    audit = {
        "local_id": "work:1",
        "status": "created",
        "blockers": [],
        "ready_for_live": True,
    }
    merged = merge_live_ready(
        audit=audit,
        llm={"overall": "full", "name_ok": "yes", "type_ok": "yes", "role_ok": "yes"},
        judged=True,
    )
    assert merged["live_ready"] is True
    assert merged["gate"] == "llm_full"


def test_merge_skipped_is_not_live_ready() -> None:
    audit = {
        "local_id": "person:1",
        "status": "skipped",
        "blockers": [],
        "ready_for_live": False,
    }
    merged = merge_live_ready(audit=audit, llm=None, judged=False)
    assert merged["live_ready"] is False
    assert merged["gate"] == "not_written"


def test_merge_skip_for_live_excluded_from_live_ready() -> None:
    audit = {
        "local_id": "person:savoy",
        "status": "created",
        "blockers": [],
        "ready_for_live": False,
        "skip_for_live": True,
    }
    merged = merge_live_ready(audit=audit, llm=None, judged=False)
    assert merged["live_ready"] is False
    assert merged["gate"] == "skipped_for_live"
    assert merged["copy_test_ids_to_live"] is False
