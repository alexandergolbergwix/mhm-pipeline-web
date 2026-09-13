"""Research Assistant thread, canvas artifacts, and short-lived tool grants.

The Modal orchestrator never stores user wiki passwords. A grant row may
hold a master-key-wrapped copy of the wiki token for the grant TTL only.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, _new_uuid


class ResearchAgentThread(Base, TimestampMixin):
    __tablename__ = "research_agent_threads"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_new_uuid,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="Research session")
    messages: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    canvas_state: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class ResearchAgentReplyCache(Base, TimestampMixin):
    """Cached planner answers, keyed by (project, normalized question).

    Identical questions inside one project reuse the stored answer instead
    of spending another planner call. Only successful chat runs are cached.
    """
    __tablename__ = "research_agent_reply_cache"
    __table_args__ = (
        UniqueConstraint("project_id", "text_hash", name="uq_reply_cache_project_hash"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_new_uuid,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    text_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    answer_text: Mapped[str] = mapped_column(Text, nullable=False)


class ResearchAgentArtifact(Base, TimestampMixin):
    __tablename__ = "research_agent_artifacts"
    __table_args__ = (
        UniqueConstraint(
            "thread_id", "artifact_key", "version",
            name="uq_research_agent_artifact_version",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_new_uuid,
    )
    thread_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_agent_threads.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    artifact_key: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    content: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_by: Mapped[str] = mapped_column(String(16), nullable=False, default="agent")


class ResearchAgentGrant(Base):
    __tablename__ = "research_agent_grants"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_new_uuid,
    )
    thread_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_agent_threads.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="viewer")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    wiki_wrapped: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    wiki_nonce: Mapped[bytes | None] = mapped_column(LargeBinary(12), nullable=True)
    wiki_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
