"""Pydantic request / response shapes for the public access-request flow.

The access-request workflow lets prospective collaborators ask for an
account. The endpoints behind these schemas deliberately give nothing
away about whether a given email already has an account — every
submission returns the same generic 202 body (see
:class:`AccessRequestSubmitResponse`). Admin-only schemas surface the
full review surface.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field


# ── Public submission ──────────────────────────────────────────────────


class AccessRequestCreateRequest(BaseModel):
    """Payload posted to ``POST /api/access-request``.

    The ``website`` field is a honeypot — real users never see or fill
    it; bots that auto-fill every input get rejected silently. The
    ``turnstile_token`` is the Cloudflare Turnstile challenge proof and
    is verified server-side before any DB write.
    """

    email: EmailStr
    name: str = Field(min_length=2, max_length=120)
    affiliation: str = Field(min_length=2, max_length=200)
    justification: str = Field(min_length=40, max_length=2000)
    website: str = Field(default="", max_length=200)
    turnstile_token: str


class AccessRequestSubmitResponse(BaseModel):
    """Generic 202 body returned regardless of outcome.

    Strict OWASP anti-enumeration: the response shape and content are
    identical whether the email is new, already has an account, or hit
    a rate limit. Side effects (notice email to existing accounts,
    double-opt-in to new requests) happen out-of-band.
    """

    message: str = "If your email is eligible, you'll receive next steps shortly."


# ── Double opt-in confirmation ─────────────────────────────────────────


class ConfirmResponse(BaseModel):
    """Result of clicking the confirmation link in the opt-in email."""

    message: str
    status: Literal["confirmed", "expired", "already_used"]


# ── Admin review surface ───────────────────────────────────────────────


class AccessRequestListItem(BaseModel):
    """Row in the admin's access-request queue listing."""

    id: uuid.UUID
    email: str
    name: str
    affiliation: str
    status: str
    created_at: datetime
    confirmed_at: datetime | None
    reviewed_at: datetime | None
    reviewed_by_email: str | None
    denial_reason: str | None


class AccessRequestDetail(AccessRequestListItem):
    """Full record including the free-text justification and request
    metadata. Surfaced only to admins on the detail view."""

    justification: str
    client_ip: str
    user_agent: str


class AccessRequestDenyRequest(BaseModel):
    """Admin payload when rejecting a request. The reason is included in
    the denial email the requester receives."""

    reason: str = Field(min_length=1, max_length=500)
