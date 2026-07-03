"""wikibase_entity_mappings: idempotency ledger for HMO Wikibase writes

Revision ID: 0025_wikibase_entity_mappings
Revises: 0024_run_jobs
Create Date: 2026-07-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0025_wikibase_entity_mappings"
down_revision = "0024_run_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "wikibase_entity_mappings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("ontology_uri", sa.Text(), nullable=False),
        sa.Column("entity_kind", sa.String(16), nullable=False),  # class|property|instance
        sa.Column("wikibase_id", sa.String(32), nullable=False),  # QID or PID
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("local_key", sa.Text(), nullable=True),
        sa.Column("datatype", sa.String(32), nullable=True),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_wikibase_entity_mappings_entity_kind",
        "wikibase_entity_mappings",
        ["entity_kind"],
    )
    op.create_index(
        "ix_wikibase_entity_mappings_run_id",
        "wikibase_entity_mappings",
        ["run_id"],
    )
    # Schema rows (run_id IS NULL) are unique per ontology URI globally.
    op.create_index(
        "uq_wikibase_entity_mappings_schema_uri",
        "wikibase_entity_mappings",
        ["ontology_uri"],
        unique=True,
        postgresql_where=sa.text("run_id IS NULL"),
    )
    # Instance rows (run_id set) are unique per (ontology_uri, run_id).
    op.create_index(
        "uq_wikibase_entity_mappings_instance_uri_run",
        "wikibase_entity_mappings",
        ["ontology_uri", "run_id"],
        unique=True,
        postgresql_where=sa.text("run_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_wikibase_entity_mappings_instance_uri_run",
        table_name="wikibase_entity_mappings",
    )
    op.drop_index(
        "uq_wikibase_entity_mappings_schema_uri",
        table_name="wikibase_entity_mappings",
    )
    op.drop_index("ix_wikibase_entity_mappings_run_id", table_name="wikibase_entity_mappings")
    op.drop_index(
        "ix_wikibase_entity_mappings_entity_kind", table_name="wikibase_entity_mappings"
    )
    op.drop_table("wikibase_entity_mappings")
