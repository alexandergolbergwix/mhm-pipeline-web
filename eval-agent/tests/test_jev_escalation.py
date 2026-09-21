"""Jev-primary escalation policy (LLM exception handler) — step 5/8 tests.

High-confidence `full` rows stay with Jev; everything else (partial / fail /
low-confidence full) is re-judged by the fallback LLM, whose verdict — and
judge_id — wins for the row.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from eval_agent.client.judge_interface import JudgeResponse
from eval_agent.client.rate_limiter import RateLimiter
from eval_agent.client.typesafe_client import TypesafeJudge
from eval_agent.evaluators._base import Candidate
from eval_agent.evaluators.person_ner import PersonNERevaluator
from eval_agent.orchestration.session import Session, SessionConfig

TYPESAFE_ID = "typesafe/jev-1.13.0"
FALLBACK_ID = "moonshotai/Kimi-K2.5"


def _answers(axes: dict[str, str], conf: float) -> dict[str, Any]:
    out = {
        "name_ok": {"choice": axes.get("name_ok", "yes"), "confidence": conf},
        "type_ok": {"choice": axes.get("type_ok", "yes"), "confidence": conf},
    }
    if "role_ok" in axes:
        out["role_ok"] = {"choice": axes["role_ok"], "confidence": conf}
    return out


def _body(axes: dict[str, str], conf: float) -> dict:
    return {
        "answers": {
            **_answers(axes, conf),
            "evidence_field": {"choice": "notes", "confidence": conf},
        },
        "usage": {"input_tokens": 10},
    }


class _ScriptedTypesafe(TypesafeJudge):
    """Real TypesafeJudge with a scripted HTTP layer."""

    def __init__(self, bodies: list[dict]) -> None:
        super().__init__(
            model=TYPESAFE_ID, api_key="test-key",
            rate_limiter=RateLimiter(6000),
        )
        self._bodies = list(bodies)
        self.calls = 0

    def _post(self, payload: dict, *, timeout: int) -> dict:  # noqa: ARG002
        self.calls += 1
        return self._bodies.pop(0)


class _FallbackJudge:
    id = FALLBACK_ID

    def __init__(self, overall: str = "full") -> None:
        self.calls = 0
        self._overall = overall

    def judge(self, *, prompt, schema, timeout=120, context=None):  # noqa: ANN001, ARG001
        self.calls += 1
        return JudgeResponse(
            verdict={
                "name_ok": "yes", "type_ok": "yes", "role_ok": "yes",
                "overall": self._overall,
                "reasoning": "fallback re-judged this row",
                "suggested_fix": None,
            },
            raw_text=None, error=None, judge_id=self.id,
        )


def _session(tmp_path: Path, judge: TypesafeJudge, *, escalate: bool = True) -> Session:
    cfg = SessionConfig(
        pipeline_output=tmp_path, threshold=0.0, rpm=60, parallel=1,
        judge_model=TYPESAFE_ID, evaluators=["person_ner"], api_key="",
        mode="linear",
        escalate_policy=escalate, escalate_below_conf=0.85,
        fallback_model=FALLBACK_ID,
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


def _wire_fallback(s: Session, fallback: _FallbackJudge) -> None:
    s.config.fallback_model = fallback.id
    s._fallback_judge = fallback
    s._fallback_attempted = True


def test_high_confidence_full_stays_with_jev(tmp_path: Path) -> None:
    judge = _ScriptedTypesafe([_body({"name_ok": "yes", "type_ok": "yes", "role_ok": "yes"}, 0.95)])
    fallback = _FallbackJudge()
    s = _session(tmp_path, judge)
    _wire_fallback(s, fallback)

    verdict = s._judge_one(PersonNERevaluator(), _candidate())

    assert verdict.overall == "full"
    assert verdict.judge_id == TYPESAFE_ID
    assert fallback.calls == 0
    assert judge.calls == 1


def test_partial_row_escalates_to_fallback(tmp_path: Path) -> None:
    judge = _ScriptedTypesafe([_body({"name_ok": "partial", "type_ok": "yes"}, 0.95)])
    fallback = _FallbackJudge(overall="full")
    s = _session(tmp_path, judge)
    _wire_fallback(s, fallback)

    verdict = s._judge_one(PersonNERevaluator(), _candidate())

    # The fallback's verdict — and its judge_id — win for the row.
    assert verdict.overall == "full"
    assert verdict.judge_id == FALLBACK_ID
    assert fallback.calls == 1


def test_low_confidence_full_escalates(tmp_path: Path) -> None:
    judge = _ScriptedTypesafe([_body({"name_ok": "yes", "type_ok": "yes"}, 0.5)])
    fallback = _FallbackJudge()
    s = _session(tmp_path, judge)
    _wire_fallback(s, fallback)

    verdict = s._judge_one(PersonNERevaluator(), _candidate())

    assert verdict.judge_id == FALLBACK_ID
    assert fallback.calls == 1


def test_policy_off_never_escalates(tmp_path: Path) -> None:
    judge = _ScriptedTypesafe([_body({"name_ok": "yes", "type_ok": "yes"}, 0.5)])
    fallback = _FallbackJudge()
    s = _session(tmp_path, judge, escalate=False)
    _wire_fallback(s, fallback)

    verdict = s._judge_one(PersonNERevaluator(), _candidate())

    assert verdict.judge_id == TYPESAFE_ID
    assert fallback.calls == 0


def test_typesafe_error_row_falls_back(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EVAL_AGENT_JUDGE_RETRY", "0")
    judge = _ScriptedTypesafe([{"answers": {}, "usage": {}}])  # missing axes → row error
    fallback = _FallbackJudge()
    s = _session(tmp_path, judge)
    _wire_fallback(s, fallback)

    verdict = s._judge_one(PersonNERevaluator(), _candidate())

    assert verdict.judge_id == FALLBACK_ID
    assert verdict.overall == "full"


def test_escalation_without_fallback_configured_keeps_jev(tmp_path: Path) -> None:
    judge = _ScriptedTypesafe([_body({"name_ok": "partial", "type_ok": "yes"}, 0.95)])
    s = _session(tmp_path, judge)
    s.config.fallback_model = ""  # no fallback wired

    verdict = s._judge_one(PersonNERevaluator(), _candidate())

    assert verdict.overall == "partial"
    assert verdict.judge_id == TYPESAFE_ID


def test_cached_non_full_row_escalates(tmp_path: Path) -> None:
    judge = _ScriptedTypesafe([])  # no fresh calls expected — cache hit
    fallback = _FallbackJudge()
    s = _session(tmp_path, judge)
    _wire_fallback(s, fallback)

    prompt = PersonNERevaluator().build_prompt(_candidate())
    cache_id = f"{TYPESAFE_ID}::linear"
    s._cache.append(
        judge_id=cache_id, prompt=prompt,
        verdict={
            "name_ok": "yes", "type_ok": "yes", "role_ok": "n/a",
            "overall": "partial", "reasoning": "cached jev partial",
            "suggested_fix": None,
        },
    )

    verdict = s._judge_one(PersonNERevaluator(), _candidate())

    assert verdict.judge_id == FALLBACK_ID
    assert verdict.overall == "full"
    assert judge.calls == 0  # served from the fallback's own cache/judge


def test_cached_full_row_stays_with_jev(tmp_path: Path) -> None:
    judge = _ScriptedTypesafe([])
    fallback = _FallbackJudge()
    s = _session(tmp_path, judge)
    _wire_fallback(s, fallback)

    prompt = PersonNERevaluator().build_prompt(_candidate())
    s._cache.append(
        judge_id=f"{TYPESAFE_ID}::linear", prompt=prompt,
        verdict={
            "name_ok": "yes", "type_ok": "yes", "role_ok": "n/a",
            "overall": "full", "reasoning": "cached jev full",
            "suggested_fix": None,
        },
    )

    verdict = s._judge_one(PersonNERevaluator(), _candidate())

    assert verdict.overall == "full"
    assert verdict.judge_id == TYPESAFE_ID
    assert fallback.calls == 0


def test_non_typesafe_primary_never_escalates(tmp_path: Path) -> None:
    from eval_agent.client.openai_compat_client import OpenAICompatJudge

    class _ScriptedKimi(OpenAICompatJudge):
        def __init__(self) -> None:
            super().__init__(
                model=FALLBACK_ID, api_key="k",
                base_url="https://example.invalid/v1",
                rate_limiter=RateLimiter(6000),
            )
            self.calls = 0

        def _post(self, payload, *, timeout):  # noqa: ANN001, ARG002
            self.calls += 1
            return {
                "choices": [{"message": {"content": '{"name_ok": "partial", '
                            '"type_ok": "yes", "role_ok": "n/a", '
                            '"overall": "partial", "reasoning": "kimi", '
                            '"suggested_fix": null}'}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 5},
            }

    judge = _ScriptedKimi()
    s = _session(tmp_path, judge)
    s.config.escalate_policy = True

    verdict = s._judge_one(PersonNERevaluator(), _candidate())

    # Escalation policy is typesafe-only: a Kimi primary never re-judges.
    assert verdict.overall == "partial"
    assert verdict.judge_id == FALLBACK_ID
    assert judge.calls == 1
