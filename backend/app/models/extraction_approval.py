"""Per-entity approval store for AI Extraction (AI Extraction).

One row per (run, control_number, source, text, start, end) tuple —
content-addressed, so re-running extraction on the same MARC input
re-attaches existing approvals (no silent wipe).

Beyond the approval bool, the row carries:

* ``override_type`` / ``override_role`` — curator-supplied corrections
  to the model's prediction. Downstream stages (Authority Enrichment authority +
  RDF Graph RDF) read these instead of the original prediction when
  present (per user decision: edits flow downstream).
* ``ai_verdict`` — the latest agent-judged verdict snapshot
  ({overall, reasoning, judged_at, action_id}). Written by the
  AI-verify flow when a session ends. Lets the table light up the
  "AI verdict" column without re-walking session dirs.

Filtering semantics (per user decision): unapproved entities are
hard-dropped from the ner_results.json that downstream stages
consume. Approval = "ship this in the final output".
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, _new_uuid


class ExtractionApproval(Base):
    """Approval + override + AI verdict for one extracted entity."""

    __tablename__ = "extraction_approvals"
    __table_args__ = (
        UniqueConstraint(
            "run_id", "control_number", "source", "text", "start", "end",
            name="uq_extraction_approval_key",
        ),
        Index("ix_extraction_approval_run", "run_id"),
        Index("ix_extraction_approval_run_source", "run_id", "source"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_new_uuid,
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("runs.id", ondelete="CASCADE"),
        nullable=False,
    )

    # ── Canonical entity key ──────────────────────────────────────────
    control_number: Mapped[str] = mapped_column(String(64), nullable=False)
    # "person_ner" | "provenance_ner" | "contents_ner" | "genre"
    source:         Mapped[str] = mapped_column(String(32), nullable=False)
    text:           Mapped[str] = mapped_column(String(512), nullable=False)
    # For genre predictions (no offset), start = end = 0.
    start:          Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    end:            Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # ── Original prediction (snapshotted at first PATCH) ──────────────
    type:           Mapped[str | None] = mapped_column(String(64), nullable=True)
    role:           Mapped[str | None] = mapped_column(String(64), nullable=True)
    confidence:     Mapped[float | None] = mapped_column(nullable=True)
    model_confidence: Mapped[float | None] = mapped_column(nullable=True)

    # ── Curator overrides (downstream-influencing) ────────────────────
    override_type:  Mapped[str | None] = mapped_column(String(64), nullable=True)
    override_role:  Mapped[str | None] = mapped_column(String(64), nullable=True)
    override_text:  Mapped[str | None] = mapped_column(String(512), nullable=True)

    # ── Approval state ─────────────────────────────────────────────────
    approved:       Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    approved_by:    Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    approved_at:    Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    # ── Latest AI agent verdict (snapshot) ────────────────────────────
    # Shape: {"overall": "pass"|"partial"|"fail"|"abstain",
    #         "reasoning": str, "judged_at": iso8601,
    #         "action_id": str, "session_id": str, "model": str}
    ai_verdict:     Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    ai_verdict_at:  Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
