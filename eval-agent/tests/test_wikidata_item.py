from __future__ import annotations

import json

from eval_agent.evaluators import REGISTRY
from eval_agent.evaluators.wikidata_item import WikidataItemEvaluator
from eval_agent.ingest import pipeline_run, wikidata_items


def test_discover_accepts_wikidata_items_without_ner_or_authority(tmp_path):
    (tmp_path / "marc_extracted.json").write_text("[]", encoding="utf-8")
    (tmp_path / "wikidata_items.json").write_text("[]", encoding="utf-8")

    run = pipeline_run.discover(tmp_path)

    assert run.ner_results is None
    assert run.authority_results is None
    assert run.wikidata_items == tmp_path.resolve() / "wikidata_items.json"


def test_wikidata_items_load_normalises_local_id(tmp_path):
    path = tmp_path / "wikidata_items.json"
    path.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "local_id": "person::Maimonides",
                        "entity_type": "person",
                        "labels": {"en": "Maimonides"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    loaded = wikidata_items.load(path)

    assert loaded[0]["_local_id"] == "person::Maimonides"


def test_wikidata_item_evaluator_emits_local_id_keyed_candidate():
    evaluator = WikidataItemEvaluator()
    item = {
        "_local_id": "12345",
        "entity_type": "manuscript",
        "labels": {"en": "Jerusalem, NLI, Ms. Heb. 8"},
        "descriptions": {"en": "Hebrew manuscript, 16th century, NLI"},
        "statements": [
            {
                "property": "P31",
                "property_label": "instance of",
                "value": "Q87167",
                "value_label": "manuscript",
            }
        ],
        "existing_qid": None,
        "validation_issues": [
            {"code": "missing_reference", "severity": "warning", "message": "No ref"}
        ],
        "authority_evidence": [{"source": "NLI", "birth_year": 1500}],
        "local_reference_targets": {"person::1": {"entity_type": "person", "labels": {"en": "Jane Doe"}}},
    }
    marc = {
        "_control_number": "12345",
        "title": "Sefer ha-refu'ot",
        "dates": "16th century",
        "shelfmark": "Ms. Heb. 8",
    }

    candidates = list(
        evaluator.extract_candidates(ner_record=item, marc_record=marc, threshold=0.9)
    )

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.record_id == "12345"
    assert candidate.evaluator_id == "wikidata_item"
    assert candidate.payload["_local_id"] == "12345"
    assert candidate.payload["validation_issues"][0]["code"] == "missing_reference"
    assert candidate.payload["authority_evidence"][0]["source"] == "NLI"
    assert "person::1" in candidate.payload["local_reference_targets"]
    assert candidate.marc_context["title"] == "Sefer ha-refu'ot"


def test_wikidata_item_is_registered():
    assert REGISTRY["wikidata_item"] is WikidataItemEvaluator
