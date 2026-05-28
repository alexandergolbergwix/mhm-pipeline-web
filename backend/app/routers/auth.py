"""Auth router — login, logout, /me.

Login is the ONLY place where the user's password reaches the server
in plaintext. We derive the KEK from the password (Argon2id), verify
the password against the stored Argon2id hash (a *separate* hash, see
:mod:`app.auth.password` vs :mod:`app.crypto.kek`), then mint a new
session: the KEK is wrapped with a fresh random ``session_secret``,
stored in ``sessions.kek_wrapped``, and the ``session_secret`` is sent
as the second half of the session cookie. Plaintext password and
plaintext KEK both fall out of scope as soon as the response is built.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import password as pw
from app.auth.session import (
    AuthContext,
    clear_session_cookie,
    create_session,
    current_auth,
    delete_session,
    set_session_cookie,
)
from app.crypto import index as idx
from app.crypto import kek as kek_mod
from app.crypto import pii
from app.db import get_session
from app.models.user import User
from app.schemas.auth import LoginRequest, LoginResponse, MeResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
async def login(
    payload: LoginRequest,
    response: Response,
    db: AsyncSession = Depends(get_session),
) -> LoginResponse:
    email_idx = idx.blind_index(payload.email)
    user = (
        await db.execute(select(User).where(User.email_index == email_idx))
    ).scalar_one_or_none()

    # Defence: same response for "no such user" and "wrong password" so
    # an attacker can't enumerate accounts via timing or error text.
    invalid = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password",
    )
    if user is None or not pw.verify_password(payload.password, user.password_hash):
        raise invalid

    # Derive KEK from the now-verified password + the user's stored salt.
    kek = kek_mod.derive_kek(payload.password, salt=user.kek_salt)

    # Mint session: wrap KEK with a fresh random session_secret.
    session_row, session_secret = await create_session(db, user=user, kek=kek)

    # Opportunistic password-hash upgrade if our Argon2id parameters
    # have moved on since the user last logged in.
    if pw.needs_rehash(user.password_hash):
        user.password_hash = pw.hash_password(payload.password)

    await db.commit()

    set_session_cookie(response, session_id=session_row.id, session_secret=session_secret)

    return LoginResponse(
        id=user.id,
        email=pii.decrypt_pii(user.email_encrypted),
        name=pii.decrypt_pii(user.name_encrypted),
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> Response:
    await delete_session(db, auth.session.id)
    await db.commit()
    clear_session_cookie(response)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=MeResponse)
async def me(auth: AuthContext = Depends(current_auth)) -> MeResponse:
    return MeResponse(
        id=auth.user.id,
        email=pii.decrypt_pii(auth.user.email_encrypted),
        name=pii.decrypt_pii(auth.user.name_encrypted),
    )
