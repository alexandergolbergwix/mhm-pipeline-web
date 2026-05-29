"""Pydantic request / response shapes for auth + admin invite endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field


# ── Login + /me ─────────────────────────────────────────────────────────


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1)


class MeResponse(BaseModel):
    id: uuid.UUID
    email: EmailStr
    name: str
    role: Literal["admin", "editor"]


class LoginResponse(MeResponse):
    pass


# ── Invites (admin-only) ────────────────────────────────────────────────


class InviteCreateRequest(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=200)
    role: Literal["admin", "editor"] = "editor"


class InviteResponse(BaseModel):
    """Returned to the admin after issuing the invite. ``accept_url``
    contains the plaintext token — DO NOT echo it anywhere except the
    invitation email and the admin's response. It is never stored
    plaintext server-side (we only persist its SHA-256)."""

    id: uuid.UUID
    email: EmailStr
    name: str
    role: Literal["admin", "editor"]
    expires_at: datetime
    accept_url: str


class InviteListItem(BaseModel):
    id: uuid.UUID
    email: EmailStr
    role: Literal["admin", "editor"]
    expires_at: datetime
    accepted_at: datetime | None
    created_at: datetime


# ── Invite acceptance ───────────────────────────────────────────────────


class AcceptInviteRequest(BaseModel):
    token: str = Field(min_length=10)
    password: str = Field(min_length=8, max_length=200)


class InvitePreviewResponse(BaseModel):
    """What the accept-invite page renders before the invitee picks a
    password. Reveals the email so the invitee can confirm; reveals
    nothing about the inviter."""

    email: EmailStr
    name: str
    role: Literal["admin", "editor"]
    expires_at: datetime


# ── Password change / reset ─────────────────────────────────────────────


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=8, max_length=200)


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ForgotPasswordResponse(BaseModel):
    """Always returns ``ok`` — the server deliberately doesn't tell the
    caller whether the email exists, to prevent account enumeration."""

    ok: bool = True
    # Dev convenience only — populated when ``ENV != production`` so the
    # operator doesn't need a real SMTP server to test the flow.
    dev_reset_url: str | None = None


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=10)
    new_password: str = Field(min_length=8, max_length=200)


class ResetPasswordResponse(BaseModel):
    """Reset always wipes any stored API keys — surface that count so the
    UI can warn the user."""

    ok: bool = True
    api_keys_wiped: int
