"""Org-wide invitations.

An admin creates an invitation for an email + role; the system emits a
one-time token (the *plaintext* token never lands in the DB — we store
:func:`hashlib.sha256` of it, the same pattern session cookies could use
in a stricter mode). The invitee clicks the link, sets a password,
becomes a regular user. The token row is then ``accepted_at``-stamped
so it can't be reused.

Project-level invites land in Phase 3 alongside ``memberships``.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, LargeBinary, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, _new_uuid


class Invitation(Base):
    __tablename__ = "invitations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_new_uuid,
    )

    # PII — same blind-index + AES-GCM treatment as users.email so a DB
    # dump can't reveal who was invited.
    email_index: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False, index=True)
    email_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)

    role: Mapped[str] = mapped_column(String(16), nullable=False)

    # Token: random URL-safe 32-byte value mailed to the invitee. We
    # only persist its SHA-256, so a DB leak doesn't expose pending
    # invite links.
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False, unique=True)

    invited_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
