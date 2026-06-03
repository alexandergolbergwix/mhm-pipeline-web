"""Pydantic shapes for the project-export router.

Three flavours of export sit on top of the same domain model:

* **Full-project bundle** — every entity-type's current read-model state
  in one JSON document, plus the run-list and the project descriptor.
* **Snapshot archive** — every row from ``entity_snapshot`` for a project
  (cold-tier ``3/day`` history that survives the ``1000-event`` prune).
* **History dump** — every row from ``project_events`` for the project,
  or just one entity's slice when ``entity_type`` + ``entity_id`` are
  supplied.

The schemas below are deliberately permissive (``Any`` for JSONB
columns) because the export's job is to round-trip what the database
already holds — not to re-validate it. Strong shapes live on the
write paths.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

# Mirrors ``app.models.event.ALL_ENTITY_TYPES`` so the router's
# ``entity_types`` query parameter can be validated against a closed
# set without importing the constants here.
ExportEntityType = Literal[
    "marc_record",
    "extraction_entity",
    "authority_match",
    "wikidata_override",
    "wikibase_item",
]


class ExportRunRow(BaseModel):
    """One row of the project's run-list, copied verbatim from ``runs``."""

    id: str
    name: str
    status: str
    record_count: int
    match_count: int
    error: str | None
    created_by_email: str | None
    created_at: datetime
    completed_at: datetime | None


class ExportMarcRecord(BaseModel):
    """One ``run_records`` row with its full MARC payload preserved."""

    run_id: str
    control_number: str
    marc: dict[str, Any] = Field(default_factory=dict)


class ExportExtractionEntity(BaseModel):
    """One ``extraction_approvals`` row plus the curator's email."""

    id: str
    run_id: str
    control_number: str
    source: str
    text: str
    start: int
    end: int
    type: str | None
    role: str | None
    confidence: float | None
    model_confidence: float | None
    override_type: str | None
    override_role: str | None
    override_text: str | None
    approved: bool
    approved_by_email: str | None
    approved_at: datetime | None
    ai_verdict: dict[str, Any] | None
    ai_verdict_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ExportAuthorityMatch(BaseModel):
    """One ``authority_matches`` row."""

    id: str
    run_id: str
    control_number: str
    entity_text: str
    entity_kind: str
    role: str
    matched_name: str
    mazal_id: str
    viaf_id: str
    wikidata_qid: str
    confidence: str
    source: str
    payload: dict[str, Any] = Field(default_factory=dict)
    approved: bool
    approved_by_email: str | None
    approved_at: datetime | None
    created_at: datetime


class ExportWikidataOverride(BaseModel):
    """One ``wikidata_item_overrides`` row."""

    id: str
    run_id: str
    local_id: str
    labels: dict[str, Any] = Field(default_factory=dict)
    descriptions: dict[str, Any] = Field(default_factory=dict)
    aliases: dict[str, Any] = Field(default_factory=dict)
    add_statements: list[dict[str, Any]] = Field(default_factory=list)
    remove_statements: list[int] = Field(default_factory=list)
    statement_edits: dict[str, Any] = Field(default_factory=dict)
    updated_by_email: str | None
    created_at: datetime
    updated_at: datetime


class ExportWikibaseItem(BaseModel):
    """Folded current state of one ``wikibase_item`` from the event log.

    There is no read-model table for Wikibase items — the event log
    itself is the record of what we wrote out. Each entry is the most
    recent ``state``/``patch``-folded snapshot at export time.
    """

    entity_id: str
    rev_no: int
    state: dict[str, Any] | None
    last_event_at: datetime
    last_actor_email: str | None


class ExportProjectBundle(BaseModel):
    """Top-level shape returned by the full-project export."""

    project_id: str
    project_name: str
    exported_at: datetime
    exported_by_email: str | None
    runs: list[ExportRunRow] = Field(default_factory=list)
    marc_records: list[ExportMarcRecord] = Field(default_factory=list)
    extraction_entities: list[ExportExtractionEntity] = Field(default_factory=list)
    authority_matches: list[ExportAuthorityMatch] = Field(default_factory=list)
    wikidata_overrides: list[ExportWikidataOverride] = Field(default_factory=list)
    wikibase_items: list[ExportWikibaseItem] = Field(default_factory=list)


class ExportSnapshotRow(BaseModel):
    """One ``entity_snapshot`` row carrying full state at that moment."""

    entity_type: str
    entity_id: str
    bucket: str  # YYYY-MM-DD
    slot: int
    rev_no: int
    state: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class ExportSnapshotsResponse(BaseModel):
    """Top-level shape returned by the snapshots export."""

    project_id: str
    exported_at: datetime
    snapshot_count: int
    snapshots: list[ExportSnapshotRow] = Field(default_factory=list)


class ExportHistoryEvent(BaseModel):
    """One ``project_events`` row, redacted for actor PII."""

    id: str
    entity_type: str | None
    entity_id: str | None
    rev_no: int | None
    parent_event_id: str | None
    op: str | None
    type: str
    patch: list[Any] | None
    state: dict[str, Any] | None
    payload: dict[str, Any] = Field(default_factory=dict)
    actor_email: str | None
    message: str | None
    created_at: datetime


class ExportHistoryResponse(BaseModel):
    """Top-level shape returned by the history export."""

    project_id: str
    exported_at: datetime
    entity_type: str | None
    entity_id: str | None
    event_count: int
    events: list[ExportHistoryEvent] = Field(default_factory=list)
