"""Add ``exists_in`` to extraction_approvals.

Stores the per-entity MARC grounding classification
("grounded" | "wrong_field" | "novel" | "unknown") computed once at
extraction stream-end. Previously ``list_entities`` rebuilt the full
``MarcStructuredIndex`` from every RunRecord and re-classified every
entity on *every* poll (every 2 s while a verify modal is open); the
result is deterministic, so it is now snapshotted here and read
straight from the row.

Nullable: rows written before this migration (or before a re-run that
recomputes it) have NULL and the endpoint falls back to "unknown".

Revision ID: 0020_extraction_exists_in
Revises: 0019_trgm_for_fuzzy_authority
Create Date: 2026-06-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0020_extraction_exists_in"
down_revision = "0019_trgm_for_fuzzy_authority"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "extraction_approvals",
        sa.Column("exists_in", sa.String(length=16), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("extraction_approvals", "exists_in")
