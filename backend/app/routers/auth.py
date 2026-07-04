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

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cryptography.exceptions import InvalidTag
from sqlalchemy import delete

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
from app.crypto import secrets as secrets_mod
from app.db import get_session
from app.middleware.rate_limit import limiter
from app.models.api_key import ApiKey
from app.models.session import Session as SessionRow
from app.models.user import User
from app.schemas.auth import (
    LoginRequest,
    LoginResponse,
    MeResponse,
    PasswordChangeRequest,
)
from app.services.auth_me import build_me_response
from app.services.wikibase_user_access import ensure_wikibase_access

router = APIRouter(prefix="/auth", tags=["auth"])

# Computed once at import time so the no-such-user branch of /login has the
# same ~150ms cost as a real Argon2id verify — defeats timing-based account
# enumeration. The plaintext is irrelevant; only the hash's parameters matter.
_DUMMY_HASH = pw.hash_password("dummy-password-for-timing-parity-do-not-remove")


@router.post("/login", response_model=LoginResponse)
@limiter.limit("10/minute")
async def login(
    request: Request,
    payload: LoginRequest,
    response: Response,
    db: AsyncSession = Depends(get_session),
) -> LoginResponse:
    email_idx = idx.blind_index(payload.email)
    user = (
        await db.execute(select(User).where(User.email_index == email_idx))
    ).scalar_one_or_none()

    # Defence: same response for "no such user" and "wrong password" so
    # an attacker can't enumerate accounts via timing or error text. The
    # 401 detail string MUST be identical for both branches.
    if user is None:
        # Burn the same ~150ms a real verify would take, then 401.
        pw.verify_password(payload.password, _DUMMY_HASH)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )
    if not pw.verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )

    # Derive KEK from the now-verified password + the user's stored salt.
    kek = kek_mod.derive_kek(payload.password, salt=user.kek_salt)

    # Mint session: wrap KEK with a fresh random session_secret.
    session_row, session_secret = await create_session(db, user=user, kek=kek)

    # Opportunistic password-hash upgrade if our Argon2id parameters
    # have moved on since the user last logged in.
    if pw.needs_rehash(user.password_hash):
        user.password_hash = pw.hash_password(payload.password)

    email = pii.decrypt_pii(user.email_encrypted)
    wikibase = await ensure_wikibase_access(db, user=user, email=email)
    await db.commit()

    set_session_cookie(response, session_id=session_row.id, session_secret=session_secret)

    return build_me_response(user, email, wikibase)


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
async def me(
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> MeResponse:
    email = pii.decrypt_pii(auth.user.email_encrypted)
    wikibase = await ensure_wikibase_access(db, user=auth.user, email=email)
    await db.commit()
    return build_me_response(auth.user, email, wikibase)


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    payload: PasswordChangeRequest,
    response: Response,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> Response:
    """Re-derive the user's KEK from the new password and re-wrap every
    stored DEK in place. This preserves the user's saved API keys —
    unlike :func:`reset_password`, which has no old password to decrypt
    with and therefore must wipe them.
    """
    if not pw.verify_password(payload.current_password, auth.user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Current password is wrong",
        )
    if payload.current_password == payload.new_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must differ from the current one",
        )

    old_kek = auth.kek  # already unwrapped for this request
    new_salt = pii.random_bytes(16)
    new_kek = kek_mod.derive_kek(payload.new_password, salt=new_salt)

    # Re-wrap every DEK in-place: decrypt with old_kek, re-wrap with new_kek.
    keys = (
        await db.execute(select(ApiKey).where(ApiKey.user_id == auth.user.id))
    ).scalars().all()
    for k in keys:
        try:
            wrapped = secrets_mod.WrappedSecret(
                ciphertext=k.ciphertext,
                ciphertext_nonce=k.ciphertext_nonce,
                dek_wrapped=k.dek_wrapped,
                dek_wrap_nonce=k.dek_wrap_nonce,
            )
            plaintext = secrets_mod.unwrap_secret(wrapped, kek=old_kek)
            re_wrapped = secrets_mod.wrap_secret(plaintext, kek=new_kek)
        except InvalidTag as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Could not re-wrap stored secrets; aborting change",
            ) from exc
        k.ciphertext = re_wrapped.ciphertext
        k.ciphertext_nonce = re_wrapped.ciphertext_nonce
        k.dek_wrapped = re_wrapped.dek_wrapped
        k.dek_wrap_nonce = re_wrapped.dek_wrap_nonce

    auth.user.password_hash = pw.hash_password(payload.new_password)
    auth.user.kek_salt = new_salt

    # Invalidate every existing session — the cookie-wrapped KEK is now
    # stale. The current session is replaced with a fresh one bound to
    # the new KEK so the user stays logged in seamlessly.
    await db.execute(delete(SessionRow).where(SessionRow.user_id == auth.user.id))
    new_session, new_secret = await create_session(db, user=auth.user, kek=new_kek)
    await db.commit()

    # Build the actual returned response. Setting the cookie on the
    # FastAPI-injected ``response`` and then returning a fresh
    # Response() (the old bug) silently dropped the cookie — the
    # browser kept its now-invalidated session cookie and every
    # subsequent request 401'd with "Session not found".
    out = Response(status_code=status.HTTP_204_NO_CONTENT)
    set_session_cookie(
        out, session_id=new_session.id, session_secret=new_secret,
    )
    return out
