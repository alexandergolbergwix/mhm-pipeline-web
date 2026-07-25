"""Tests for GET /api/judge-models."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_list_judge_models(auth_user) -> None:
    _user, async_client = auth_user
    r = await async_client.get("/api/judge-models")
    assert r.status_code == 200
    body = r.json()
    assert body["default"] == "gemini-3.5-flash"
    ids = [m["id"] for m in body["models"]]
    assert "gemini-3.5-flash" in ids
    assert "moonshotai/Kimi-K2.5" in ids
    assert "deepseek-ai/DeepSeek-V4-Flash" in ids
    gemini = next(m for m in body["models"] if m["id"] == "gemini-3.5-flash")
    assert gemini["supports_agentic"] is True
    deepseek = next(m for m in body["models"] if m["id"] == "deepseek-ai/DeepSeek-V4-Flash")
    assert deepseek["provider"] == "openai_compat"
    assert deepseek["supports_agentic"] is False
    assert deepseek["label"] == "DeepSeek V4 Flash (Qubrid)"
