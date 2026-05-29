"""Per-user encrypted API key store (Gemini, Wikidata, Wikibase bot password).

Envelope encryption — see :mod:`app.crypto.secrets`. The wrapping key is
the user's *password-derived* KEK, which only exists for the duration of
an active session. Result: even a full server + DB compromise leaks
nothing about these secrets.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, LargeBinary, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ApiKey(Base):
    __tablename__ = "api_keys"
    __table_args__ = (UniqueConstraint("user_id", "key_name", name="uq_api_key_user_name"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    key_name: Mapped[str] = mapped_column(String(64), primary_key=True)

    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    ciphertext_nonce: Mapped[bytes] = mapped_column(LargeBinary(12), nullable=False)
    dek_wrapped: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    dek_wrap_nonce: Mapped[bytes] = mapped_column(LargeBinary(12), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
