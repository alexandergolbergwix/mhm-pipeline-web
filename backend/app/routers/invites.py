"""Admin invite router (``/api/admin/invites``)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.admin import require_admin
from app.auth.session import AuthContext
from app.auth.tokens import new_token
from app.crypto import index as idx
from app.crypto import pii
from app.db import get_session
from app.models.invitation import Invitation
from app.models.user import User
from app.schemas.auth import InviteCreateRequest, InviteListItem, InviteResponse
from app.settings import get_settings

router = APIRouter(prefix="/admin/invites", tags=["admin"])

INVITE_TTL_HOURS = 72


def _accept_url(token: str) -> str:
    base = get_settings().frontend_origin.rstrip("/")
    return f"{base}/accept-invite?token={token}"


@router.post("", response_model=InviteResponse, status_code=status.HTTP_201_CREATED)
async def create_invite(
    payload: InviteCreateRequest,
    admin: AuthContext = Depends(require_admin),  # noqa: ARG001 — gate
    db: AsyncSession = Depends(get_session),
) -> InviteResponse:
    email_norm = payload.email.lower()
    email_idx = idx.blind_index(email_norm)

    # Reject if a user with that email already exists.
    existing_user = (
        await db.execute(select(User).where(User.email_index == email_idx))
    ).scalar_one_or_none()
    if existing_user is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this email already exists",
        )

    # Reject if there's an outstanding (un-accepted, un-expired) invite.
    now = datetime.now(timezone.utc)
    pending = (
        await db.execute(
            select(Invitation).where(
                Invitation.email_index == email_idx,
                Invitation.accepted_at.is_(None),
                Invitation.expires_at > now,
            )
        )
    ).scalar_one_or_none()
    if pending is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An active invitation already exists for this email",
        )

    plaintext, token_hash = new_token()
    inv = Invitation(
        email_index=email_idx,
        email_encrypted=pii.encrypt_pii(email_norm),
        role=payload.role,
        token_hash=token_hash,
        invited_by=admin.user.id,
        expires_at=now + timedelta(hours=INVITE_TTL_HOURS),
    )
    # Stash the invitee's name on a private attribute so the response
    # below can echo it; the field is intentionally not part of the
    # Invitation model (name is set during accept based on what the
    # invitee enters).
    db.add(inv)
    await db.flush()
    await db.commit()

    return InviteResponse(
        id=inv.id,
        email=payload.email,
        name=payload.name,
        role=payload.role,
        expires_at=inv.expires_at,
        accept_url=_accept_url(plaintext),
    )


@router.get("", response_model=list[InviteListItem])
async def list_invites(
    admin: AuthContext = Depends(require_admin),  # noqa: ARG001 — gate
    db: AsyncSession = Depends(get_session),
) -> list[InviteListItem]:
    rows = (
        await db.execute(select(Invitation).order_by(Invitation.created_at.desc()))
    ).scalars().all()
    return [
        InviteListItem(
            id=r.id,
            email=pii.decrypt_pii(r.email_encrypted),
            role=r.role,
            expires_at=r.expires_at,
            accepted_at=r.accepted_at,
            created_at=r.created_at,
        )
        for r in rows
    ]


@router.delete("/{invite_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_invite(
    invite_id: str,
    admin: AuthContext = Depends(require_admin),  # noqa: ARG001 — gate
    db: AsyncSession = Depends(get_session),
) -> None:
    inv = (
        await db.execute(select(Invitation).where(Invitation.id == invite_id))
    ).scalar_one_or_none()
    if inv is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found")
    if inv.accepted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot revoke an already-accepted invitation",
        )
    await db.delete(inv)
    await db.commit()
