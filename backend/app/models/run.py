"""Runs, MARC records, authority matches, approvals.

A *run* is one ingest of MARC data into a project. We persist:

* ``runs`` — name + status + who/when.
* ``run_records`` — the parsed MARC payload per record (control_number → JSONB).
* ``authority_matches`` — one row per (record, candidate-match), with the
  approval state the curators flip in the Review UI (Phase 5).

The actual ingest/match logic lives in :mod:`app.pipeline` and is
deliberately a thin interface so the real Mazal/VIAF/Wikidata adapters
from the desktop pipeline can be swapped in piece-by-piece.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, String, Text, func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, _new_uuid

RUN_STATUS_PENDING = "pending"
RUN_STATUS_RUNNING = "running"
RUN_STATUS_SUCCEEDED = "succeeded"
RUN_STATUS_FAILED = "failed"


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_new_uuid,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=RUN_STATUS_PENDING,
    )
    record_count: Mapped[int] = mapped_column(default=0, nullable=False)
    match_count: Mapped[int] = mapped_column(default=0, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )


class RunRecord(Base):
    __tablename__ = "run_records"

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("runs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    control_number: Mapped[str] = mapped_column(String(64), primary_key=True)
    marc: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class AuthorityMatch(Base):
    __tablename__ = "authority_matches"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_new_uuid,
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("runs.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    control_number: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # The candidate we extracted from the MARC record (a name string,
    # mostly). ``entity_kind`` distinguishes person/place/organisation.
    entity_text: Mapped[str] = mapped_column(Text, nullable=False)
    entity_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="person")
    role: Mapped[str] = mapped_column(String(64), nullable=False, default="")

    # What the authority resolver came back with. Any of mazal/viaf/
    # wikidata may be empty.
    matched_name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    mazal_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    viaf_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    wikidata_qid: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    confidence: Mapped[str] = mapped_column(String(16), nullable=False, default="low")
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="")

    # The full match payload — everything the resolver returned that
    # didn't fit the columns above. JSON so the Review UI can render
    # extra detail without schema churn each phase.
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    # Approval state.
    approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )


class RdfTripleOverride(Base):
    """Curator edit to a literal value in the RDF graph.

    Subject + predicate identify the triple. ``new_value`` replaces the
    literal during the next RDF build (applied before serialising).
    Overrides persist across rebuilds so the curator edit is preserved.
    """

    __tablename__ = "rdf_triple_overrides"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_new_uuid,
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("runs.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    subject_uri: Mapped[str] = mapped_column(Text, nullable=False)
    predicate_uri: Mapped[str] = mapped_column(Text, nullable=False)
    new_value: Mapped[str] = mapped_column(Text, nullable=False)
    new_datatype: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_lang: Mapped[str | None] = mapped_column(Text, nullable=True)
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
