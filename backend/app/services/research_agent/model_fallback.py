"""Model resolution with a provider fallback and a humanized 402 (F3).

The research planner is billed on the Qubrid account; when that account runs
out of credit every call fails with ``insufficient_quota`` (HTTP 402) and the
assistant dies with a raw error (2026-10-17). Two guards: a fallback to the
Gemini OpenAI-compatible endpoint (same OpenAIChatModel, no new dependency)
and a plain-language message when no provider can serve the request.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ModelConfig:
    model: str
    base_url: str
    api_key: str
    source: str


HUMANIZED_QUOTA_MESSAGE = (
    "The AI model account is out of credit, so the assistant cannot answer "
    "right now. The administrator must recharge it at "
    "https://platform.qubrid.com/billing. Your tools, canvas, and datasets "
    "keep working — try again after the recharge."
)
HUMANIZED_MODEL_FAILURE = (
    "The AI model service is unavailable right now. Your tools, canvas, and "
    "datasets keep working — try again in a few minutes."
)


def is_quota_error(message: str) -> bool:
    lowered = message.lower()
    return "insufficient_quota" in lowered or "insufficient credits" in lowered or "status_code: 402" in lowered


def humanize_model_error(message: str) -> str:
    if is_quota_error(message):
        return HUMANIZED_QUOTA_MESSAGE
    return HUMANIZED_MODEL_FAILURE


def resolve_model_configs() -> list[ModelConfig]:
    """Primary + fallback model configurations, best first."""
    configs: list[ModelConfig] = []
    qubrid_key = os.environ.get("QUBRID_API_KEY") or ""
    qubrid_model = os.environ.get("RESEARCH_AGENT_MODEL") or "glm-5.3-flash"
    if qubrid_key:
        configs.append(ModelConfig(
            model=qubrid_model,
            base_url=(os.environ.get("QUBRID_BASE_URL") or "https://platform.qubrid.com/v1").rstrip("/"),
            api_key=qubrid_key,
            source="qubrid",
        ))
    gemini_key = os.environ.get("GEMINI_API_KEY") or ""
    if gemini_key:
        configs.append(ModelConfig(
            model=os.environ.get("RESEARCH_AGENT_FALLBACK_MODEL") or "gemini-2.5-flash",
            base_url=(
                os.environ.get("RESEARCH_AGENT_FALLBACK_BASE_URL")
                or "https://generativelanguage.googleapis.com/v1beta/openai/"
            ).rstrip("/"),
            api_key=gemini_key,
            source="gemini",
        ))
    return configs
