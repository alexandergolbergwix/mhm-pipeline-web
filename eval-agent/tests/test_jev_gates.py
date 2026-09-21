"""Tests for the deterministic Jev gates (ported from the bake-off harness)."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from eval_agent import jev_gates
from eval_agent.client.rate_limiter import RateLimiter
from eval_agent.client.typesafe_client import TypesafeJudge
from eval_agent.evaluators._base import Candidate
from eval_agent.evaluators.hmo_wikibase_item import HmoWikibaseItemEvaluator
from eval_agent.evaluators.person_ner import PersonNERevaluator


def _answers(axes: dict[str, str], conf: float = 0.9) -> dict[str, Any]:
    out = {
        "name_ok": {"choice": axes.get("name_ok", "yes"), "confidence": conf},
        "type_ok": {"choice": axes.get("type_ok", "yes"), "confidence": conf},
    }
    if "role_ok" in axes:
        out["role_ok"] = {"choice": axes["role_ok"], "confidence": conf}
    return out


def _hmo_payload() -> dict:
    """HMO item payload with a deterministic text artifact (unclosed paren)."""
    return {
        "_local_id": "QDraft_990123456789012345",
        "labels": {"en": "Sefer Torah (folios 1"},
        "descriptions": {"en": "a manuscript"},
        "statements": [],
    }


def _candidate(evaluator_id: str, payload: dict | None = None) -> Candidate:
    return Candidate(
        record_id="rec1", evaluator_id=evaluator_id, sub_type="",
        payload=payload or {}, confidence=0.9,
        marc_context={"title": "Sefer Torah"},
    )


def test_universal_overall_table() -> None:
    f = jev_gates.universal_overall
    assert f({"name_ok": "yes", "type_ok": "yes", "role_ok": "n/a"}) == "full"
    assert f({"name_ok": "yes", "type_ok": "no", "role_ok": "yes"}) == "fail"
    assert f({"name_ok": "yes", "type_ok": "partial", "role_ok": "n/a"}) == "partial"
    assert f({"name_ok": "unknown", "type_ok": "unknown", "role_ok": "unknown"}) == "full"


def test_deterministic_text_artifacts_findings() -> None:
    payload = {
        "_local_id": "QDraft_990123456789012345",
        "labels": {"en": "Sefer Torah (folios 1"},
        "descriptions": {"en": "a manuscript"},
    }
    findings = jev_gates.deterministic_text_artifacts(payload)
    names = {name for name, _ in findings}
    assert "unclosed parenthesis" in names
    assert "truncated folio range" in names


def test_deterministic_text_artifacts_manuscript_mismatch() -> None:
    payload = {
        "_local_id": "QDraft_990123456789012345",
        "labels": {"en": "A manuscript"},
        "descriptions": {"en": "manuscript 990999999999999999"},
        "control_numbers": ["990123456789012345"],
    }
    findings = jev_gates.deterministic_text_artifacts(payload)
    assert any(name == "manuscript mismatch" for name, _ in findings)


def test_clean_text_has_no_artifacts() -> None:
    payload = {
        "_local_id": "QDraft_990123456789012345",
        "labels": {"en": "Sefer Torah"},
        "descriptions": {"en": "a complete manuscript (folios 1-40)"},
    }
    assert jev_gates.deterministic_text_artifacts(payload) == []


def test_confidence_gate_routes_low_conf_no_to_partial() -> None:
    verdict = {
        "name_ok": "yes", "type_ok": "yes", "role_ok": "no",
        "overall": "fail", "reasoning": "r", "suggested_fix": None,
    }
    meta = {"answers": _answers({"name_ok": "yes", "type_ok": "yes", "role_ok": "no"}, conf=0.2)}
    gated = jev_gates.apply_jev_gates(
        verdict, evaluator_id="person_ner",
        candidate=_candidate("person_ner"), meta=meta,
    )
    assert gated["role_ok"] == "partial"  # conf 0.2 < ROLE_CONF_GATE → review
    assert gated["overall"] == "partial"
    assert "routed to review" in gated["reasoning"]


def test_confidence_gate_keeps_high_conf_no() -> None:
    verdict = {
        "name_ok": "yes", "type_ok": "yes", "role_ok": "no",
        "overall": "fail", "reasoning": "r", "suggested_fix": None,
    }
    meta = {"answers": _answers({"name_ok": "yes", "type_ok": "yes", "role_ok": "no"}, conf=0.9)}
    gated = jev_gates.apply_jev_gates(
        verdict, evaluator_id="person_ner",
        candidate=_candidate("person_ner"), meta=meta,
    )
    assert gated["role_ok"] == "no"
    assert gated["overall"] == "fail"


def test_validator_error_gate_forces_role_fail() -> None:
    payload = {"statements": [], "validation_issues": [
        {"severity": "ERROR", "message": "missing identifier"},
    ]}
    verdict = {
        "name_ok": "yes", "type_ok": "yes", "role_ok": "yes",
        "overall": "full", "reasoning": "r", "suggested_fix": None,
    }
    meta = {"answers": _answers({"name_ok": "yes", "type_ok": "yes", "role_ok": "yes"}, conf=0.2)}
    gated = jev_gates.apply_jev_gates(
        verdict, evaluator_id="wikidata_item",
        candidate=_candidate("wikidata_item", payload), meta=meta,
    )
    # ERROR-severity is a rule, not a judgment: the conf gate must not soften it.
    assert gated["role_ok"] == "no"
    assert gated["overall"] == "fail"
    assert "upload gate" in gated["reasoning"]


def _wiki_payload(extra: dict | None = None) -> dict:
    """A well-formed wikidata_item payload: P31 present, no duplicate hit."""
    payload = {
        "statements": [{"property": "P31", "value": "Q5", "value_label": "human"}],
        "existing_qid": None,
        "verify_evidence": {},
    }
    payload.update(extra or {})
    return payload


def test_duplicate_gate_fails_identifier_match() -> None:
    payload = _wiki_payload({
        "verify_evidence": {"wikidata_existing": {"duplicate_check": {
            "status": "candidates_found",
            "candidates": [{"qid": "Q999", "matched_on": "P8189=9870…"}],
        }}},
    })
    verdict = {
        "name_ok": "yes", "type_ok": "yes", "role_ok": "yes",
        "overall": "full", "reasoning": "r", "suggested_fix": None,
    }
    # Judge said yes at low confidence — the gate is mechanical and wins.
    meta = {"answers": {
        **_answers({"name_ok": "yes", "type_ok": "yes", "role_ok": "yes"}, conf=0.2),
        "duplicate_risk": {"choice": "no_duplicate_risk", "confidence": 0.9},
    }}
    gated = jev_gates.apply_jev_gates(
        verdict, evaluator_id="wikidata_item",
        candidate=_candidate("wikidata_item", payload), meta=meta,
    )
    assert gated["type_ok"] == "no"
    assert gated["overall"] == "fail"
    assert "candidates_found" in gated["reasoning"]
    assert "Q999" in gated["reasoning"]


def test_duplicate_gate_ignores_update_and_adopted_and_absent() -> None:
    verdict = {
        "name_ok": "yes", "type_ok": "yes", "role_ok": "yes",
        "overall": "full", "reasoning": "r", "suggested_fix": None,
    }
    meta = {"answers": {
        **_answers({"name_ok": "yes", "type_ok": "yes", "role_ok": "yes"}),
        "duplicate_risk": {"choice": "no_duplicate_risk"},
    }}

    # existing_qid set → UPDATE, no CREATE risk.
    update_payload = _wiki_payload({
        "existing_qid": "Q42",
        "verify_evidence": {"wikidata_existing": {"duplicate_check": {
            "status": "candidates_found",
            "candidates": [{"qid": "Q42"}],
        }}},
    })
    gated = jev_gates.apply_jev_gates(
        verdict, evaluator_id="wikidata_item",
        candidate=_candidate("wikidata_item", update_payload), meta=meta,
    )
    assert gated["type_ok"] == "yes" and gated["overall"] == "full"

    # adoption.adopted: true → settled (Rule W-139).
    adopted_payload = _wiki_payload({
        "verify_evidence": {"wikidata_existing": {"duplicate_check": {
            "status": "candidates_found",
            "candidates": [{"qid": "Q42"}],
            "adoption": {"adopted": True},
        }}},
    })
    gated = jev_gates.apply_jev_gates(
        verdict, evaluator_id="wikidata_item",
        candidate=_candidate("wikidata_item", adopted_payload), meta=meta,
    )
    assert gated["type_ok"] == "yes" and gated["overall"] == "full"

    # absent → CREATE is reasonable on the duplicate axis.
    absent_payload = _wiki_payload({
        "verify_evidence": {"wikidata_existing": {"duplicate_check": {
            "status": "absent", "candidates": [],
        }}},
    })
    gated = jev_gates.apply_jev_gates(
        verdict, evaluator_id="wikidata_item",
        candidate=_candidate("wikidata_item", absent_payload), meta=meta,
    )
    assert gated["type_ok"] == "yes" and gated["overall"] == "full"


def test_duplicate_gate_not_run_never_moves_an_axis() -> None:
    payload = _wiki_payload({
        "verify_evidence": {"wikidata_existing": {"duplicate_check": {
            "status": "not_run", "note": "budget deferred",
        }}},
    })
    verdict = {
        "name_ok": "yes", "type_ok": "yes", "role_ok": "yes",
        "overall": "full", "reasoning": "r", "suggested_fix": None,
    }
    meta = {"answers": {
        **_answers({"name_ok": "yes", "type_ok": "yes", "role_ok": "yes"}),
        "duplicate_risk": {"choice": "unknown"},
    }}
    gated = jev_gates.apply_jev_gates(
        verdict, evaluator_id="wikidata_item",
        candidate=_candidate("wikidata_item", payload), meta=meta,
    )
    assert gated["type_ok"] == "yes"
    assert gated["overall"] == "full"
    assert "duplicate_risk: unknown" in gated["reasoning"]


def test_missing_p31_fails_wikidata_item() -> None:
    payload = _wiki_payload({"statements": [{"property": "P1476", "value": "t"}]})
    verdict = {
        "name_ok": "yes", "type_ok": "yes", "role_ok": "yes",
        "overall": "full", "reasoning": "r", "suggested_fix": None,
    }
    meta = {"answers": _answers({"name_ok": "yes", "type_ok": "yes", "role_ok": "yes"}, conf=0.95)}
    gated = jev_gates.apply_jev_gates(
        verdict, evaluator_id="wikidata_item",
        candidate=_candidate("wikidata_item", payload), meta=meta,
    )
    assert gated["type_ok"] == "no"
    assert gated["overall"] == "fail"
    assert "no P31" in gated["reasoning"]


def test_p31_answer_no_fails_overall() -> None:
    payload = _wiki_payload()
    verdict = {
        "name_ok": "yes", "type_ok": "yes", "role_ok": "yes",
        "overall": "full", "reasoning": "r", "suggested_fix": None,
    }
    meta = {"answers": {
        **_answers({"name_ok": "yes", "type_ok": "yes", "role_ok": "yes"}),
        "p31_ok": {"choice": "no", "confidence": 0.9},
    }}
    gated = jev_gates.apply_jev_gates(
        verdict, evaluator_id="wikidata_item",
        candidate=_candidate("wikidata_item", payload), meta=meta,
    )
    assert gated["overall"] == "fail"
    assert "P31 typing: no" in gated["reasoning"]


def test_p31_answer_partial_caps_overall() -> None:
    payload = _wiki_payload()
    verdict = {
        "name_ok": "yes", "type_ok": "yes", "role_ok": "yes",
        "overall": "full", "reasoning": "r", "suggested_fix": None,
    }
    meta = {"answers": {
        **_answers({"name_ok": "yes", "type_ok": "yes", "role_ok": "yes"}),
        "p31_ok": {"choice": "partial", "confidence": 0.9},
    }}
    gated = jev_gates.apply_jev_gates(
        verdict, evaluator_id="wikidata_item",
        candidate=_candidate("wikidata_item", payload), meta=meta,
    )
    assert gated["overall"] == "partial"


def test_duplicate_status_surface_precedence() -> None:
    # _wikidata_existence wins over the pack.
    payload = _wiki_payload({
        "_wikidata_existence": {"status": "already_linked"},
        "verify_evidence": {"wikidata_existing": {"duplicate_check": {
            "status": "candidates_found",
        }}},
    })
    assert jev_gates.duplicate_check_from_payload(payload)["status"] == "already_linked"
    # _duplicate_status is the last surface.
    assert jev_gates.duplicate_check_from_payload(
        _wiki_payload({"_duplicate_status": "absent"})
    )["status"] == "absent"
    # not_run is a placeholder, not an answer.
    assert jev_gates.duplicate_check_from_payload(
        _wiki_payload({"verify_evidence": {"wikidata_existing": {
            "duplicate_check": {"status": "not_run"},
        }}})
    ) == {}


def test_artifact_downgrade_caps_name_ok() -> None:
    verdict = {
        "name_ok": "yes", "type_ok": "yes", "role_ok": "n/a",
        "overall": "full", "reasoning": "r", "suggested_fix": None,
    }
    meta = {"answers": _answers({"name_ok": "yes", "type_ok": "yes"})}
    gated = jev_gates.apply_jev_gates(
        verdict, evaluator_id="hmo_wikibase_item",
        candidate=_candidate("hmo_wikibase_item", _hmo_payload()), meta=meta,
    )
    assert gated["name_ok"] == "partial"
    assert gated["overall"] == "partial"
    assert "deterministic text artifacts" in gated["reasoning"]


def test_model_text_artifact_downgrade() -> None:
    verdict = {
        "name_ok": "yes", "type_ok": "yes", "role_ok": "n/a",
        "overall": "full", "reasoning": "r", "suggested_fix": None,
    }
    meta = {"answers": {
        "name_ok": {"choice": "yes", "confidence": 0.9},
        "type_ok": {"choice": "yes", "confidence": 0.9},
        "text_quality": {"noul": 1.0},
    }}
    gated = jev_gates.apply_jev_gates(
        verdict, evaluator_id="hmo_wikibase_item",
        candidate=_candidate("hmo_wikibase_item"), meta=meta,
    )
    assert gated["name_ok"] == "partial"
    assert "text artifacts flagged" in gated["reasoning"]


def test_claim_checks_cap_wikidata_overall_at_partial() -> None:
    payload = {"statements": [
        {"property": "P31", "value": "Q1"},
        {"property": "P1476", "value": "title"},
    ]}
    verdict = {
        "name_ok": "yes", "type_ok": "yes", "role_ok": "yes",
        "overall": "full", "reasoning": "r", "suggested_fix": None,
    }
    answers = _answers({"name_ok": "yes", "type_ok": "yes", "role_ok": "yes"})
    answers["claim_0"] = {"noul": 0.9}
    answers["claim_1"] = {"noul": 0.2}  # unsupported claim
    gated = jev_gates.apply_jev_gates(
        verdict, evaluator_id="wikidata_item",
        candidate=_candidate("wikidata_item", payload), meta={"answers": answers},
    )
    assert gated["overall"] == "partial"


def test_name_quality_malformed_fails_hmo_item() -> None:
    verdict = {
        "name_ok": "yes", "type_ok": "yes", "role_ok": "n/a",
        "overall": "full", "reasoning": "r", "suggested_fix": None,
    }
    meta = {"answers": {
        "name_ok": {"choice": "yes", "confidence": 0.9},
        "type_ok": {"choice": "yes", "confidence": 0.9},
        "name_quality": {"choice": "malformed"},
    }}
    gated = jev_gates.apply_jev_gates(
        verdict, evaluator_id="hmo_wikibase_item",
        candidate=_candidate("hmo_wikibase_item"), meta=meta,
    )
    assert gated["overall"] == "fail"


def test_match_kind_absent_keeps_ner_at_partial() -> None:
    verdict = {
        "name_ok": "yes", "type_ok": "yes", "role_ok": "n/a",
        "overall": "full", "reasoning": "r", "suggested_fix": None,
    }
    meta = {"answers": {
        "name_ok": {"choice": "yes", "confidence": 0.9},
        "type_ok": {"choice": "yes", "confidence": 0.9},
        "match_kind": {"choice": "absent"},
    }}
    gated = jev_gates.apply_jev_gates(
        verdict, evaluator_id="person_ner",
        candidate=_candidate("person_ner"), meta=meta,
    )
    assert gated["overall"] == "partial"


def test_missing_meta_returns_verdict_unchanged() -> None:
    verdict = {"name_ok": "yes", "type_ok": "yes", "role_ok": "n/a",
               "overall": "full", "reasoning": "r", "suggested_fix": None}
    gated = jev_gates.apply_jev_gates(
        verdict, evaluator_id="person_ner",
        candidate=_candidate("person_ner"), meta=None,
    )
    assert gated == verdict


def test_gated_verdict_validates_against_schema() -> None:
    import json
    from pathlib import Path

    import jsonschema

    schema_path = Path(jev_gates.__file__).parent.parent / "config" / "schemas" / "verdict.v2.json"
    full_schema = json.loads(schema_path.read_text(encoding="utf-8"))
    verdict = {
        "name_ok": "yes", "type_ok": "yes", "role_ok": "n/a",
        "overall": "full", "reasoning": "r", "suggested_fix": None,
    }
    meta = {"answers": _answers({"name_ok": "yes", "type_ok": "yes"}, conf=0.1)}
    gated = jev_gates.apply_jev_gates(
        verdict, evaluator_id="hmo_wikibase_item",
        candidate=_candidate("hmo_wikibase_item", _hmo_payload()), meta=meta,
    )
    # Inner verdict object (the cached shape): no extra fields allowed.
    jsonschema.validate(gated, full_schema["properties"]["verdict"])

    # Full results.jsonl envelope (person_ner is in the schema's enum).
    from eval_agent.cache.verdict_cache import VerdictCache

    cand = _candidate("person_ner")
    v = PersonNERevaluator().parse_verdict(
        jev_gates.apply_jev_gates(
            {
                "name_ok": "yes", "type_ok": "yes", "role_ok": "yes",
                "overall": "full", "reasoning": "r", "suggested_fix": None,
            },
            evaluator_id="person_ner", candidate=cand,
            meta={"answers": _answers(
                {"name_ok": "yes", "type_ok": "yes", "role_ok": "yes"}, conf=0.9,
            )},
        ),
        cand,
    )
    v.judge_id = "typesafe/jev-1.13.0"
    v.cache_key = VerdictCache.key(judge_id="typesafe/jev-1.13.0::linear", prompt="p")
    jsonschema.validate(v.to_jsonl_record(), full_schema)


# ── session wiring: gates applied for typesafe judges only ────────────────


class _ScriptedTypesafe(TypesafeJudge):
    """Real TypesafeJudge with a scripted HTTP layer — mapping + gates run."""

    def __init__(self, bodies: list[dict]) -> None:
        super().__init__(
            model="typesafe/jev-1.13.0", api_key="test-key",
            rate_limiter=RateLimiter(6000),
        )
        self._bodies = list(bodies)
        self.payloads: list[dict] = []

    def _post(self, payload: dict, *, timeout: int) -> dict:  # noqa: ARG002
        self.payloads.append(payload)
        return self._bodies.pop(0)


def _typesafe_answers(axes: dict[str, str], conf: float = 0.9) -> dict[str, Any]:
    return _answers(axes, conf)


def test_session_applies_gates_for_typesafe_primary(tmp_path) -> None:
    from eval_agent.orchestration.session import Session, SessionConfig

    judge = _ScriptedTypesafe([{
        "answers": {
            "name_ok": {"choice": "yes", "confidence": 0.95},
            "type_ok": {"choice": "yes", "confidence": 0.95},
            "evidence_field": {"choice": "title", "confidence": 0.9},
        },
        "usage": {"input_tokens": 10},
    }])
    cfg = SessionConfig(
        pipeline_output=tmp_path, threshold=0.0, rpm=60, parallel=1,
        judge_model="typesafe/jev-1.13.0", evaluators=["hmo_wikibase_item"],
        api_key="", mode="linear",
    )
    s = Session(
        cfg, judge=judge,
        cache_path=tmp_path / "cache.jsonl", runs_dir=tmp_path / "runs",
        progress_path=tmp_path / "progress.md",
    )
    cand = _candidate("hmo_wikibase_item", _hmo_payload())
    verdict = s._judge_one(HmoWikibaseItemEvaluator(), cand)

    # The client's provisional overall was full; the deterministic artifact
    # gate downgraded it — proving the session ran the gates.
    assert verdict.overall == "partial"
    assert verdict.judge_id == "typesafe/jev-1.13.0"
    assert "deterministic text artifacts" in verdict.reasoning


def test_non_typesafe_verdicts_skip_gates(tmp_path) -> None:
    from eval_agent.client.judge_interface import JudgeResponse
    from eval_agent.orchestration.session import Session, SessionConfig

    class _OpenAICompatStyleJudge:
        id = "moonshotai/Kimi-K2.5"

        def __init__(self) -> None:
            self.calls = 0

        def judge(self, *, prompt, schema, timeout=120, context=None):  # noqa: ANN001
            self.calls += 1
            return JudgeResponse(
                verdict={
                    "name_ok": "yes", "type_ok": "yes", "role_ok": "n/a",
                    "overall": "full", "reasoning": "kimi says full",
                    "suggested_fix": None,
                },
                raw_text=None, error=None, judge_id=self.id,
                meta={"answers": _typesafe_answers({"name_ok": "yes", "type_ok": "yes"}, 0.1)},
            )

    judge = _OpenAICompatStyleJudge()
    cfg = SessionConfig(
        pipeline_output=tmp_path, threshold=0.0, rpm=60, parallel=1,
        judge_model="moonshotai/Kimi-K2.5", evaluators=["person_ner"],
        api_key="", mode="linear",
    )
    s = Session(
        cfg, judge=judge,
        cache_path=tmp_path / "cache.jsonl",
        runs_dir=tmp_path / "runs",
        progress_path=tmp_path / "progress.md",
    )
    verdict = s._judge_one(PersonNERevaluator(), _candidate("person_ner"))
    assert verdict.overall == "full"  # gates never touched the non-typesafe row
    assert judge.calls == 1


def test_gates_response_is_frozen_safe(tmp_path) -> None:
    from eval_agent.orchestration.session import Session, SessionConfig

    judge = _ScriptedTypesafe([{
        "answers": _typesafe_answers({"name_ok": "yes", "type_ok": "yes"}, 0.9),
        "usage": {"input_tokens": 10},
    }])
    cfg = SessionConfig(
        pipeline_output=tmp_path, threshold=0.0, rpm=60, parallel=1,
        judge_model="typesafe/jev-1.13.0", evaluators=["hmo_wikibase_item"],
        api_key="", mode="linear",
    )
    s = Session(
        cfg, judge=judge,
        cache_path=tmp_path / "cache.jsonl",
        runs_dir=tmp_path / "runs",
        progress_path=tmp_path / "progress.md",
    )
    response, _v = s._judge_with_retries(
        HmoWikibaseItemEvaluator(), _candidate("hmo_wikibase_item", _hmo_payload()),
        "p\nReturn only the JSON verdict.",
    )
    assert response.verdict is not None
    assert response.verdict["overall"] == "partial"
    assert replace(response, verdict=response.verdict).verdict == response.verdict
