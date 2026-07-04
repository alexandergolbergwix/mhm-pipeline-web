"""Per-user Wikibase Cloud access granted on login / invite acceptance."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

WIKI_ACCOUNT_NONE = "none"
WIKI_ACCOUNT_PENDING = "pending"
WIKI_ACCOUNT_ACTIVE = "active"
WIKI_ACCOUNT_SKIPPED = "skipped"
WIKI_ACCOUNT_FAILED = "failed"


class WikibaseUserAccess(Base):
    __tablename__ = "wikibase_user_access"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    app_authorized: Mapped[bool] = mapped_column(nullable=False, default=False)
    wiki_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    wiki_account_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=WIKI_ACCOUNT_NONE,
    )
    wiki_account_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    authorized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    wiki_provisioned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False,
    )
