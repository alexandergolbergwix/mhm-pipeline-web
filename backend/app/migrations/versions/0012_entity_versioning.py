"""entity_versioning: per-entity revisions on project_events + entity_snapshot

Revision ID: 0012_entity_versioning
Revises: 0011
Create Date: 2026-06-02

Hybrid event-sourcing schema. We piggyback per-entity revisions on the
existing ``project_events`` log (new nullable columns — legacy rows stay
valid, new rows fill them in) and add an ``entity_snapshot`` table that
stores three daily slot snapshots per entity (00:00 / 08:00 / 16:00 UTC)
forever, so timeline reconstruction never has to replay more than ~8 h
of patches.

Closed sets (validated in app code, not as DB CHECK constraints so they
can evolve without a migration):

- ``entity_type`` ∈ {marc_record, extraction_entity, authority_match,
  wikidata_override, wikibase_item}
- ``op`` ∈ {create, patch, revert, snapshot}

Patches are RFC 6902 JSON Patch arrays (``jsonpatch>=1.33``). ``state``
carries the full entity payload on ``create`` and ``snapshot`` ops so
the latest-state lookup is one row, not a replay.

Downgrade is a no-op. We never roll back event-log schemas in prod —
the new columns are nullable and the new table is additive, so the
forward-only stance is safe.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012_entity_versioning"
down_revision: str | None = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1-8: per-entity revision columns on the existing event log.
    op.add_column(
        "project_events",
        sa.Column("entity_type", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "project_events",
        sa.Column("entity_id", sa.Text(), nullable=True),
    )
    op.add_column(
        "project_events",
        sa.Column("rev_no", sa.Integer(), nullable=True),
    )
    op.add_column(
        "project_events",
        sa.Column(
            "parent_event_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("project_events.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "project_events",
        sa.Column("op", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "project_events",
        sa.Column(
            "patch",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "project_events",
        sa.Column(
            "state",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "project_events",
        sa.Column("message", sa.Text(), nullable=True),
    )

    # 9: lookup index — latest revision per entity first.
    op.create_index(
        "ix_project_events_entity",
        "project_events",
        ["entity_type", "entity_id", sa.text("rev_no DESC")],
    )

    # 10: monotonic-rev uniqueness, only when the row is a versioned event.
    op.create_index(
        "ux_project_events_entity_rev",
        "project_events",
        ["entity_type", "entity_id", "rev_no"],
        unique=True,
        postgresql_where=sa.text(
            "entity_type IS NOT NULL "
            "AND entity_id IS NOT NULL "
            "AND rev_no IS NOT NULL"
        ),
    )

    # 11: snapshot table — three rolling slots per entity per UTC day.
    op.create_table(
        "entity_snapshots",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            # UUID generation is Python-side (`default=_new_uuid` on the
            # model). `_new_uuid` is NOT a Postgres function, so don't
            # emit it as a SQL DEFAULT — the model layer fills the column
            # at insert time.
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("entity_id", sa.Text(), nullable=False),
        sa.Column("bucket", sa.Date(), nullable=False),
        sa.Column("slot", sa.SmallInteger(), nullable=False),
        sa.Column("rev_no", sa.Integer(), nullable=False),
        sa.Column(
            "state",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    # 12: one snapshot per (entity, day, slot).
    op.create_index(
        "ux_entity_snapshot_slot",
        "entity_snapshots",
        ["entity_type", "entity_id", "bucket", "slot"],
        unique=True,
    )

    # 13: timeline-replay lookup — newest day + slot first inside a project.
    op.create_index(
        "ix_entity_snapshot_timeline",
        "entity_snapshots",
        [
            "project_id",
            "entity_type",
            "entity_id",
            sa.text("bucket DESC"),
            sa.text("slot DESC"),
        ],
    )


def downgrade() -> None:
    # Forward-only event-log schema; downgrade intentionally does nothing.
    return None
