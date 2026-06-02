"""Pydantic shapes for the entity-versioned history router.

Mirrors the entity-event log defined in :mod:`app.models.event` and the
versioning core in :mod:`app.versioning`. Closed-set literals for
``entity_type`` and ``op`` match ``ALL_ENTITY_TYPES`` / ``ALL_OPS`` in
the model.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

EntityType = Literal[
    "marc_record",
    "extraction_entity",
    "authority_match",
    "wikidata_override",
    "wikibase_item",
]
EventOp = Literal["create", "patch", "revert", "snapshot"]


class EventRow(BaseModel):
    """One row of the entity event timeline."""

    id: str
    rev_no: int
    parent_event_id: str | None
    op: EventOp
    actor_email: str | None
    message: str
    created_at: datetime


class PatchOp(BaseModel):
    """One RFC 6902 JSON Patch operation."""

    model_config = ConfigDict(populate_by_name=True)

    op: str  # add | remove | replace | move | copy | test
    path: str
    value: Any | None = None
    from_: str | None = Field(default=None, alias="from")


class DiffPayload(BaseModel):
    """Side-by-side diff between two revisions of an entity."""

    patch: list[dict[str, Any]]
    before: Any | None
    after: Any | None


class SnapshotRow(BaseModel):
    """One archive-tier snapshot — bucketed by UTC date + slot."""

    bucket: str  # YYYY-MM-DD
    slot: int
    rev_no: int
    created_at: datetime


class RevertRequest(BaseModel):
    """POST body for ``/history/revert``."""

    entity_type: EntityType
    entity_id: str
    target_rev: int
    message: str = ""


class RevertResponse(BaseModel):
    """Response shape for a successful revert."""

    ok: bool
    new_event_id: str
    new_rev_no: int
