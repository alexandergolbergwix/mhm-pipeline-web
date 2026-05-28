"""Session creation, cookie handling, and FastAPI dependencies.

Session cookie format::

    mhm_session = <session_id>.<session_secret_b64url>

Both halves are required to unwrap the KEK. The cookie is HTTP-only,
``SameSite=Lax``, and ``Secure`` in production.
"""

from __future__ import annotations

import base64
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import Cookie, Depends, HTTPException, Response, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crypto import kek as kek_mod
from app.db import get_session
from app.models.session import Session as SessionRow
from app.models.user import User
from app.settings import get_settings

COOKIE_NAME = "mhm_session"


@dataclass
class AuthContext:
    """The unit of trust handed to authenticated route handlers."""

    user: User
    session: SessionRow
    kek: bytes  # plaintext, lives in memory for the duration of the request

    @property
    def user_id(self) -> uuid.UUID:
        return self.user.id


# ── Cookie encode/decode ────────────────────────────────────────────────


def _encode_cookie(session_id: uuid.UUID, session_secret: bytes) -> str:
    return f"{session_id}.{base64.urlsafe_b64encode(session_secret).decode('ascii').rstrip('=')}"


def _decode_cookie(value: str) -> tuple[uuid.UUID, bytes] | None:
    try:
        sid_str, secret_b64 = value.split(".", 1)
        sid = uuid.UUID(sid_str)
        # Re-pad before decode.
        secret_b64 += "=" * (-len(secret_b64) % 4)
        secret = base64.urlsafe_b64decode(secret_b64)
        if len(secret) != 32:
            return None
        return sid, secret
    except (ValueError, AttributeError):
        return None


# ── Session creation / deletion ─────────────────────────────────────────


async def create_session(
    db: AsyncSession,
    *,
    user: User,
    kek: bytes,
) -> tuple[SessionRow, bytes]:
    """Create a fresh server-side session row + return the matching
    ``session_secret`` to be sent as a cookie."""
    settings = get_settings()
    session_secret = kek_mod.new_session_secret()
    wrapped, nonce = kek_mod.wrap_kek(kek, session_secret)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=settings.session_ttl_hours)
    row = SessionRow(
        user_id=user.id,
        kek_wrapped=wrapped,
        kek_wrap_nonce=nonce,
        expires_at=expires_at,
    )
    db.add(row)
    await db.flush()  # populate row.id
    return row, session_secret


def set_session_cookie(
    response: Response,
    *,
    session_id: uuid.UUID,
    session_secret: bytes,
) -> None:
    settings = get_settings()
    response.set_cookie(
        key=COOKIE_NAME,
        value=_encode_cookie(session_id, session_secret),
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=settings.session_ttl_hours * 3600,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=COOKIE_NAME, path="/")


async def delete_session(db: AsyncSession, session_id: uuid.UUID) -> None:
    await db.execute(delete(SessionRow).where(SessionRow.id == session_id))


# ── Per-request dependency: extract + verify the session ────────────────


async def current_auth(
    db: AsyncSession = Depends(get_session),
    cookie_value: str | None = Cookie(default=None, alias=COOKIE_NAME),
) -> AuthContext:
    """FastAPI dependency: resolve cookie → User + decrypted KEK.

    Raises 401 on missing cookie, malformed cookie, expired session, or
    wrapping-key mismatch (which would indicate tampering).
    """
    if not cookie_value:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    decoded = _decode_cookie(cookie_value)
    if decoded is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Malformed session cookie",
        )
    session_id, session_secret = decoded

    session_row = (
        await db.execute(select(SessionRow).where(SessionRow.id == session_id))
    ).scalar_one_or_none()
    if session_row is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Session not found",
        )
    if session_row.expires_at < datetime.now(timezone.utc):
        await delete_session(db, session_id)
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired",
        )

    try:
        kek = kek_mod.unwrap_kek(
            session_row.kek_wrapped, session_row.kek_wrap_nonce, session_secret,
        )
    except Exception as exc:  # noqa: BLE001 — any unwrap failure → 401
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session verification failed",
        ) from exc

    user = (
        await db.execute(select(User).where(User.id == session_row.user_id))
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User missing",
        )

    # Sliding expiry — push expiry forward on every authenticated touch.
    settings = get_settings()
    session_row.expires_at = datetime.now(timezone.utc) + timedelta(
        hours=settings.session_ttl_hours,
    )
    session_row.last_seen_at = datetime.now(timezone.utc)
    await db.commit()

    return AuthContext(user=user, session=session_row, kek=kek)
