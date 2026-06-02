"""Self-service access requests.

A prospective user fills out the public request-access form. The submission
lands here in ``pending_email_confirm`` until the requester clicks the
confirmation link (double opt-in); the row then advances to
``pending_admin`` where an admin approves or denies it via a one-time
decision token. Approval creates an :class:`Invitation` (existing primitive)
so the requester completes signup through the standard flow.

All PII (email, name, affiliation, justification) is treated the same way
as :class:`Invitation` — blind-indexed HMAC for lookup, AES-GCM encrypted
at rest. The plaintext confirm/decision tokens never land in the DB; we
store only their SHA-256 digests, mirroring :class:`Invitation.token_hash`.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, LargeBinary, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, _new_uuid

STATUS_PENDING_EMAIL_CONFIRM = "pending_email_confirm"
STATUS_PENDING_ADMIN = "pending_admin"
STATUS_APPROVED = "approved"
STATUS_DENIED = "denied"


class AccessRequest(Base):
    __tablename__ = "access_requests"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_new_uuid,
    )

    # PII — blind-index + AES-GCM, same treatment as users.email /
    # invitations.email so a DB dump can't reveal who applied.
    email_index: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False, index=True)
    email_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    name_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    affiliation_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    justification_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)

    status: Mapped[str] = mapped_column(String(32), nullable=False)

    # Double opt-in confirmation token: random URL-safe value mailed to
    # the requester. We persist only its SHA-256 so a DB leak can't be
    # used to bypass email verification.
    confirm_token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    confirm_token_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    # Admin decision token: one-time approve/deny link mailed to admins
    # once the request reaches ``pending_admin``. Same hash-only pattern.
    decision_token_hash: Mapped[bytes | None] = mapped_column(
        LargeBinary(32), nullable=True,
    )
    decision_token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    denial_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Audit / abuse signals — not PII in the GDPR sense but kept for
    # rate-limit forensics and spam triage.
    client_ip: Mapped[str] = mapped_column(String(45), nullable=False)
    user_agent: Mapped[str] = mapped_column(String(512), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
