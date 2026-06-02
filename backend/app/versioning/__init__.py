"""Versioning package — entity-scoped append-only event log.

Public API re-exported here; see :mod:`app.versioning.core` for the
implementation. Every state-changing curator decision routes through
``apply_event()`` BEFORE the caller updates the read-model. The caller
commits both writes in the same SQLAlchemy transaction.
"""

from __future__ import annotations

from app.models.event import (
    ENTITY_TYPE_AUTHORITY_MATCH,
    ENTITY_TYPE_EXTRACTION_ENTITY,
    ENTITY_TYPE_MARC_RECORD,
    ENTITY_TYPE_WIKIBASE_ITEM,
    ENTITY_TYPE_WIKIDATA_OVERRIDE,
    OP_CREATE,
    OP_PATCH,
    OP_REVERT,
    OP_SNAPSHOT,
)

from app.versioning.core import (
    apply_event,
    current_state,
    diff_revs,
    event_timeline,
    revert_to_rev,
    state_at_rev,
)

__all__ = [
    "apply_event",
    "current_state",
    "state_at_rev",
    "diff_revs",
    "revert_to_rev",
    "event_timeline",
    "OP_CREATE",
    "OP_PATCH",
    "OP_REVERT",
    "OP_SNAPSHOT",
    "ENTITY_TYPE_MARC_RECORD",
    "ENTITY_TYPE_EXTRACTION_ENTITY",
    "ENTITY_TYPE_AUTHORITY_MATCH",
    "ENTITY_TYPE_WIKIDATA_OVERRIDE",
    "ENTITY_TYPE_WIKIBASE_ITEM",
]
