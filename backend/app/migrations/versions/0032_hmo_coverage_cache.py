"""Persist HMO coverage report build cache in Postgres for durability.

The HMO -> Wikidata projection-coverage report was previously cached only
on the dyno's local filesystem (backend/state/runs/{run_id}/
hmo_projection_coverage.json), which every Heroku deploy or dyno restart
wipes. That forced a full rebuild (9-14 minutes on a large corpus,
observed repeatedly against the same run) every time the dyno cycled,
even though the underlying RDF graph had not changed.

Revision ID: 0032_hmo_coverage_cache
Revises: 0031_hmo_studio_item_overrides
Create Date: 2026-07-06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0032_hmo_coverage_cache"
down_revision = "0031_hmo_studio_item_overrides"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hmo_coverage_cache",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("input_fingerprint", sa.CHAR(64), nullable=False),
        sa.Column(
            "report",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "built_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_hmo_coverage_cache_run_id", "hmo_coverage_cache", ["run_id"],
    )
    op.create_unique_constraint(
        "uq_hmo_coverage_cache_run", "hmo_coverage_cache", ["run_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_hmo_coverage_cache_run", "hmo_coverage_cache", type_="unique")
    op.drop_index("ix_hmo_coverage_cache_run_id", table_name="hmo_coverage_cache")
    op.drop_table("hmo_coverage_cache")
