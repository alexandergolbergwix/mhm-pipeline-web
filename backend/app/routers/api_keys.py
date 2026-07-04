"""Per-user encrypted API key router.

Read-back is deliberately *prohibited*: the GET endpoint reports only
whether a key is stored and when it was last used — never the plaintext.
The frontend mirrors that with a "stored — type to replace" placeholder
flow lifted straight from the desktop app's Credentials dialog.

The wrapping KEK is derived from the user's password; we only have it
while their cookie is presented. Forgetting the password makes every
stored secret unrecoverable (zero-knowledge).
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.session import AuthContext, current_auth
from app.crypto import secrets as secrets_mod
from app.db import get_session
from app.models.api_key import ApiKey
from app.services.wikibase_credentials import (
    resolve_wikibase_bot_credentials,
    verify_wikibase_bot_credentials,
    verify_wikibase_bot_credentials_sync,
)

router = APIRouter(prefix="/me/api-keys", tags=["api-keys"])

_WIKIBASE_BOT_KEYS = frozenset({
    "wikibase_cloud_bot_name",
    "wikibase_cloud_bot_username",
    "wikibase_cloud_bot_password",
})


KeyName = Literal[
    "gemini",
    "wikidata",
    "wikibase_cloud_bot_name",
    "wikibase_cloud_bot_username",
    "wikibase_cloud_bot_password",
    "huggingface",
]
_VALID_KEYS: set[str] = {
    "gemini",
    "wikidata",
    "wikibase_cloud_bot_name",
    "wikibase_cloud_bot_username",
    "wikibase_cloud_bot_password",
    "huggingface",
}


class ApiKeyStatus(BaseModel):
    name: KeyName
    set: bool
    last_used_at: datetime | None
    created_at: datetime | None


class SetApiKeyRequest(BaseModel):
    value: str = Field(min_length=1, max_length=4096)


class WikibaseCloudVerifyRequest(BaseModel):
    username: str | None = Field(default=None, max_length=256)
    bot_name: str | None = Field(default=None, max_length=256)
    password: str | None = Field(default=None, max_length=4096)


class WikibaseCloudVerifyResponse(BaseModel):
    ok: bool
    message: str
    login_name: str | None = None


@router.post("/wikibase-cloud/verify", response_model=WikibaseCloudVerifyResponse)
async def verify_wikibase_cloud_credentials(
    payload: WikibaseCloudVerifyRequest | None = None,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> WikibaseCloudVerifyResponse:
    """Test bot-password login against mhm-hmo.wikibase.cloud.

    Omit the body (or leave fields empty) to verify whatever is already
    stored in Settings. Pass individual fields to test a value before
    saving it alongside the other stored secrets.
    """
    overrides = payload or WikibaseCloudVerifyRequest()
    result = await verify_wikibase_bot_credentials(
        db,
        auth,
        username=overrides.username,
        bot_name=overrides.bot_name,
        password=overrides.password,
    )
    return WikibaseCloudVerifyResponse(
        ok=result.ok,
        message=result.message,
        login_name=result.login_name,
    )


@router.get("", response_model=list[ApiKeyStatus])
async def list_keys(
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> list[ApiKeyStatus]:
    rows = (
        await db.execute(select(ApiKey).where(ApiKey.user_id == auth.user.id))
    ).scalars().all()
    by_name = {r.key_name: r for r in rows}
    return [
        ApiKeyStatus(
            name=name,  # type: ignore[arg-type]
            set=name in by_name,
            last_used_at=by_name[name].last_used_at if name in by_name else None,
            created_at=by_name[name].created_at if name in by_name else None,
        )
        for name in sorted(_VALID_KEYS)
    ]


@router.put("/{name}", response_model=ApiKeyStatus)
async def set_key(
    name: str,
    payload: SetApiKeyRequest,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> ApiKeyStatus:
    if name not in _VALID_KEYS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown key name. Allowed: {sorted(_VALID_KEYS)}",
        )
    wrapped = secrets_mod.wrap_secret(payload.value, kek=auth.kek)

    existing = (
        await db.execute(
            select(ApiKey).where(ApiKey.user_id == auth.user.id, ApiKey.key_name == name)
        )
    ).scalar_one_or_none()

    if existing is None:
        existing = ApiKey(user_id=auth.user.id, key_name=name)
        db.add(existing)

    existing.ciphertext = wrapped.ciphertext
    existing.ciphertext_nonce = wrapped.ciphertext_nonce
    existing.dek_wrapped = wrapped.dek_wrapped
    existing.dek_wrap_nonce = wrapped.dek_wrap_nonce

    if name in _WIKIBASE_BOT_KEYS:
        overrides: dict[str, str] = {}
        if name == "wikibase_cloud_bot_username":
            overrides["username"] = payload.value
        elif name == "wikibase_cloud_bot_password":
            overrides["password"] = payload.value
        elif name == "wikibase_cloud_bot_name":
            overrides["bot_name"] = payload.value
        credentials = await resolve_wikibase_bot_credentials(db, auth, **overrides)
        if credentials is not None:
            verify_result = await asyncio.to_thread(
                verify_wikibase_bot_credentials_sync, credentials,
            )
            if not verify_result.ok:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=verify_result.message,
                )

    await db.commit()
    return ApiKeyStatus(
        name=name,  # type: ignore[arg-type]
        set=True,
        last_used_at=existing.last_used_at,
        created_at=existing.created_at,
    )


@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_key(
    name: str,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> None:
    if name not in _VALID_KEYS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown key name",
        )
    row = (
        await db.execute(
            select(ApiKey).where(ApiKey.user_id == auth.user.id, ApiKey.key_name == name)
        )
    ).scalar_one_or_none()
    if row is None:
        return  # idempotent
    await db.delete(row)
    await db.commit()
