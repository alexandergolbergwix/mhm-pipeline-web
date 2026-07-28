"""eval-agent spawn parallelism (Rule W-135)."""

from __future__ import annotations

import pytest

from app.pipeline.agent_runner import _spawn_parallel_for_provider


@pytest.mark.parametrize(
    ("provider", "env", "expected"),
    [
        ("openai_compat", {}, 2),
        ("openai_compat", {"EVAL_AGENT_OPENAI_COMPAT_PARALLEL": "3"}, 3),
        ("openai_compat", {"EVAL_AGENT_OPENAI_COMPAT_PARALLEL": "99"}, 4),
        ("openai_compat", {"EVAL_AGENT_OPENAI_COMPAT_PARALLEL": "0"}, 1),
        ("gemini", {}, None),
        ("gemini", {"EVAL_AGENT_GEMINI_PARALLEL": "4"}, 4),
    ],
)
def test_spawn_parallel_for_provider(
    provider: str,
    env: dict[str, str],
    expected: int | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in ("EVAL_AGENT_OPENAI_COMPAT_PARALLEL", "EVAL_AGENT_GEMINI_PARALLEL"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    assert _spawn_parallel_for_provider(provider) == expected
