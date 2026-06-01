"""Admin invite router (``/api/admin/invites``)."""

from __future__ import annotations

import uuid
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


async def create_invitation_for_email(
    db: AsyncSession,
    *,
    email: str,
    role: str,
    invited_by: uuid.UUID,
    bypass_duplicate_checks: bool = False,
) -> tuple[Invitation, str]:
    """Mint an invitation + return (row, accept_url).

    Used by both /api/admin/invites AND the approve-access-request path
    (which already validated the email when the request was submitted).

    When ``bypass_duplicate_checks=True`` the existing-user and
    pending-invite 409 checks are skipped — the caller has already
    asserted the email is eligible for invitation.
    """
    email_norm = email.lower()
    email_idx = idx.blind_index(email_norm)
    now = datetime.now(timezone.utc)

    if not bypass_duplicate_checks:
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
        role=role,
        token_hash=token_hash,
        invited_by=invited_by,
        expires_at=now + timedelta(hours=INVITE_TTL_HOURS),
    )
    db.add(inv)
    await db.flush()
    await db.commit()

    return inv, _accept_url(plaintext)


@router.post("", response_model=InviteResponse, status_code=status.HTTP_201_CREATED)
async def create_invite(
    payload: InviteCreateRequest,
    admin: AuthContext = Depends(require_admin),  # noqa: ARG001 — gate
    db: AsyncSession = Depends(get_session),
) -> InviteResponse:
    inv, accept_url = await create_invitation_for_email(
        db,
        email=payload.email,
        role=payload.role,
        invited_by=admin.user.id,
    )
    return InviteResponse(
        id=inv.id,
        email=payload.email,
        name=payload.name,
        role=payload.role,
        expires_at=inv.expires_at,
        accept_url=accept_url,
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
