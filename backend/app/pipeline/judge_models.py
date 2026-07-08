"""Tier-1 judge model registry — reads eval-agent/config/tier1_models.yaml."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.pipeline.agent_runner import locate_eval_agent

DEFAULT_TIER1_MODEL = "gemini-3.5-flash"


class UnknownTier1ModelError(ValueError):
    pass


class Tier1CredentialsError(ValueError):
    pass


@dataclass(frozen=True)
class Tier1ModelSpec:
    id: str
    label: str
    provider: str
    api_key_env: str
    supports_agentic: bool
    base_url: str | None = None
    extra_body: dict[str, Any] | None = None


def _config_path() -> Path:
    return locate_eval_agent() / "config" / "tier1_models.yaml"


@lru_cache(maxsize=1)
def _load_raw() -> dict[str, Any]:
    path = _config_path()
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError(f"invalid tier1_models config: {path}")
    return data


def default_tier1_model() -> str:
    return str(_load_raw().get("default") or DEFAULT_TIER1_MODEL)


def list_tier1_models() -> list[Tier1ModelSpec]:
    models = _load_raw().get("models") or {}
    out: list[Tier1ModelSpec] = []
    for model_id, spec in models.items():
        if not isinstance(spec, dict):
            continue
        out.append(
            Tier1ModelSpec(
                id=str(model_id),
                label=str(spec.get("label") or model_id),
                provider=str(spec.get("provider") or "gemini"),
                api_key_env=str(spec.get("api_key_env") or "GEMINI_API_KEY"),
                supports_agentic=bool(spec.get("supports_agentic", True)),
                base_url=spec.get("base_url"),
                extra_body=(
                    dict(spec["extra_body"])
                    if isinstance(spec.get("extra_body"), dict)
                    else None
                ),
            )
        )
    return out


def resolve_tier1_model(model_id: str | None) -> Tier1ModelSpec:
    mid = (model_id or "").strip() or default_tier1_model()
    for spec in list_tier1_models():
        if spec.id == mid:
            return spec
    known = [s.id for s in list_tier1_models()]
    raise UnknownTier1ModelError(f"unknown tier-1 model {mid!r}; known: {known}")


def tier1_api_key_for_spec(
    spec: Tier1ModelSpec,
    *,
    gemini_key: str | None,
    extra_env: dict[str, str] | None = None,
) -> str | None:
    env = {**os.environ, **(extra_env or {})}
    if spec.provider == "gemini":
        return gemini_key or env.get(spec.api_key_env)
    return env.get(spec.api_key_env)


def ensure_tier1_credentials(
    spec: Tier1ModelSpec,
    *,
    gemini_key: str | None,
    extra_env: dict[str, str] | None = None,
) -> None:
    key = tier1_api_key_for_spec(spec, gemini_key=gemini_key, extra_env=extra_env)
    if not key:
        if spec.provider == "gemini":
            raise Tier1CredentialsError(
                "No Gemini API key configured. Open Settings → Credentials and add one.",
            )
        raise Tier1CredentialsError(
            f"{spec.label} is not configured on this server "
            f"(missing env {spec.api_key_env}).",
        )


def model_available(
    spec: Tier1ModelSpec,
    *,
    gemini_key: str | None,
) -> bool:
    try:
        ensure_tier1_credentials(spec, gemini_key=gemini_key)
        return True
    except Tier1CredentialsError:
        return False
