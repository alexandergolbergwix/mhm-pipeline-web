"""Change ``exists_in`` on extraction_approvals from VARCHAR(16) to JSONB.

Migration 0020 added exists_in as VARCHAR(16) intending to store only the
status string. In practice ``_bulk_persist_entities`` stores the full
``MarcStructuredIndex.classify()`` dict (with status, fields, note) which
PostgreSQL rejects as "value too long", silently swallowed, so the column
stayed NULL for every entity and the MARC column showed "--" always.

This migration widens the column to JSONB so the full dict can be stored.
All existing rows have NULL (the failed inserts left them that way), so no
data conversion is needed.

Revision ID: 0021_exists_in_jsonb
Revises: 0020_extraction_exists_in
Create Date: 2026-06-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0021_exists_in_jsonb"
down_revision = "0020_extraction_exists_in"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "extraction_approvals",
        "exists_in",
        existing_type=sa.String(length=16),
        type_=postgresql.JSONB(astext_type=sa.Text()),
        existing_nullable=True,
        postgresql_using="NULL::jsonb",
    )


def downgrade() -> None:
    op.alter_column(
        "extraction_approvals",
        "exists_in",
        existing_type=postgresql.JSONB(astext_type=sa.Text()),
        type_=sa.String(length=16),
        existing_nullable=True,
        postgresql_using="NULL::character varying",
    )
