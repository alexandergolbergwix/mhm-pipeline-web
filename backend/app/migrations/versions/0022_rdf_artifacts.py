"""Persist RDF build output in Postgres for durability across Heroku deploys.

The RDF graph was previously written only to the local dyno filesystem
(backend/state/runs/{run_id}/manuscripts.ttl), which is wiped on every
deploy or dyno restart.  This migration adds rdf_artifacts to store the
serialised Turtle so that the graph survives restarts without needing an
external object-store addon.

Revision ID: 0022_rdf_artifacts
Revises: 0021_exists_in_jsonb
Create Date: 2026-06-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0022_rdf_artifacts"
down_revision = "0021_exists_in_jsonb"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rdf_artifacts",
        sa.Column("run_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ttl_content", sa.Text(), nullable=False),
        sa.Column(
            "built_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("triples_count", sa.Integer(), nullable=True),
        sa.Column("manuscripts_count", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("run_id"),
    )


def downgrade() -> None:
    op.drop_table("rdf_artifacts")
