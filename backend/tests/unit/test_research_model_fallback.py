"""Model layer: Qubrid-only policy, breaker, humanized quota errors."""
from __future__ import annotations

from app.services.research_agent.model_fallback import (
    HUMANIZED_QUOTA_MESSAGE,
    breaker_open,
    humanize_model_error,
    is_quota_error,
    resolve_model_configs,
    reset_breaker,
    trip_breaker,
)


def test_quota_errors_are_detected_and_humanized_with_codes():
    assert is_quota_error("status_code: 402, body: {'type': 'insufficient_quota'}")
    assert is_quota_error("Insufficient credits to process this request.")
    message, code = humanize_model_error("insufficient_quota")
    assert message == HUMANIZED_QUOTA_MESSAGE
    assert code == "model_quota_exhausted"
    message, code = humanize_model_error("connection reset")
    assert code == "model_unavailable"
    assert "unavailable" in message.lower()


def test_only_qubrid_is_configured(monkeypatch):
    """Provider policy: Qubrid only — no other providers, ever."""
    monkeypatch.setenv("QUBRID_API_KEY", "q-key")
    monkeypatch.setenv("RESEARCH_AGENT_MODEL", "glm-5.3-flash")
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")  # must be ignored

    configs = resolve_model_configs()

    assert [c.source for c in configs] == ["qubrid"]
    assert configs[0].model == "glm-5.3-flash"
    assert "qubrid" in configs[0].base_url


def test_breaker_opens_on_a_quota_trip_and_closes(monkeypatch):
    reset_breaker()
    assert breaker_open() is False
    trip_breaker()
    assert breaker_open() is True
    reset_breaker()
    assert breaker_open() is False


def test_no_key_means_no_model_configs(monkeypatch):
    monkeypatch.delenv("QUBRID_API_KEY", raising=False)
    assert resolve_model_configs() == []
