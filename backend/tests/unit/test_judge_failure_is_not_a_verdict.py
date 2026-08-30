"""Rule W-158 — a judge that did not answer must not persist as a rejection.

One manuscript in run 48ba6c13 was stored as `overall="fail", name_ok="no",
type_ok="no", role_ok="n/a", reasoning=""` — which is exactly the eval-agent
dataclass default set. A transport failure had been persisted as a hard
rejection, the envelope-level `error` that explained it was never read, and the
job snapshot dropped the empty reasoning, so the UI showed a reasonless fail.
"""

from __future__ import annotations

from app.pipeline.ai_verdict_cache_common import (
    JUDGE_FAILURE_OVERALL,
    is_judge_failure,
    normalise_verdict_body,
)
from app.pipeline.verify_outcome import (
    count_judge_failures,
    resolve_verify_session_outcome,
)
from app.pipeline.verify_session_store import _compact_verdict_for_job


class TestNormaliseVerdictBody:
    def test_the_exact_bad_row_from_run_48ba6c13_is_not_a_fail(self) -> None:
        body, error = normalise_verdict_body({
            "error": "no verdict (judge failure)",
            "verdict": {
                "overall": "fail", "name_ok": "no", "type_ok": "no",
                "role_ok": "n/a", "reasoning": "",
            },
        })
        assert body["overall"] == "unknown"
        assert body["name_ok"] == "unknown"
        assert body["type_ok"] == "unknown"
        assert body["judge_failure"] is True
        assert body["verification_error"] == "no verdict (judge failure)"
        assert error == "no verdict (judge failure)"
        assert "NOT an assessment" in body["reasoning"]

    def test_a_blank_reasoning_fail_is_a_judge_failure(self) -> None:
        """The agentic loop cannot send a responseSchema, so this shape is reachable."""
        body, error = normalise_verdict_body({
            "verdict": {"overall": "fail", "name_ok": "no", "reasoning": "   "},
        })
        assert body["overall"] == "unknown"
        assert body["judge_failure"] is True
        assert error == "judge returned no reasoning"

    def test_a_missing_overall_is_a_judge_failure(self) -> None:
        body, error = normalise_verdict_body({"verdict": {"reasoning": "hmm"}})
        assert body["overall"] == "unknown"
        assert body["judge_failure"] is True
        assert error

    def test_a_substantive_verdict_is_passed_through_untouched(self) -> None:
        original = {
            "overall": "full", "name_ok": "yes", "type_ok": "yes",
            "role_ok": "n/a", "reasoning": "labels match MARC 245",
        }
        body, error = normalise_verdict_body({"verdict": original})
        assert body == original
        assert error is None

    def test_a_real_fail_with_a_reason_stays_a_fail(self) -> None:
        body, error = normalise_verdict_body({
            "verdict": {"overall": "fail", "reasoning": "label asserts NLI, MARC says BL"},
        })
        assert body["overall"] == "fail"
        assert error is None
        assert not is_judge_failure(body)

    def test_an_abstain_needs_no_reasoning(self) -> None:
        """`abstain` is a stated non-answer, not a claim about the item."""
        body, error = normalise_verdict_body({"verdict": {"overall": "abstain"}})
        assert body["overall"] == "abstain"
        assert error is None


class TestJobSnapshotKeepsTheReason:
    def test_job_snapshot_keeps_verdict_envelope_metadata(self) -> None:
        row = _compact_verdict_for_job({
            "schema_version": 2,
            "judge_id": "deepseek-ai/DeepSeek-V4-Flash",
            "evaluator_id": "wikidata_item",
            "candidate": {"local_id": "QDraft_MS_1", "model_confidence": 0.91},
            "record_id": "9900001",
            "sub_type": "manuscript",
            "confidence": 0.88,
            "cache_key": "a" * 64,
            "judged_at": "2026-08-29T19:00:00+00:00",
            "verdict": {
                "overall": "full", "name_ok": "yes", "type_ok": "yes",
                "role_ok": "n/a", "reasoning": "all item claims match",
            },
        })

        assert row["judge_id"] == "deepseek-ai/DeepSeek-V4-Flash"
        assert row["evaluator_id"] == "wikidata_item"
        assert row["confidence"] == 0.88
        assert row["cache_key"] == "a" * 64
        assert row["judged_at"] == "2026-08-29T19:00:00+00:00"

    def test_a_judge_failure_gets_a_reason_in_the_job_snapshot(self) -> None:
        row = _compact_verdict_for_job({
            "candidate": {"local_id": "QDraft_MS_1"},
            "verdict": {
                "overall": JUDGE_FAILURE_OVERALL, "name_ok": "unknown",
                "reasoning": "",
            },
            "error": "no verdict (judge failure)",
        })
        assert row["verdict"]["overall"] == "unknown"
        assert row["verdict"]["judge_failure"] is True
        assert row["verdict"]["error"] == "no verdict (judge failure)"
        assert "Judge failure" in row["verdict"]["reasoning"]

    def test_a_normal_verdict_keeps_its_own_reasoning(self) -> None:
        row = _compact_verdict_for_job({
            "candidate": {"local_id": "QDraft_MS_1"},
            "verdict": {"overall": "full", "reasoning": "all channels agree"},
        })
        assert row["verdict"]["reasoning"] == "all channels agree"
        assert "error" not in row["verdict"]


class TestRunOutcome:
    def test_a_run_with_judge_failures_reports_partial(self) -> None:
        outcome = resolve_verify_session_outcome(
            uncached_count=2, fresh_verdict_count=2, scope_size=2, cache_hits=0,
            judge_failure_count=1,
        )
        assert outcome == "partial"

    def test_unknown_judge_failure_is_counted_as_a_judge_failure(self) -> None:
        assert count_judge_failures([
            {"verdict": {"overall": "unknown", "judge_failure": True}},
            {"verdict": {"overall": "unknown"}},
        ]) == 1

    def test_a_clean_run_still_reports_complete(self) -> None:
        outcome = resolve_verify_session_outcome(
            uncached_count=2, fresh_verdict_count=2, scope_size=2, cache_hits=0,
        )
        assert outcome == "complete"

    def test_judge_failures_are_counted_from_the_body_or_the_envelope(self) -> None:
        assert count_judge_failures([
            {"verdict": {"overall": JUDGE_FAILURE_OVERALL}},
            {"verdict": {"overall": "full"}, "error": "transport"},
            {"verdict": {"overall": "full"}},
        ]) == 2
