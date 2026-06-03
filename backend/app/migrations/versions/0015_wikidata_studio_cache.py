"""wikidata_studio_cache: per-run build cache with input fingerprint

Revision ID: 0015_wikidata_studio_cache
Revises: 0014_extraction_override_text
Create Date: 2026-06-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0015_wikidata_studio_cache"
down_revision = "0014_extraction_override_text"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "wikidata_studio_cache",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("approved_only", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("input_fingerprint", sa.CHAR(64), nullable=False),
        sa.Column("result_items", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("quickstatements", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("summary", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("approved_match_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("pending_match_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("used_match_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("record_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("built_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_wikidata_studio_cache_run_id", "wikidata_studio_cache", ["run_id"])
    op.create_unique_constraint(
        "uq_wikidata_studio_cache_run_mode",
        "wikidata_studio_cache",
        ["run_id", "approved_only"],
    )


def downgrade() -> None:
    op.drop_table("wikidata_studio_cache")
