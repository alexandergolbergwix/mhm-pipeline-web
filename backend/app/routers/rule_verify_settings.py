"""Per-curator rule-verification settings (blocking rules, filter presets)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.session import AuthContext, current_auth
from app.db import get_session
from app.models.user_rule_settings import UserRuleSettings

router = APIRouter(prefix="/me/rule-verify-settings", tags=["rule-verify"])


class RuleVerifySettingsPayload(BaseModel):
    blocked_rules: dict[str, bool] = Field(default_factory=dict)
    filter_presets: list[dict[str, Any]] | None = None


class RuleVerifySettingsResponse(RuleVerifySettingsPayload):
    pass


async def _get_or_create(db: AsyncSession, user_id) -> UserRuleSettings:
    row = await db.get(UserRuleSettings, user_id)
    if row is None:
        row = UserRuleSettings(user_id=user_id)
        db.add(row)
        await db.flush()
    return row


@router.get("", response_model=RuleVerifySettingsResponse)
async def get_settings_row(
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> RuleVerifySettingsResponse:
    row = await _get_or_create(db, auth.user.id)
    await db.commit()
    return RuleVerifySettingsResponse(
        blocked_rules=dict(row.blocked_rules or {}),
        filter_presets=row.filter_presets,
    )


@router.put("", response_model=RuleVerifySettingsResponse)
async def put_settings_row(
    payload: RuleVerifySettingsPayload,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> RuleVerifySettingsResponse:
    row = await _get_or_create(db, auth.user.id)
    row.blocked_rules = {str(k): bool(v) for k, v in payload.blocked_rules.items() if v}
    row.filter_presets = payload.filter_presets
    await db.commit()
    return RuleVerifySettingsResponse(
        blocked_rules=dict(row.blocked_rules or {}),
        filter_presets=row.filter_presets,
    )
