"""Tests for the top-30 Wikidata entity-creation rule manifest."""

from __future__ import annotations

from eval_agent.client.typesafe_questions import questions_for
from eval_agent.jev_gates import explain_wikidata_verdict
from eval_agent.wikidata_rules import (
    RULES,
    judgment_rules,
    rule_question_id,
    rule_states,
    rules_by_kind,
)


def test_manifest_shape() -> None:
    assert len(RULES) == 30
    ids = [rule["id"] for rule in RULES]
    assert len(ids) == len(set(ids)), "rule ids must be unique"
    deterministic = rules_by_kind("deterministic")
    judgment = rules_by_kind("judgment")
    assert len(deterministic) == 8
    assert len(judgment) == 22
    for rule in RULES:
        assert rule["axis"] in ("name_ok", "type_ok", "role_ok")
        assert rule["name"] and rule["statement"]
        if rule["kind"] == "judgment":
            assert rule["question"] and rule["fix"]


def test_judgment_rules_filter_by_entity_type() -> None:
    all_ids = {rule["id"] for rule in judgment_rules("")}
    ms_ids = {rule["id"] for rule in judgment_rules("manuscript")}
    person_ids = {rule["id"] for rule in judgment_rules("person")}
    work_ids = {rule["id"] for rule in judgment_rules("work")}
    assert all_ids == {rule["id"] for rule in rules_by_kind("judgment")}
    assert "person_role" in person_ids and "person_role" not in work_ids
    assert "holder_identity" in ms_ids and "holder_identity" not in work_ids
    assert "author_modeling" in work_ids and "author_modeling" not in ms_ids
    # every rule not restricted applies everywhere
    unrestricted = {
        rule["id"] for rule in rules_by_kind("judgment")
        if "applies" not in rule
    }
    assert unrestricted <= ms_ids and unrestricted <= person_ids


def test_questions_for_wikidata_item_includes_rule_questions() -> None:
    from types import SimpleNamespace

    payload = {"entity_type": "manuscript", "statements": []}
    qs = questions_for("wikidata_item", SimpleNamespace(payload=payload))
    expected = {rule_question_id(r["id"]) for r in judgment_rules("manuscript")}
    assert expected <= set(qs)
    # certified axes untouched
    for key in ("name_ok", "type_ok", "role_ok", "p31_ok", "duplicate_risk",
                "evidence_field"):
        assert key in qs
    # no rule questions leak into a non-wikidata evaluator
    qs_person = questions_for("person_ner", SimpleNamespace(payload={}))
    assert not [k for k in qs_person if k.startswith("rule_")]


def test_rule_states_maps_choices() -> None:
    answers = {
        rule_question_id("label_identity"): {"choice": "no", "confidence": 0.9},
        rule_question_id("claims_supported"): {"choice": "partial", "confidence": 0.8},
        rule_question_id("p31_class_choice"): {"choice": "yes", "confidence": 0.9},
        "unrelated": {"choice": "no"},
    }
    states = {rule["id"]: state for rule, state in rule_states(answers)}
    assert states == {
        "label_identity": "fail",
        "claims_supported": "partial",
        "p31_class_choice": "pass",
    }


def test_explanation_plain_language() -> None:
    payload = {
        "entity_type": "person",
        "statements": [{"property": "P106", "value": "scribe",
                        "value_label": "scribe"}],
    }
    answers = {
        "name_ok": {"choice": "yes", "confidence": 0.9},
        "type_ok": {"choice": "yes", "confidence": 0.9},
        "role_ok": {"choice": "partial", "confidence": 0.9},
        "evidence_field": {"choice": "provenance"},
        rule_question_id("person_role"): {
            "choice": "no", "confidence": 0.9,
        },
        rule_question_id("claims_supported"): {
            "choice": "partial", "confidence": 0.9,
        },
        "claim_0": {"noul": 0.3},
    }
    text = explain_wikidata_verdict(
        {"name_ok": "yes", "type_ok": "yes", "role_ok": "partial"},
        "partial", payload, answers,
        ["role_ok: 'no' at confidence 0.20 — routed to review"],
    )
    assert "Do not upload" not in text  # partial, not fail
    assert "curator attention" in text
    assert "person role supported" in text
    assert "P106 (P106) = scribe" in text
    assert "role_ok=" not in text and "p=" not in text


def test_explanation_legacy_answers_fall_back_to_axis_text() -> None:
    payload = {"entity_type": "work", "statements": []}
    answers = {
        "name_ok": {"choice": "yes", "confidence": 0.9},
        "type_ok": {"choice": "yes", "confidence": 0.9},
        "role_ok": {"choice": "partial", "confidence": 0.9},
        "evidence_field": {"choice": "title"},
    }
    text = explain_wikidata_verdict(
        {"name_ok": "yes", "type_ok": "yes", "role_ok": "partial"},
        "partial", payload, answers, [],
    )
    assert "The statements need attention." in text
