"""HMO Wikibase item ingest helpers."""

from __future__ import annotations

from eval_agent.ingest import hmo_wikibase_items


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
