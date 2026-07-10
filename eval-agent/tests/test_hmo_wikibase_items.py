"""HMO Wikibase item ingest helpers."""

from __future__ import annotations

from eval_agent.evaluators._base import Candidate
from eval_agent.evaluators.hmo_wikibase_item import HmoWikibaseItemEvaluator
from eval_agent.ingest import hmo_wikibase_items


def _candidate(entity_type: str) -> Candidate:
    return Candidate(
        record_id="990001",
        evaluator_id="hmo_wikibase_item",
        sub_type=entity_type,
        payload={"entity_type": entity_type, "control_numbers": ["990001"]},
        confidence=1.0,
    )


def test_grounding_treats_production_as_system_labeled_event() -> None:
    grounding = HmoWikibaseItemEvaluator().format_grounding(_candidate("E12_Production"))
    assert "SYSTEM-LABELED EVENT" in grounding
    assert "rule 3d" in grounding


def test_grounding_treats_text_tradition_as_system_labeled_event() -> None:
    grounding = HmoWikibaseItemEvaluator().format_grounding(_candidate("TextTradition"))
    assert "SYSTEM-LABELED EVENT" in grounding


def test_control_number_from_acquisition_source_uri() -> None:
    item = {
        "source_uri": (
            "http://www.ontology.org.il/HebrewManuscripts/2025-12-06"
            "#Acquisition_990000403370205171_01"
        ),
    }
    assert hmo_wikibase_items.control_number(item) == "990000403370205171"


def test_control_number_from_expression_local_id() -> None:
    item = {
        "local_id": "QDraft_Expression_abc_in_990000880710205171",
    }
    assert hmo_wikibase_items.control_number(item) == "990000880710205171"


def test_enrich_control_numbers_propagates_via_deferred_links() -> None:
    items = [
        {
            "local_id": "QDraft_MS_990000403370205171",
            "source_uri": "http://example#MS_990000403370205171",
            "deferred_links": [],
        },
        {
            "local_id": "QDraft_Person_1",
            "source_uri": "http://example#Person_foo",
            "deferred_links": [
                {
                    "source_local_id": "QDraft_Production_990000403370205171",
                    "target_local_id": "QDraft_Person_1",
                    "property_id": "P1",
                }
            ],
        },
        {
            "local_id": "QDraft_Production_990000403370205171",
            "source_uri": "http://example#Production_990000403370205171",
            "deferred_links": [
                {
                    "source_local_id": "QDraft_MS_990000403370205171",
                    "target_local_id": "QDraft_Production_990000403370205171",
                    "property_id": "P2",
                }
            ],
        },
    ]
    enriched = hmo_wikibase_items.enrich_control_numbers(items)
    by_id = {item["local_id"]: item for item in enriched}
    assert by_id["QDraft_Person_1"]["_control_number"] == "990000403370205171"


def test_primary_control_number_prefers_uri_match() -> None:
    item = {
        "control_numbers": ["990000403370205171", "990000880710205171"],
        "source_uri": "http://example#Person_in_990000880710205171",
    }
    assert (
        hmo_wikibase_items.primary_control_number(item)
        == "990000880710205171"
    )

