"""Model fallback + humanized quota errors (F3)."""
from __future__ import annotations

from app.services.research_agent.model_fallback import (
    HUMANIZED_QUOTA_MESSAGE,
    humanize_model_error,
    is_quota_error,
    resolve_model_configs,
)


def test_quota_errors_are_detected_and_humanized():
    assert is_quota_error("status_code: 402, body: {'type': 'insufficient_quota'}")
    assert is_quota_error("Insufficient credits to process this request.")
    assert humanize_model_error("insufficient_quota") == HUMANIZED_QUOTA_MESSAGE
    assert "recharge" in humanize_model_error("status_code: 402")
    assert "unavailable" in humanize_model_error("connection reset").lower()


def test_model_configs_put_qubrid_first_and_gemini_as_fallback(monkeypatch):
    monkeypatch.setenv("QUBRID_API_KEY", "q-key")
    monkeypatch.setenv("RESEARCH_AGENT_MODEL", "glm-5.3-flash")
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")

    configs = resolve_model_configs()

    assert [c.source for c in configs] == ["qubrid", "gemini"]
    assert configs[0].model == "glm-5.3-flash"
    assert configs[1].base_url.endswith("/v1beta/openai")


def test_model_configs_skip_providers_without_keys(monkeypatch):
    monkeypatch.delenv("QUBRID_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    assert resolve_model_configs() == []
