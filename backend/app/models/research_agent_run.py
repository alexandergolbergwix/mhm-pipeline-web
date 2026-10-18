"""Run journal for the remote research agent (P1 observability).

One row per AG-UI run; ``stages`` is an append-only list of
``{stage, at, ok, code, detail}`` entries so "the run stopped responding"
becomes a diagnosis (gate → dispatch → model → tool → relay → sse) instead
of a guess. Rule W-274.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ResearchAgentRunJournal(Base):
    __tablename__ = "research_agent_run_journal"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    thread_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    stages: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    terminal_code: Mapped[str | None] = mapped_column(String(48), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
