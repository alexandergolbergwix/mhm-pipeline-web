"""Tier-1 judge model registry (YAML-backed)."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_CONFIG = _REPO_ROOT / "config" / "tier1_models.yaml"


class UnknownTier1ModelError(ValueError):
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


@lru_cache(maxsize=1)
def _load_raw(config_path: str | None = None) -> dict[str, Any]:
    path = Path(config_path) if config_path else _DEFAULT_CONFIG
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError(f"invalid tier1_models config: {path}")
    return data


def default_tier1_model(*, config_path: str | None = None) -> str:
    raw = _load_raw(config_path)
    return str(raw.get("default") or "gemini-3.5-flash")


def list_tier1_models(*, config_path: str | None = None) -> list[Tier1ModelSpec]:
    raw = _load_raw(config_path)
    models = raw.get("models") or {}
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


def resolve_tier1_model(
    model_id: str | None,
    *,
    config_path: str | None = None,
) -> Tier1ModelSpec:
    mid = (model_id or "").strip() or default_tier1_model(config_path=config_path)
    for spec in list_tier1_models(config_path=config_path):
        if spec.id == mid:
            return spec
    known = [s.id for s in list_tier1_models(config_path=config_path)]
    raise UnknownTier1ModelError(f"unknown tier-1 model {mid!r}; known: {known}")
