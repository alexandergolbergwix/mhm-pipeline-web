"""Accept-invite + forgot/reset password endpoints (no auth required)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import password as pw
from app.auth.session import create_session, set_session_cookie
from app.auth.tokens import hash_token, new_token
from app.crypto import index as idx
from app.crypto import kek as kek_mod
from app.crypto import pii
from app.db import get_session
from app.models.api_key import ApiKey
from app.models.invitation import Invitation
from app.models.password_reset import PasswordResetToken
from app.models.user import User
from app.schemas.auth import (
    AcceptInviteRequest,
    ForgotPasswordRequest,
    ForgotPasswordResponse,
    InvitePreviewResponse,
    LoginResponse,
    ResetPasswordRequest,
    ResetPasswordResponse,
)
from app.services.auth_me import build_me_response
from app.services.wikibase_user_access import ensure_wikibase_access
from app.settings import get_settings

router = APIRouter(prefix="/onboarding", tags=["onboarding"])

RESET_TTL_HOURS = 1


# ── Invite preview + accept ──────────────────────────────────────────────


@router.get("/invite/{token}", response_model=InvitePreviewResponse)
async def preview_invite(
    token: str, db: AsyncSession = Depends(get_session),
) -> InvitePreviewResponse:
    inv = await _lookup_invite(db, token)
    return InvitePreviewResponse(
        email=pii.decrypt_pii(inv.email_encrypted),
        name="",  # the invitee provides their own name at accept time
        role=inv.role,
        expires_at=inv.expires_at,
    )


@router.post("/accept-invite", response_model=LoginResponse)
async def accept_invite(
    payload: AcceptInviteRequest,
    response: Response,
    db: AsyncSession = Depends(get_session),
) -> LoginResponse:
    inv = await _lookup_invite(db, payload.token)
    email = pii.decrypt_pii(inv.email_encrypted)

    # Defend against a race: someone else may have signed up with this email.
    existing = (
        await db.execute(select(User).where(User.email_index == inv.email_index))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this email already exists",
        )

    user = User(
        email_index=inv.email_index,
        email_encrypted=inv.email_encrypted,
        # The invitee supplies their display name at accept time via the
        # frontend's accept form, which echoes it back into ``payload``
        # only if you extend AcceptInviteRequest. For now we seed name
        # to the local-part of the email; users can rename in Settings.
        name_encrypted=pii.encrypt_pii(email.split("@", 1)[0]),
        password_hash=pw.hash_password(payload.password),
        kek_salt=pii.random_bytes(16),
        role=inv.role,
    )
    db.add(user)
    await db.flush()
    inv.accepted_at = datetime.now(timezone.utc)

    # Mint a session right away — the invitee is logged in after accept.
    kek = kek_mod.derive_kek(payload.password, salt=user.kek_salt)
    session_row, session_secret = await create_session(db, user=user, kek=kek)
    wikibase = await ensure_wikibase_access(
        db, user=user, email=email, attempt_provision=True,
    )
    await db.commit()
    set_session_cookie(
        response, session_id=session_row.id, session_secret=session_secret,
    )

    return build_me_response(user, email, wikibase)


# ── Forgot / reset password ──────────────────────────────────────────────


@router.post("/forgot-password", response_model=ForgotPasswordResponse)
async def forgot_password(
    payload: ForgotPasswordRequest, db: AsyncSession = Depends(get_session),
) -> ForgotPasswordResponse:
    user = (
        await db.execute(
            select(User).where(User.email_index == idx.blind_index(payload.email))
        )
    ).scalar_one_or_none()

    # Don't betray whether the email exists.
    if user is None:
        return ForgotPasswordResponse(ok=True)

    plaintext, h = new_token()
    db.add(
        PasswordResetToken(
            user_id=user.id,
            token_hash=h,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=RESET_TTL_HOURS),
        )
    )
    await db.commit()

    base = get_settings().frontend_origin.rstrip("/")
    url = f"{base}/reset-password?token={plaintext}"

    # In production this URL goes via Postmark / SMTP. In dev we return
    # it inline so the operator can copy-paste without needing real mail.
    dev_url = None if get_settings().is_production else url
    return ForgotPasswordResponse(ok=True, dev_reset_url=dev_url)


@router.post("/reset-password", response_model=ResetPasswordResponse)
async def reset_password(
    payload: ResetPasswordRequest, db: AsyncSession = Depends(get_session),
) -> ResetPasswordResponse:
    h = hash_token(payload.token)
    now = datetime.now(timezone.utc)

    reset = (
        await db.execute(
            select(PasswordResetToken).where(PasswordResetToken.token_hash == h)
        )
    ).scalar_one_or_none()

    if (
        reset is None
        or reset.used_at is not None
        or reset.expires_at < now
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset link",
        )

    user = (
        await db.execute(select(User).where(User.id == reset.user_id))
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset link",
        )

    # ZERO-KNOWLEDGE CONSEQUENCE: the user's old KEK was derived from the
    # old password. We don't have either, so every wrapped DEK becomes
    # unrecoverable. Wipe the api_keys rows so the user re-enters them
    # post-reset. (The Settings UI surfaces this with a confirmation
    # *before* the user clicks "Reset password" so they know what's
    # about to happen.)
    wipe_result = await db.execute(delete(ApiKey).where(ApiKey.user_id == user.id))
    wiped = wipe_result.rowcount or 0

    # New password → new KEK derived from a fresh salt. Old salt is
    # discarded — the old wraps were already unrecoverable so there's
    # no value in keeping it.
    user.password_hash = pw.hash_password(payload.new_password)
    user.kek_salt = pii.random_bytes(16)

    reset.used_at = now
    await db.commit()

    return ResetPasswordResponse(ok=True, api_keys_wiped=int(wiped))


# ── helpers ──────────────────────────────────────────────────────────────


async def _lookup_invite(db: AsyncSession, token: str) -> Invitation:
    inv = (
        await db.execute(
            select(Invitation).where(Invitation.token_hash == hash_token(token))
        )
    ).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if inv is None or inv.accepted_at is not None or inv.expires_at < now:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired invitation",
        )
    return inv
