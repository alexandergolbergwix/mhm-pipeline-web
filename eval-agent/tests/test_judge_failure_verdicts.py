"""Rule W-158 — a judge that did not answer must not look like a rejection.

The `Verdict` dataclass defaults are `fail / no / no / n-a / ""` because that is
the safe pre-parse construction shape. `parse_verdict(None)` used to return them
unchanged, so a transport error, a parse error and a budget exhaustion all
persisted as a substantive hard `fail` with no reasoning — which is exactly the
row that shipped for one manuscript in run 48ba6c13.
"""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from eval_agent.evaluators import build
from eval_agent.evaluators._base import Candidate

SCHEMA_DIR = Path(__file__).resolve().parents[1] / "config" / "schemas"


def _schema() -> dict:
    return json.loads((SCHEMA_DIR / "verdict.v2.json").read_text(encoding="utf-8"))


def _candidate() -> Candidate:
    return Candidate(
        record_id="990000403370205171",
        evaluator_id="person_ner",
        sub_type="PERSON",
        payload={"text": "אברהם", "type": "PERSON"},
        confidence=0.87,
        marc_context={},
    )


@pytest.fixture
def evaluator():
    return build("person_ner")


class TestParseVerdictFailures:
    def test_provider_failure_uses_abstain_as_the_public_verdict(self, evaluator) -> None:
        v = evaluator.parse_verdict(None, _candidate())
        assert v.overall == "abstain"
        assert v.verification_status == "provider_error"

    def test_no_raw_verdict_is_abstain_not_fail(self, evaluator) -> None:
        v = evaluator.parse_verdict(None, _candidate())
        assert v.overall == "abstain"
        assert v.verification_status == "provider_error"
        assert v.error == "no verdict (judge failure)"

    def test_no_raw_verdict_does_not_assert_the_axes(self, evaluator) -> None:
        """`no` is a claim about the item; `unknown` is the truth here."""
        v = evaluator.parse_verdict(None, _candidate())
        assert v.name_ok == "unknown"
        assert v.type_ok == "unknown"

    def test_no_raw_verdict_says_it_is_not_an_assessment(self, evaluator) -> None:
        v = evaluator.parse_verdict(None, _candidate())
        assert "NOT an assessment" in v.reasoning

    def test_a_blank_reasoning_verdict_is_marked_as_a_judge_failure(
        self, evaluator,
    ) -> None:
        v = evaluator.parse_verdict(
            {"overall": "fail", "name_ok": "no", "type_ok": "no", "reasoning": ""},
            _candidate(),
        )
        assert v.overall == "abstain"
        assert v.error == "verdict missing reasoning"

    def test_a_substantive_verdict_is_untouched(self, evaluator) -> None:
        v = evaluator.parse_verdict(
            {
                "overall": "fail", "name_ok": "no", "type_ok": "yes",
                "role_ok": "n/a", "reasoning": "not in the colophon",
            },
            _candidate(),
        )
        assert v.overall == "fail"
        assert v.name_ok == "no"
        assert v.error is None


class TestSchemaRequiresAReason:
    def _record(self, verdict: dict, **extra) -> dict:
        return {
            "schema_version": 2,
            "judge_id": "gemini-3.5-flash",
            "record_id": "990000403370205171",
            "evaluator_id": "person_ner",
            "sub_type": "PERSON",
            "candidate": {"text": "אברהם", "type": "PERSON"},
            "verdict": {"suggested_fix": None, **verdict},
            "judged_at": "2026-08-05T00:00:00Z",
            "cache_key": "a" * 64,
            **extra,
        }

    def test_a_substantive_verdict_without_a_reason_is_invalid(self) -> None:
        record = self._record({
            "name_ok": "no", "type_ok": "no", "role_ok": "n/a",
            "overall": "fail", "reasoning": "",
        })
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(record, _schema())

    def test_a_substantive_verdict_with_a_reason_is_valid(self) -> None:
        record = self._record({
            "name_ok": "no", "type_ok": "no", "role_ok": "n/a",
            "overall": "fail", "reasoning": "not in the colophon",
        })
        jsonschema.validate(record, _schema())

    def test_provider_error_uses_abstain_and_status(self) -> None:
        record = self._record({
            "name_ok": "unknown", "type_ok": "unknown", "role_ok": "n/a",
            "overall": "abstain", "reasoning": "Judge failure: …",
        }, verification_status="provider_error")
        jsonschema.validate(record, _schema())

    def test_legacy_verification_failed_is_not_a_public_schema_value(self) -> None:
        record = self._record({
            "name_ok": "unknown", "type_ok": "unknown", "role_ok": "n/a",
            "overall": "verification_failed", "reasoning": "Judge failure: …",
        }, error="no verdict (judge failure)")
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(record, _schema())


class TestResponseSchemaStaysTight:
    def test_the_judge_is_never_offered_verification_failed(self) -> None:
        """A model must not be able to declare its own check failed."""
        from eval_agent.orchestration.session import _load_schema

        enum = _load_schema()["properties"]["overall"]["enum"]
        assert "verification_failed" not in enum
        assert set(enum) == {"full", "partial", "fail"}

    def test_the_gemini_sanitizer_strips_the_conditionals(self) -> None:
        from eval_agent.client.gemini_client import _sanitize_schema_for_gemini

        sanitized = _sanitize_schema_for_gemini(_schema())
        assert "allOf" not in sanitized
        assert "if" not in sanitized
