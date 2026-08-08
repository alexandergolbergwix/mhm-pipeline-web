"""Per-user encrypted API key router.

Read-back is deliberately *prohibited*: the GET endpoint reports only
whether a key is stored and when it was last used — never the plaintext.
The frontend mirrors that with a "stored — type to replace" placeholder
flow lifted straight from the desktop app's Credentials dialog.

The wrapping KEK is derived from the user's password; we only have it
while their cookie is presented. Forgetting the password makes every
stored secret unrecoverable (zero-knowledge).

Wikibase Cloud credentials are server-held OAuth config vars — not
stored per-user here.
"""

from __future__ import annotations

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

router = APIRouter(prefix="/me/api-keys", tags=["api-keys"])


KeyName = Literal[
    "gemini",
    "wikidata",
    "wikidata_test",
    "huggingface",
]
# Display / list order — live Wikidata then test.wikidata.org bot.
_KEY_ORDER: tuple[str, ...] = (
    "gemini",
    "wikidata",
    "wikidata_test",
    "huggingface",
)
_VALID_KEYS: set[str] = set(_KEY_ORDER)


class ApiKeyStatus(BaseModel):
    name: KeyName
    set: bool
    last_used_at: datetime | None
    created_at: datetime | None


class SetApiKeyRequest(BaseModel):
    value: str = Field(min_length=1, max_length=4096)


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
        for name in _KEY_ORDER
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
