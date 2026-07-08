"""Tests for OpenAI-compatible judge client."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from eval_agent.client.openai_compat_client import OpenAICompatJudge
from eval_agent.client.rate_limiter import RateLimiter


def test_judge_parses_json_verdict() -> None:
    payload = {
        "choices": [{
            "message": {
                "content": json.dumps({
                    "overall": "full",
                    "name_ok": "yes",
                    "type_ok": "yes",
                    "role_ok": "n/a",
                    "reasoning": "looks good",
                }),
            },
        }],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20},
    }
    judge = OpenAICompatJudge(
        model="moonshotai/Kimi-K2.5",
        api_key="test-key",
        base_url="https://platform.qubrid.com/v1",
        rate_limiter=RateLimiter(60),
    )
    with patch.object(judge, "_post", return_value=payload):
        resp = judge.judge(
            prompt="decide this",
            schema={"type": "object", "properties": {"overall": {"type": "string"}}},
        )
    assert resp.error is None
    assert resp.verdict is not None
    assert resp.verdict["overall"] == "full"
    assert resp.input_tokens == 10
    assert resp.output_tokens == 20
