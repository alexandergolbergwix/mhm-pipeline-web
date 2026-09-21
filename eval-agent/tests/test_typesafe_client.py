"""Tests for the TypeSafe Jev judge client (Jev-primary rollout step 2/8)."""

from __future__ import annotations

import io
import json
import urllib.error
from unittest.mock import patch

from eval_agent.client.rate_limiter import RateLimiter
from eval_agent.client.typesafe_client import TYPESAFE_URL, TypesafeJudge
from eval_agent.client.typesafe_questions import JEV_CLOSE


def _client(**kwargs) -> TypesafeJudge:
    return TypesafeJudge(
        model="typesafe/jev-1.13.0",
        api_key="test-key",
        rate_limiter=RateLimiter(6000),
        **kwargs,
    )


_BASE_ANSWERS = {
    "name_ok": {"choice": "yes", "confidence": 0.9},
    "type_ok": {"choice": "yes", "confidence": 0.8},
    "evidence_field": {"choice": "colophon_text", "confidence": 0.7},
}
_PERSON_NER_QUESTIONS = {"name_ok", "type_ok", "role_ok", "evidence_field", "match_kind"}


class _FakeResponse(io.BytesIO):
    def __enter__(self):  # noqa: ANN202
        return self

    def __exit__(self, *args: object) -> bool:
        return False


def _body(answers: dict, input_tokens: int = 123) -> dict:
    return {"answers": answers, "usage": {"input_tokens": input_tokens}}


def test_state_is_prompt_with_only_trailing_line_replaced() -> None:
    judge = _client()
    captured: dict = {}

    def fake_post(payload, *, timeout):  # noqa: ANN001, ARG001
        captured.update(payload)
        return _body(_BASE_ANSWERS)

    with patch.object(judge, "_post", side_effect=fake_post):
        judge.judge(
            prompt="RUBRIC TEXT\n\nMARC context\nReturn only the JSON verdict.",
            schema={},
            context={"evaluator_id": "person_ner", "payload": {}},
        )
    assert captured["state"] == f"RUBRIC TEXT\n\nMARC context\n{JEV_CLOSE}"
    assert captured["model"] == "jev-1.13.0"  # registry prefix stripped
    assert set(captured["questions"]) == _PERSON_NER_QUESTIONS


def test_question_sets_per_evaluator() -> None:
    judge = _client()
    seen: dict[str, set[str]] = {}
    current = ["person_ner"]

    def fake_post(payload, *, timeout):  # noqa: ANN001, ARG001
        seen[current[0]] = set(payload["questions"])
        return _body({"name_ok": {"choice": "yes", "confidence": 0.9},
                      "type_ok": {"choice": "yes", "confidence": 0.9}})

    with patch.object(judge, "_post", side_effect=fake_post):
        for evaluator_id in (
            "person_ner", "provenance_ner", "contents_ner",
            "genre_classifier", "hmo_wikibase_item",
        ):
            current[0] = evaluator_id
            judge.judge(prompt="p\nReturn only the JSON verdict.", schema={},
                        context={"evaluator_id": evaluator_id, "payload": {}})

    assert seen["person_ner"] == \
        {"name_ok", "type_ok", "role_ok", "evidence_field", "match_kind"}
    assert seen["provenance_ner"] == seen["person_ner"]
    assert seen["contents_ner"] == seen["person_ner"]
    assert "name_quality" in seen["hmo_wikibase_item"]
    assert "text_quality" in seen["hmo_wikibase_item"]
    assert "claim_0" not in seen["hmo_wikibase_item"]
    assert seen["genre_classifier"] == \
        {"name_ok", "type_ok", "role_ok", "evidence_field"}


def test_wikidata_claim_questions_from_context_payload() -> None:
    judge = _client()
    captured: dict = {}

    def fake_post(payload, *, timeout):  # noqa: ANN001, ARG001
        captured.update(payload)
        return _body({"name_ok": {"choice": "yes", "confidence": 0.9},
                      "type_ok": {"choice": "yes", "confidence": 0.9}})

    with patch.object(judge, "_post", side_effect=fake_post):
        judge.judge(
            prompt="p\nReturn only the JSON verdict.", schema={},
            context={
                "evaluator_id": "wikidata_item",
                "payload": {"statements": [
                    {"property": "P31", "value_label": "manuscript"},
                    {"property": "P1476", "value_label": "Sefer Torah"},
                ]},
            },
        )
    assert "claim_0" in captured["questions"]
    assert "claim_1" in captured["questions"]
    assert "claim_2" not in captured["questions"]
    assert "claim_8" not in captured["questions"]  # ≤ max_claims
    # Wikidata-contract questions (types + duplicates) ride along with the
    # certified set (Rule W-139 + WPM P31 typing).
    assert "p31_ok" in captured["questions"]
    assert "duplicate_risk" in captured["questions"]


def test_generic_schema_questions_for_unknown_evaluator() -> None:
    judge = _client()
    captured: dict = {}

    def fake_post(payload, *, timeout):  # noqa: ANN001, ARG001
        captured.update(payload)
        return _body({"name_ok": {"choice": "yes", "confidence": 0.9},
                      "type_ok": {"choice": "yes", "confidence": 0.9}})

    schema = {"properties": {
        "name_ok": {"enum": ["yes", "partial", "no", "unknown"]},
        "type_ok": {"enum": ["yes", "partial", "no", "unknown"]},
        "role_ok": {"enum": ["yes", "partial", "no", "n/a", "unknown"]},
    }}
    with patch.object(judge, "_post", side_effect=fake_post):
        judge.judge(prompt="p\nReturn only the JSON verdict.", schema=schema, context=None)
    assert set(captured["questions"]) == {"name_ok", "type_ok", "role_ok"}


def test_verdict_mapping_universal_table_and_meta() -> None:
    judge = _client()
    with patch.object(judge, "_post", return_value=_body(_BASE_ANSWERS)):
        resp = judge.judge(
            prompt="p\nReturn only the JSON verdict.", schema={},
            context={"evaluator_id": "person_ner", "payload": {}},
        )
    assert resp.error is None
    assert resp.verdict is not None
    # This scripted body omits the role answer → n/a (harness parity).
    assert resp.verdict["role_ok"] == "n/a"
    assert resp.verdict["name_ok"] == "yes"
    assert resp.verdict["overall"] == "full"  # universal table
    assert resp.verdict["suggested_fix"] is None
    assert "evidence in colophon_text" in resp.verdict["reasoning"]
    assert resp.input_tokens == 123
    assert resp.output_tokens == 0
    assert resp.meta is not None
    assert resp.meta["confidence"] == 0.8  # min axis confidence


def test_overall_table_fail_and_partial() -> None:
    judge = _client()

    def run(answers: dict):
        with patch.object(judge, "_post", return_value=_body(answers)):
            return judge.judge(
                prompt="p\nReturn only the JSON verdict.", schema={},
                context={"evaluator_id": "person_ner", "payload": {}},
            )

    fail = run({
        "name_ok": {"choice": "yes", "confidence": 0.9},
        "type_ok": {"choice": "no", "confidence": 0.9},
        "role_ok": {"choice": "no", "confidence": 0.9},
    })
    assert fail.verdict is not None and fail.verdict["overall"] == "fail"
    partial = run({
        "name_ok": {"choice": "partial", "confidence": 0.9},
        "type_ok": {"choice": "yes", "confidence": 0.9},
        "role_ok": {"choice": "n/a", "confidence": 0.9},
    })
    assert partial.verdict is not None and partial.verdict["overall"] == "partial"
    na = run({
        "name_ok": {"choice": "yes", "confidence": 0.9},
        "type_ok": {"choice": "yes", "confidence": 0.9},
    })
    assert na.verdict is not None and na.verdict["overall"] == "full"  # n/a→yes


def test_missing_axis_answer_is_row_error() -> None:
    judge = _client()
    with patch.object(judge, "_post", return_value=_body({"name_ok": {"choice": "yes"}})):
        resp = judge.judge(prompt="p\nReturn only the JSON verdict.", schema={}, context=None)
    assert resp.verdict is None
    assert resp.error is not None
    assert "'type_ok'" in resp.error


def test_4xx_fails_row_without_retry() -> None:
    judge = _client(max_retries=3)

    def fake_urlopen(req, timeout):  # noqa: ANN001, ARG001
        raise urllib.error.HTTPError(
            TYPESAFE_URL, 400, "bad request", {}, io.BytesIO(b"too large"),
        )

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        resp = judge.judge(prompt="p\nReturn only the JSON verdict.", schema={}, context=None)
    assert resp.verdict is None
    assert resp.error is not None
    assert "HTTP 400" in resp.error


def test_429_retries_then_succeeds(monkeypatch) -> None:
    monkeypatch.setattr("eval_agent.client.typesafe_client.time.sleep", lambda _s: None)
    judge = _client(max_retries=2)
    calls = {"n": 0}

    def fake_urlopen(req, timeout):  # noqa: ANN001, ARG001
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.HTTPError(
                TYPESAFE_URL, 429, "rate limited", {"retry-after": "0"},
                io.BytesIO(b"{}"),
            )
        return _FakeResponse(json.dumps(_body(_BASE_ANSWERS)).encode())

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        resp = judge.judge(prompt="p\nReturn only the JSON verdict.", schema={},
                           context={"evaluator_id": "person_ner", "payload": {}})
    assert calls["n"] == 2
    assert resp.error is None
    assert resp.verdict is not None
    assert resp.verdict["overall"] == "full"


def test_transport_error_after_max_retries_is_row_error(monkeypatch) -> None:
    monkeypatch.setattr("eval_agent.client.typesafe_client.time.sleep", lambda _s: None)
    judge = _client(max_retries=1)

    def fake_urlopen(req, timeout):  # noqa: ANN001, ARG001
        raise urllib.error.URLError("connection refused")

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        resp = judge.judge(prompt="p\nReturn only the JSON verdict.", schema={}, context=None)
    assert resp.verdict is None
    assert resp.error is not None
    assert "after 2 attempts" in resp.error
