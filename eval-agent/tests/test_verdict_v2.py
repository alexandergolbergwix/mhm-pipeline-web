"""TDD tests for verdict schema v2 (suggested_fix extension).

Pins the following contracts:

1.  Schema v2 validates a verdict with ``suggested_fix: null``.
2.  Schema v2 validates a verdict with a valid high-confidence fix object.
3.  Schema v2 rejects a fix with confidence other than "high".
4.  Schema v2 rejects a fix with an empty text field.
5.  Parser drops fix objects with confidence != "high".
6.  Parser drops fix objects where text equals the original (after strip).
7.  Parser drops fix objects that are not dicts.
8.  Parser drops fix with unreasonably long text (> 512 chars).
9.  Verdict.to_jsonl_record() includes suggested_fix when present.
10. Verdict.to_jsonl_record() includes suggested_fix: null when absent.
11. GenreClassifier parse_verdict always returns suggested_fix=None
    (genre is a label, not extracted text).
12. PersonNER parse_verdict passes through a valid fix from Gemini.
13. ProvenanceNER parse_verdict drops fix when candidate has no
    MARC context (guardrail against missing-context proposals).
14. ContentsNER parse_verdict drops fix when candidate has no
    MARC context.
"""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from eval_agent.evaluators._base import Candidate, Verdict

# ── Helpers ──────────────────────────────────────────────────────────

SCHEMA_DIR = Path(__file__).resolve().parents[1] / "config" / "schemas"


def load_schema(name: str) -> dict:
    return json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))


def _make_valid_v2_record(
    *,
    suggested_fix: dict | None = None,
    evaluator_id: str = "person_ner",
) -> dict:
    return {
        "schema_version": 2,
        "judge_id":       "gemini-3.1-pro-preview",
        "record_id":      "990000403370205171",
        "evaluator_id":   evaluator_id,
        "sub_type":       "PERSON",
        "candidate": {
            "text": "אברהם",
            "type": "PERSON",
        },
        "verdict": {
            "name_ok":      "yes",
            "type_ok":      "yes",
            "role_ok":      "yes",
            "overall":      "full",
            "reasoning":    "Exact match in colophon_text.",
            "suggested_fix": suggested_fix,
        },
        "judged_at":  "2026-06-07T00:00:00Z",
        "cache_key":  "a" * 64,
    }


def _make_candidate(
    text: str = "אברהם",
    marc_context: dict | None = None,
    grounded: bool | None = None,
    exists_in: list | None = None,
    evaluator_id: str = "person_ner",
) -> Candidate:
    return Candidate(
        record_id="990000403370205171",
        evaluator_id=evaluator_id,
        sub_type="PERSON",
        payload={"text": text, "type": "PERSON"},
        confidence=0.87,
        marc_context=marc_context or {},
        grounded=grounded,
        exists_in=exists_in or [],
    )


# ── 1-4. Schema validation ────────────────────────────────────────────

class TestSchemaV2Validation:
    def _schema(self) -> dict:
        return load_schema("verdict.v2.json")

    def test_null_fix_is_valid(self):
        record = _make_valid_v2_record(suggested_fix=None)
        # Should not raise.
        jsonschema.validate(instance=record, schema=self._schema())

    def test_valid_high_confidence_fix_is_accepted(self):
        fix = {
            "text":         "דף 124א-133ב",
            "reasoning":    "Trailing noise '(בשוליים' stripped; clean folio range visible in contents.",
            "source_field": "contents",
            "confidence":   "high",
        }
        record = _make_valid_v2_record(suggested_fix=fix)
        jsonschema.validate(instance=record, schema=self._schema())

    def test_medium_confidence_is_rejected(self):
        fix = {
            "text":       "דף 124א-133ב",
            "confidence": "medium",
        }
        record = _make_valid_v2_record(suggested_fix=fix)
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(instance=record, schema=self._schema())

    def test_empty_text_is_rejected(self):
        fix = {
            "text":       "",
            "confidence": "high",
        }
        record = _make_valid_v2_record(suggested_fix=fix)
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(instance=record, schema=self._schema())


# ── 5-8. Python parser defensive validation ──────────────────────────

class TestParseVerdictSuggestedFix:
    """Tests for Evaluator.parse_verdict() handling of suggested_fix.

    These all use PersonNERevaluator since it is the canonical NER
    evaluator that supports fixes. The base parse_verdict is shared.
    """

    def _evaluator(self):
        from eval_agent.evaluators.person_ner import PersonNERevaluator
        return PersonNERevaluator()

    def _raw(self, fix_override=...) -> dict:
        """Build a minimal valid raw Gemini response dict."""
        raw = {
            "name_ok":   "yes",
            "type_ok":   "yes",
            "role_ok":   "yes",
            "overall":   "full",
            "reasoning": "Clear match.",
        }
        if fix_override is not ...:
            raw["suggested_fix"] = fix_override
        return raw

    def test_valid_fix_is_parsed(self):
        ev = self._evaluator()
        candidate = _make_candidate(
            text="אברהם בן",
            marc_context={"colophon_text": "אברהם בן יוסף מעתיק"},
        )
        raw = self._raw({"text": "אברהם בן יוסף", "confidence": "high", "reasoning": "patronymic present in colophon"})
        verdict = ev.parse_verdict(raw, candidate)
        assert verdict.suggested_fix is not None
        assert verdict.suggested_fix.text == "אברהם בן יוסף"
        assert verdict.suggested_fix.confidence == "high"

    def test_medium_confidence_fix_is_dropped(self):
        ev = self._evaluator()
        candidate = _make_candidate(marc_context={"colophon_text": "test"})
        raw = self._raw({"text": "something else", "confidence": "medium"})
        verdict = ev.parse_verdict(raw, candidate)
        assert verdict.suggested_fix is None

    def test_fix_equal_to_original_text_is_dropped(self):
        ev = self._evaluator()
        candidate = _make_candidate(text="אברהם", marc_context={"colophon_text": "test"})
        raw = self._raw({"text": "  אברהם  ", "confidence": "high"})  # same after strip
        verdict = ev.parse_verdict(raw, candidate)
        assert verdict.suggested_fix is None

    def test_non_dict_fix_is_dropped(self):
        ev = self._evaluator()
        candidate = _make_candidate(marc_context={"colophon_text": "test"})
        raw = self._raw("this should be an object not a string")
        verdict = ev.parse_verdict(raw, candidate)
        assert verdict.suggested_fix is None

    def test_fix_with_excessively_long_text_is_dropped(self):
        ev = self._evaluator()
        candidate = _make_candidate(marc_context={"colophon_text": "test"})
        raw = self._raw({"text": "א" * 513, "confidence": "high"})
        verdict = ev.parse_verdict(raw, candidate)
        assert verdict.suggested_fix is None

    def test_fix_absent_in_raw_gives_none(self):
        ev = self._evaluator()
        candidate = _make_candidate(marc_context={"colophon_text": "test"})
        raw = self._raw()  # no suggested_fix key
        verdict = ev.parse_verdict(raw, candidate)
        assert verdict.suggested_fix is None


# ── 9-10. JSONL serialisation ────────────────────────────────────────

class TestVerdictToJsonlRecord:
    def _verdict(self, **kw) -> Verdict:
        return Verdict(
            record_id="990000403370205171",
            evaluator_id="person_ner",
            sub_type="PERSON",
            candidate_payload={"text": "אברהם"},
            confidence=0.87,
            **kw,
        )

    def test_fix_present_is_serialised(self):
        from eval_agent.evaluators._base import SuggestedFix
        v = self._verdict()
        v.suggested_fix = SuggestedFix(
            text="אברהם בן יוסף",
            reasoning="patronymic in colophon",
            source_field="colophon_text",
            confidence="high",
        )
        record = v.to_jsonl_record()
        fix = record["verdict"]["suggested_fix"]
        assert fix is not None
        assert fix["text"] == "אברהם בן יוסף"
        assert fix["confidence"] == "high"

    def test_fix_absent_serialises_as_null(self):
        v = self._verdict()
        record = v.to_jsonl_record()
        # suggested_fix key must be present and null.
        assert "suggested_fix" in record["verdict"]
        assert record["verdict"]["suggested_fix"] is None


# ── 11. Genre classifier never proposes fixes ─────────────────────────

class TestGenreClassifierNeverFixes:
    def test_parse_verdict_always_returns_no_fix(self):
        from eval_agent.evaluators.genre_classifier import GenreClassifierEvaluator
        ev = GenreClassifierEvaluator()
        candidate = Candidate(
            record_id="rec1",
            evaluator_id="genre_classifier",
            sub_type="Piyyutim",
            payload={"label": "Piyyutim", "confidence": 0.9},
            confidence=0.9,
            marc_context={"genres": "Piyyutim"},
        )
        raw = {
            "name_ok": "yes", "type_ok": "yes", "role_ok": "n/a",
            "overall": "full", "reasoning": "MARC 655 matches.",
            "suggested_fix": {"text": "something", "confidence": "high"},
        }
        verdict = ev.parse_verdict(raw, candidate)
        assert verdict.suggested_fix is None


# ── 12. PersonNER passes fix through ─────────────────────────────────

class TestPersonNERFixPassThrough:
    def test_valid_fix_flows_to_verdict(self):
        from eval_agent.evaluators.person_ner import PersonNERevaluator
        ev = PersonNERevaluator()
        candidate = _make_candidate(
            text="יוסף",
            marc_context={"colophon_text": "יוסף בן יעקב מעתיק"},
            grounded=True,
        )
        raw = {
            "name_ok":   "partial",
            "type_ok":   "yes",
            "role_ok":   "yes",
            "overall":   "partial",
            "reasoning": "Name is truncated; patronymic present in colophon.",
            "suggested_fix": {
                "text":         "יוסף בן יעקב",
                "reasoning":    "Full name visible in colophon_text.",
                "source_field": "colophon_text",
                "confidence":   "high",
            },
        }
        verdict = ev.parse_verdict(raw, candidate)
        assert verdict.suggested_fix is not None
        assert verdict.suggested_fix.text == "יוסף בן יעקב"


# ── 13-14. Provenance/Contents drop fix when no MARC context ─────────

class TestMissingContextDropsFix:
    """Guardrail: no MARC context → no fix allowed (plan step 1)."""

    def test_provenance_drops_fix_without_marc_context(self):
        from eval_agent.evaluators.provenance_ner import ProvenanceNERevaluator
        ev = ProvenanceNERevaluator()
        candidate = Candidate(
            record_id="rec1",
            evaluator_id="provenance_ner",
            sub_type="OWNER",
            payload={"text": "שמעון", "type": "OWNER"},
            confidence=0.7,
            marc_context={},     # ← no context
            grounded=None,
            exists_in=[],
        )
        raw = {
            "name_ok":   "partial",
            "type_ok":   "yes",
            "role_ok":   "n/a",
            "overall":   "partial",
            "reasoning": "Truncated.",
            "suggested_fix": {
                "text":       "שמעון בר יהודה",
                "confidence": "high",
            },
        }
        verdict = ev.parse_verdict(raw, candidate)
        assert verdict.suggested_fix is None

    def test_contents_drops_fix_without_marc_context(self):
        from eval_agent.evaluators.contents_ner import ContentsNERevaluator
        ev = ContentsNERevaluator()
        candidate = Candidate(
            record_id="rec1",
            evaluator_id="contents_ner",
            sub_type="FOLIO",
            payload={"text": "12א", "type": "FOLIO"},
            confidence=0.8,
            marc_context={},     # ← no context
            grounded=None,
            exists_in=[],
        )
        raw = {
            "name_ok":   "partial",
            "type_ok":   "yes",
            "role_ok":   "n/a",
            "overall":   "partial",
            "reasoning": "Folio range truncated.",
            "suggested_fix": {
                "text":       "12א-18ב",
                "confidence": "high",
            },
        }
        verdict = ev.parse_verdict(raw, candidate)
        assert verdict.suggested_fix is None

    def test_provenance_accepts_fix_with_marc_context(self):
        """Fix is allowed when MARC context is present."""
        from eval_agent.evaluators.provenance_ner import ProvenanceNERevaluator
        ev = ProvenanceNERevaluator()
        candidate = Candidate(
            record_id="rec1",
            evaluator_id="provenance_ner",
            sub_type="OWNER",
            payload={"text": "שמעון", "type": "OWNER"},
            confidence=0.7,
            marc_context={"provenance": "שמעון בר יהודה בעלים"},
            grounded=True,
            exists_in=[{"field": "provenance", "match_type": "full"}],
        )
        raw = {
            "name_ok":   "partial",
            "type_ok":   "yes",
            "role_ok":   "n/a",
            "overall":   "partial",
            "reasoning": "Truncated; patronymic in provenance.",
            "suggested_fix": {
                "text":         "שמעון בר יהודה",
                "reasoning":    "Full name in provenance field.",
                "source_field": "provenance",
                "confidence":   "high",
            },
        }
        verdict = ev.parse_verdict(raw, candidate)
        assert verdict.suggested_fix is not None
        assert verdict.suggested_fix.text == "שמעון בר יהודה"


# ── 15. Gemini responseSchema sanitisation of the v2 verdict slice ────

class TestGeminiSchemaSanitisation:
    """The v2 verdict slice carries a ``oneOf`` + ``type: [..,"null"]`` that
    Gemini's ``responseSchema`` subset rejects. ``_sanitize_schema_for_gemini``
    must rewrite both into ``nullable: true`` so wiring v2 into the judge does
    not 400 every call."""

    def _sanitised(self) -> dict:
        from eval_agent.orchestration.session import _load_schema
        from eval_agent.client.gemini_client import _sanitize_schema_for_gemini
        return _sanitize_schema_for_gemini(_load_schema())

    def _walk_keys(self, node):
        if isinstance(node, dict):
            for k, v in node.items():
                yield k, v
                yield from self._walk_keys(v)
        elif isinstance(node, list):
            for v in node:
                yield from self._walk_keys(v)

    def test_no_combiners_or_list_types_survive(self):
        from eval_agent.client.gemini_client import _GEMINI_UNSUPPORTED_KEYS
        sanitised = self._sanitised()
        for k, v in self._walk_keys(sanitised):
            assert k not in ("oneOf", "anyOf"), f"combiner {k} survived"
            assert k not in _GEMINI_UNSUPPORTED_KEYS, f"unsupported {k} survived"
            if k == "type":
                assert not isinstance(v, list), f"list type {v} survived"

    def test_suggested_fix_is_nullable_object(self):
        fix = self._sanitised()["properties"]["suggested_fix"]
        assert fix["type"] == "object"
        assert fix["nullable"] is True
        # inner optional fields keep nullability too
        assert fix["properties"]["reasoning"]["nullable"] is True
        assert fix["properties"]["confidence"]["enum"] == ["high"]

    def test_suggested_fix_stays_required_so_key_is_always_emitted(self):
        # required + nullable ⇒ Gemini emits the key, value null by default.
        assert "suggested_fix" in self._sanitised().get("required", [])


# ── 16. verify.py cache validation under v2 ──────────────────────────

class TestRunVerifyUnderV2:
    """``run_verify`` must accept cache rows carrying suggested_fix (null or
    a valid high-confidence object) and reject a medium-confidence fix."""

    def _run(self, tmp_path, rows):
        from eval_agent.orchestration.verify import run_verify
        cache = tmp_path / "verdict_cache.jsonl"
        cache.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
            encoding="utf-8",
        )
        return run_verify(cache_path=cache, schemas_dir=SCHEMA_DIR)

    def _row(self, fix):
        return {
            "judge_id": "gemini-3.1-pro-preview",
            "verdict": {
                "name_ok": "partial", "type_ok": "yes", "role_ok": "yes",
                "overall": "partial", "reasoning": "trunc", "suggested_fix": fix,
            },
        }

    def test_null_and_valid_fix_rows_pass(self, tmp_path):
        valid = {"text": "יוסף בן יעקב", "reasoning": "colophon",
                 "source_field": "colophon_text", "confidence": "high"}
        report = self._run(tmp_path, [self._row(None), self._row(valid)])
        assert report.passed, report.failures
        assert report.cache_rows_checked == 2

    def test_medium_confidence_fix_row_fails(self, tmp_path):
        report = self._run(tmp_path, [self._row({"text": "x", "confidence": "medium"})])
        assert not report.passed
        assert any("suggested_fix" in f for f in report.failures)
