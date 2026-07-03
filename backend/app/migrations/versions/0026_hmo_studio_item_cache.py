"""hmo_studio_item_cache: per-run resolved-item build cache

Revision ID: 0026_hmo_studio_item_cache
Revises: 0025_wikibase_entity_mappings
Create Date: 2026-07-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0026_hmo_studio_item_cache"
down_revision = "0025_wikibase_entity_mappings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hmo_studio_item_cache",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("input_fingerprint", sa.CHAR(64), nullable=False),
        sa.Column(
            "resolved_entities",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("entity_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "deferred_link_count", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "skipped_statement_count", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "built_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_unique_constraint(
        "uq_hmo_studio_item_cache_run", "hmo_studio_item_cache", ["run_id"]
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_hmo_studio_item_cache_run", "hmo_studio_item_cache", type_="unique"
    )
    op.drop_table("hmo_studio_item_cache")
