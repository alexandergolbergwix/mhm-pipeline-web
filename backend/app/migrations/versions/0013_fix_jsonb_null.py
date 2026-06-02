"""fix_jsonb_null: convert JSON literal 'null' to SQL NULL on project_events

Revision ID: 0013_fix_jsonb_null
Revises: 0012_entity_versioning
Create Date: 2026-06-02

The ``project_events.state`` and ``project_events.patch`` columns were
declared as ``JSONB`` without ``none_as_null=True``. SQLAlchemy's default
JSONB ``none_as_null=False`` serialises a Python ``None`` to the JSON
literal ``"null"`` rather than SQL ``NULL``. That broke the replay logic
in ``app.versioning.core._latest_state_event``, which uses
``where(ProjectEvent.state.isnot(None))`` to find the latest snapshot or
create event for an entity: the predicate matched every row, including
``op="patch"`` rows that should have had ``state IS NULL``, so the fold
picked the wrong "latest state event" and replayed state as ``{}``.

The model declarations were corrected to ``JSONB(none_as_null=True)``
(see ``app/models/event.py`` and ``app/models/entity_snapshot.py``); this
migration fixes the data that was written under the broken contract by
rewriting every row whose ``state`` or ``patch`` is the JSON literal
``"null"`` to a proper SQL ``NULL``.

Idempotent: re-running the UPDATEs is a no-op once every JSON-literal
null has been converted. ``WHERE state::text = 'null'`` only matches
JSON-literal-null rows; rows that are already SQL NULL or carry real
JSON objects/arrays are left untouched.

Downgrade is a no-op — re-writing SQL NULL back to the JSON literal
``"null"`` would re-introduce the bug.
"""
from __future__ import annotations

from alembic import op

revision: str = "0013_fix_jsonb_null"
down_revision: str | None = "0012_entity_versioning"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Convert any JSON-literal 'null' values that were persisted by the
    # pre-fix SQLAlchemy default (none_as_null=False) into proper SQL
    # NULLs, so the .isnot(None) predicates in app.versioning.core filter
    # them out correctly.
    op.execute(
        "UPDATE project_events SET state = NULL WHERE state::text = 'null'"
    )
    op.execute(
        "UPDATE project_events SET patch = NULL WHERE patch::text = 'null'"
    )


def downgrade() -> None:
    # Forward-only data fix — rewriting SQL NULL back to the JSON literal
    # 'null' would re-introduce the replay bug.
    return None
