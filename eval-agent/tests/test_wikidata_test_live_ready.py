from __future__ import annotations

from eval_agent.evaluators import REGISTRY
from eval_agent.evaluators.wikidata_test_live_ready import WikidataTestLiveReadyEvaluator


def test_wikidata_test_live_ready_is_registered() -> None:
    assert REGISTRY["wikidata_test_live_ready"] is WikidataTestLiveReadyEvaluator


def test_extract_includes_test_snapshot_and_forbids_copying_test_ids() -> None:
    evaluator = WikidataTestLiveReadyEvaluator()
    item = {
        "_local_id": "manuscript:F18702",
        "entity_type": "manuscript",
        "labels": {"en": "Cambridge, CUL, F 18702"},
        "descriptions": {"en": "Hebrew manuscript, 15th century"},
        "statements": [
            {
                "property": "P31",
                "property_label": "instance of",
                "value": "Q87167",
                "value_label": "manuscript",
            }
        ],
        "existing_qid": None,
        "test_wiki_snapshot": {
            "wiki": "test",
            "qid": "Q247996",
            "claim_count": 8,
        },
        "deterministic_audit": {
            "status": "updated",
            "blockers": [],
            "ready_for_live": True,
        },
        "upload_outcome": {"status": "updated", "qid": "Q247996"},
        "live_existing_snapshot": {},
    }
    marc = {
        "_control_number": "F18702",
        "title": "Sefer",
        "shelfmark": "F 18702",
    }

    candidates = list(
        evaluator.extract_candidates(ner_record=item, marc_record=marc, threshold=0.0)
    )
    assert len(candidates) == 1
    cand = candidates[0]
    assert cand.evaluator_id == "wikidata_test_live_ready"
    assert cand.payload["test_wiki_snapshot"]["qid"] == "Q247996"
    prompt = evaluator.build_prompt(cand)
    assert "NEVER copy a test.wikidata.org Q-id" in prompt
    assert "Q247996" in prompt
    assert "www.wikidata.org would receive" in prompt
    assert "Test P/Q numbers WILL differ from live" in prompt
    assert "not a live QID" in prompt
    assert "W-192" in prompt
    assert "__LOCAL:" in prompt
    assert cand.payload["deterministic_audit"]["ready_for_live"] is True
