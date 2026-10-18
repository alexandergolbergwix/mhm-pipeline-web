"""Model layer: Qubrid-only, with a circuit breaker and humanized 402s.

Provider policy (2026-10-17): Qubrid ONLY — no other providers. Resilience
therefore comes from (1) a breaker that stops hammering an out-of-credit
account, (2) deterministic answers for fact questions (answer_router), and
(3) plain-language terminal errors with machine codes. When the account is
out of credit the humanized message tells the curator exactly what to do.
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class ModelConfig:
    model: str
    base_url: str
    api_key: str
    source: str = "qubrid"


HUMANIZED_QUOTA_MESSAGE = (
    "The AI model account is out of credit, so the assistant cannot answer "
    "that question right now. The administrator must recharge it at "
    "https://platform.qubrid.com/billing. Your tools, canvas, and datasets "
    "keep working — try again after the recharge."
)
HUMANIZED_MODEL_FAILURE = (
    "The AI model service is unavailable right now. Your tools, canvas, and "
    "datasets keep working — try again in a few minutes."
)

# Breaker: one 402 opens the circuit for this long (no more pointless calls).
_BREAKER_OPEN_S = 600.0
_breaker_lock = threading.Lock()
_breaker_open_until = 0.0


def is_quota_error(message: str) -> bool:
    lowered = message.lower()
    return (
        "insufficient_quota" in lowered
        or "insufficient credits" in lowered
        or "status_code: 402" in lowered
    )


def humanize_model_error(message: str) -> tuple[str, str]:
    """Return (human message, machine code) for a terminal model error."""
    if is_quota_error(message):
        return HUMANIZED_QUOTA_MESSAGE, "model_quota_exhausted"
    return HUMANIZED_MODEL_FAILURE, "model_unavailable"


def breaker_open() -> bool:
    with _breaker_lock:
        return time.time() < _breaker_open_until


def trip_breaker() -> None:
    global _breaker_open_until
    with _breaker_lock:
        _breaker_open_until = time.time() + _BREAKER_OPEN_S


def reset_breaker() -> None:
    global _breaker_open_until
    with _breaker_lock:
        _breaker_open_until = 0.0


def resolve_model_configs() -> list[ModelConfig]:
    """Qubrid, and only Qubrid (provider policy)."""
    key = os.environ.get("QUBRID_API_KEY") or ""
    if not key:
        return []
    return [ModelConfig(
        model=os.environ.get("RESEARCH_AGENT_MODEL") or "glm-5.3-flash",
        base_url=(os.environ.get("QUBRID_BASE_URL") or "https://platform.qubrid.com/v1").rstrip("/"),
        api_key=key,
        source="qubrid",
    )]
