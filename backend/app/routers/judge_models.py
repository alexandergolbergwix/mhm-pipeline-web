"""Public tier-1 judge model list for verify UIs."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.session import AuthContext, current_auth
from app.db import get_session
from app.pipeline.ai_verifier import unwrap_user_gemini_key
from app.pipeline.judge_models import (
    default_tier1_model,
    list_tier1_models,
    model_available,
)

router = APIRouter(prefix="/judge-models", tags=["judge-models"])


class Tier1ModelOut(BaseModel):
    id: str
    label: str
    provider: str
    supports_agentic: bool
    available: bool


class Tier1ModelListOut(BaseModel):
    default: str
    models: list[Tier1ModelOut]


@router.get("", response_model=Tier1ModelListOut)
async def list_judge_models(
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> Tier1ModelListOut:
    gemini_key = await unwrap_user_gemini_key(
        db, user_id=auth.user.id, kek=auth.kek,
    )
    models = [
        Tier1ModelOut(
            id=spec.id,
            label=spec.label,
            provider=spec.provider,
            supports_agentic=spec.supports_agentic,
            available=model_available(spec, gemini_key=gemini_key),
        )
        for spec in list_tier1_models()
    ]
    return Tier1ModelListOut(default=default_tier1_model(), models=models)
