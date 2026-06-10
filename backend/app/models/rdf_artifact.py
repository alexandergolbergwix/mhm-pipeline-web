from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class RdfArtifact(Base):
    __tablename__ = "rdf_artifacts"

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("runs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    ttl_content: Mapped[str] = mapped_column(Text, nullable=False)
    built_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    triples_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    manuscripts_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
