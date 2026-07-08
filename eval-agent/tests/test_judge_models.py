"""Tests for tier-1 judge model registry."""

from __future__ import annotations

import pytest

from eval_agent.judge_models import (
    UnknownTier1ModelError,
    default_tier1_model,
    list_tier1_models,
    resolve_tier1_model,
)


def test_default_is_gemini_flash() -> None:
    assert default_tier1_model() == "gemini-3.5-flash"


def test_list_includes_kimi() -> None:
    ids = [m.id for m in list_tier1_models()]
    assert "gemini-3.5-flash" in ids
    assert "moonshotai/Kimi-K2.5" in ids


def test_resolve_unknown_raises() -> None:
    with pytest.raises(UnknownTier1ModelError):
        resolve_tier1_model("not-a-real-model")


def test_kimi_is_openai_compat_without_agentic() -> None:
    spec = resolve_tier1_model("moonshotai/Kimi-K2.5")
    assert spec.provider == "openai_compat"
    assert spec.supports_agentic is False
    assert spec.base_url is not None
