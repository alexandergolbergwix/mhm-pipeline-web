"""Server-side session table — the zero-knowledge wrapping anchor.

Each row stores the user's KEK encrypted with a random ``session_secret``.
The ``session_secret`` is delivered to the browser as the second half of
an HTTP-only cookie (``<session_id>.<session_secret>``); the server can
only unwrap the KEK while the user's browser is presenting that cookie.
Logout / expiry deletes the row and the wrapped KEK becomes
unrecoverable.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, LargeBinary, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, _new_uuid


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_new_uuid,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Wrapped KEK + nonce. AES-256-GCM(session_secret, KEK).
    kek_wrapped: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    kek_wrap_nonce: Mapped[bytes] = mapped_column(LargeBinary(12), nullable=False)

    # Sliding expiry — updated on every authenticated request.
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
