"""Per-curator rule-verification settings: blocking rules + filter presets."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class UserRuleSettings(Base):
    __tablename__ = "user_rule_settings"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    # {rule_id: true} — rules the curator chose to make blocking for bulk
    # approval. Empty by default: nothing blocks automatically (advisory
    # contract, eval-agent R13 parity).
    blocked_rules: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # Saved smart-filter presets for the rule verification panel.
    filter_presets: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSONB, nullable=True, default=None,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
