"""Tests for the wikidata_autofix evaluator."""

from eval_agent.evaluators import REGISTRY
from eval_agent.evaluators.wikidata_autofix import WikidataAutofixEvaluator


def test_wikidata_autofix_is_registered():
    assert REGISTRY["wikidata_autofix"] is WikidataAutofixEvaluator


def test_extract_skips_without_live_compare():
    ev = WikidataAutofixEvaluator()
    item = {
        "local_id": "person::Foo",
        "entity_type": "person",
        "existing_qid": "Q123",
        "labels": {"en": "Foo"},
        "statements": [],
    }
    out = list(ev.extract_candidates(
        ner_record=item,
        marc_record={},
        threshold=0.0,
    ))
    assert out == []


def test_extract_emits_when_live_present():
    ev = WikidataAutofixEvaluator()
    item = {
        "local_id": "person::Foo",
        "entity_type": "person",
        "existing_qid": "Q123",
        "labels": {"en": "Foo"},
        "statements": [],
        "wikidata_live": {
            "qid": "Q123",
            "rows": [{"status": "conflict", "kind": "label", "key": "he"}],
            "conflict_count": 1,
        },
    }
    out = list(ev.extract_candidates(
        ner_record=item,
        marc_record={},
        threshold=0.0,
    ))
    assert len(out) == 1
    assert out[0].payload["wikidata_live"]["qid"] == "Q123"


def test_parse_verdict_collects_suggested_fixes():
    ev = WikidataAutofixEvaluator()
    from eval_agent.evaluators._base import Candidate

    cand = Candidate(
        record_id="person::Foo",
        evaluator_id="wikidata_autofix",
        sub_type="person",
        payload={"_local_id": "person::Foo"},
        confidence=1.0,
        marc_context={},
        grounded=None,
        role_fields=[],
        exists_in=[],
    )
    raw = {
        "name_ok": "partial",
        "type_ok": "yes",
        "role_ok": "n/a",
        "overall": "partial",
        "reasoning": "Hebrew label inverted",
        "suggested_fixes": [
            {
                "target": "label.he",
                "value": "שמעון דובנוב",
                "confidence": "high",
                "reasoning": "Wikidata canonical",
            },
            {
                "target": "statement.remove",
                "studio_statement_index": 0,
                "confidence": "low",
            },
        ],
    }
    v = ev.parse_verdict(raw, cand)
    fixes = v.candidate_payload.get("suggested_fixes") or []
    assert len(fixes) == 1
    assert fixes[0]["target"] == "label.he"
    assert v.candidate_payload["suggested_fix"]["value"] == "שמעון דובנוב"
