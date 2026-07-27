"""WikiProject Manuscripts skill pack for Studio judges."""

from __future__ import annotations

from eval_agent.evaluators.hmo_wikibase_item import HmoWikibaseItemEvaluator
from eval_agent.evaluators.wikidata_item import WikidataItemEvaluator
from eval_agent.skills.wikidata_manuscripts import (
    collect_claim_pids,
    load_skill,
    skill_context_for,
    skill_version,
)


def test_skill_loads_and_versions() -> None:
    skill = load_skill()
    assert skill["id"] == "wikidata_manuscripts"
    assert skill_version() == "w124_v1"
    assert "always" in skill
    assert "P50" in skill["claim_triggers"]


def test_wikidata_manuscript_context_includes_carrier_rules() -> None:
    text = skill_context_for(
        channel="wikidata",
        entity_type="manuscript",
        claim_pids=["P50", "P1574"],
    )
    assert "SKILL:" in text
    assert "P1574" in text
    assert "HARD FAIL" in text
    assert "Entity slice [manuscript]" in text
    assert "HMO Wikibase → public Wikidata" not in text


def test_hmo_work_context_includes_projection_checklist() -> None:
    text = skill_context_for(
        channel="hmo",
        entity_type="F1_Work",
        claim_pids=["P50"],
    )
    assert "Channel: hmo" in text
    assert "Entity slice [work]" in text
    assert "HMO Wikibase → public Wikidata projection checklist" in text
    assert "hmo_wikidata_pq_mapper" in text


def test_hmo_structural_skips_manuscript_fingerprint() -> None:
    text = skill_context_for(
        channel="hmo",
        entity_type="CatalogStep",
    )
    assert "Entity slice [hmo_structural]" in text
    assert "Wikidata projection is out of scope" in text


def test_collect_claim_pids_from_wikidata_and_hmo_shapes() -> None:
    assert collect_claim_pids({
        "statements": [{"property": "P31"}, {"property_id": "p50"}, {"property": "x"}],
    }) == ["P31", "P50"]
    assert collect_claim_pids({
        "claims": [{"property_id": "P1104"}],
    }) == ["P1104"]


def test_wikidata_evaluator_prompt_embeds_skill() -> None:
    from eval_agent.evaluators._base import Candidate

    cand = Candidate(
        record_id="ms-1",
        evaluator_id="wikidata_item",
        sub_type="manuscript",
        payload={
            "entity_type": "manuscript",
            "labels": {"en": "MS Heb. 1"},
            "descriptions": {},
            "aliases": {},
            "statements": [{"property": "P50", "value": "Q1"}],
            "statement_count": 1,
            "validation_issues": [],
            "authority_evidence": [],
            "work_candidate_evidence": {},
            "local_reference_targets": {},
            "verify_evidence": {
                "marc_present": True,
                "viaf": {"authority_rows": [{"kind": "viaf", "identifier": "1"}]},
                "mazal": {"authority_rows": []},
                "wikidata_existing": {"existing_qid": None},
                "hmo_wikibase": {
                    "hmo_wikibase_id": "Q9",
                    "page_url": "https://mhm-hmo.wikibase.cloud/wiki/Item:Q9",
                },
            },
        },
        confidence=1.0,
        marc_context={"title": "Test"},
    )
    prompt = WikidataItemEvaluator().build_prompt(cand)
    assert "WikiProject Manuscripts" in prompt or "SKILL:" in prompt
    assert "HARD FAIL" in prompt
    assert "P50" in prompt
    assert "VIAF pack" in prompt
    assert "HMO Wikibase pack" in prompt
    assert "mhm-hmo.wikibase.cloud/wiki/Item:Q9" in prompt


def test_skill_mentions_hmo_bridge_and_multi_source_evidence() -> None:
    text = skill_context_for(
        channel="wikidata",
        entity_type="manuscript",
        claim_pids=["P2888"],
    )
    assert "P2888" in text
    assert "mhm-hmo.wikibase.cloud" in text or "HMO" in text
    assert "VIAF" in text or "evidence" in text.lower()


def test_hmo_evaluator_prompt_embeds_projection_skill() -> None:
    from eval_agent.evaluators._base import Candidate

    cand = Candidate(
        record_id="work-1",
        evaluator_id="hmo_wikibase_item",
        sub_type="item",
        payload={
            "_local_id": "work-1",
            "entity_type": "F1_Work",
            "class_qid": "Q12",
            "control_numbers": ["990001"],
            "labels": {"he": "משנה תורה"},
            "descriptions": {"en": "Work in MS 990001"},
            "claims": [{"property_id": "P50", "value": "Q5"}],
            "shacl_issues": [],
            "blocking_shacl": False,
        },
        confidence=1.0,
        marc_context={"title": "משנה תורה"},
    )
    prompt = HmoWikibaseItemEvaluator().build_prompt(cand)
    assert "SKILL:" in prompt
    assert "HMO Wikibase → public Wikidata" in prompt
    assert "class_qid" in prompt.lower() or "NOT Wikidata" in prompt
