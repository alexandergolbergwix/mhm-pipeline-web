"""Gated-mode tier-2-failure → tier-1-with-more-context retry.

When the agentic (tier-2) judge errors out (transport / parse / budget),
``_judge_one`` retries the cheap tier-1 judge with an EXPANDED CONTEXT
block — the full MARC record (beyond the narrow per-evaluator projection)
plus the record's other NER entities — instead of returning the failed
verdict. If the enriched retry also fails, the verdict is marked
``verification_failed``.
"""
from __future__ import annotations

from pathlib import Path

from eval_agent.client.judge_interface import JudgeResponse
from eval_agent.evaluators._base import Candidate
from eval_agent.evaluators.person_ner import PersonNERevaluator
from eval_agent.orchestration.session import Session, SessionConfig


class _StubTrace:
    evaluator_id = "person_ner"

    def to_dict(self) -> dict:
        return {"evaluator_id": self.evaluator_id}


class _FailingAgent:
    """Tier-2 that always errors out."""

    def run(self, evaluator, candidate, token_sink=None):  # noqa: ANN001
        v = evaluator.parse_verdict(None, candidate)
        v.error = "tier2 transport error"
        return v, _StubTrace()


def _resp(overall: str, *, error: str | None = None, verdict_present: bool = True) -> JudgeResponse:
    verdict = None
    if verdict_present and error is None:
        verdict = {
            "name_ok": "partial" if overall == "abstain" else "yes",
            "type_ok": "yes",
            "role_ok": "partial" if overall == "abstain" else "yes",
            "overall": overall,
            "reasoning": "scripted",
            "suggested_fix": None,
        }
    return JudgeResponse(
        verdict=verdict, raw_text=None, error=error, judge_id="gemini-test",
        input_tokens=10, output_tokens=5,
    )


class _ScriptedJudge:
    """First tier-1 pass → abstain; the enriched retry → full."""

    id = "gemini-test"

    def __init__(self, retry_response: JudgeResponse) -> None:
        self.prompts: list[str] = []
        self._retry_response = retry_response

    def judge(self, *, prompt: str, schema, timeout: int = 120) -> JudgeResponse:  # noqa: ANN001
        self.prompts.append(prompt)
        if "EXPANDED CONTEXT" in prompt:
            return self._retry_response
        return _resp("abstain")


def _session(tmp_path: Path, judge) -> Session:  # noqa: ANN001
    cfg = SessionConfig(
        pipeline_output=tmp_path, threshold=0.0, rpm=60, parallel=1,
        judge_model="gemini-test", evaluators=["person_ner"], api_key="",
        mode="gated",
    )
    return Session(
        cfg, judge=judge,
        cache_path=tmp_path / "cache.jsonl",
        runs_dir=tmp_path / "runs",
        progress_path=tmp_path / "progress.md",
    )


def _candidate() -> Candidate:
    return Candidate(
        record_id="rec1", evaluator_id="person_ner", sub_type="AUTHOR",
        payload={"person": "אברהם", "role": "AUTHOR"}, confidence=0.9,
        marc_context={"authors": "אברהם"},
    )


def test_tier2_failure_retries_tier1_with_expanded_context(tmp_path: Path) -> None:
    judge = _ScriptedJudge(_resp("full"))
    s = _session(tmp_path, judge)
    s._agent = _FailingAgent()
    s._marc_index = {
        "rec1": {
            "authors": [{"name": "אברהם בן יצחק"}],
            "notes": ["src.mrc", "owned by ..."],
            "colophon_text": "אני אברהם בן יצחק",
        }
    }
    s._ner_index = {
        "rec1": {
            "entities": [
                {"person": "אברהם", "role": "AUTHOR", "source": "person_ner", "confidence": 0.9},
                {"text": "ויניציאה", "type": "PLACE", "source": "provenance_ner", "confidence": 0.7},
            ]
        }
    }

    verdict = s._judge_one(PersonNERevaluator(), _candidate())

    # The enriched retry verdict is returned, not the failed tier-2 one.
    assert verdict.overall == "full"
    assert verdict.error is None
    # Exactly two judge calls: initial tier-1 + enriched retry.
    assert len(judge.prompts) == 2
    enriched = judge.prompts[1]
    assert "EXPANDED CONTEXT" in enriched
    assert "אברהם בן יצחק" in enriched   # full MARC record was injected
    assert "ויניציאה" in enriched         # sibling NER entities were injected


def test_retry_also_failing_marks_verification_failed(tmp_path: Path) -> None:
    judge = _ScriptedJudge(_resp("full", error="retry transport error", verdict_present=False))
    s = _session(tmp_path, judge)
    s._agent = _FailingAgent()
    s._marc_index = {"rec1": {"authors": [{"name": "אברהם"}]}}
    s._ner_index = {}

    verdict = s._judge_one(PersonNERevaluator(), _candidate())

    assert verdict.overall == "verification_failed"
    assert len(judge.prompts) == 2
