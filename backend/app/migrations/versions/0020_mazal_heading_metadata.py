"""Add main_marc_tag to mazal_authorities and a btree index on (entity_type,
normalized_name) in mazal_name_index for ordered homonym disambiguation.

Background: previously mazal_authorities had no column to distinguish
אישיות (tag 100) from נושא (tag 150) or כותר (tag 130). The bare LIMIT 1
query returned an arbitrary match when multiple rows shared the same
normalized name. Storing main_marc_tag lets the matcher ORDER BY
  CASE main_marc_tag WHEN '100' THEN 1 … END
and prefer the personality entry over a subject or work entry.

The Postgres tables are populated via import_mazal_to_postgres.py (Rule W-28).
This migration only changes the schema; a re-import with the updated script
is required before the new column has data (see Rule W-33 curator playbook).

Revision ID: 0020_mazal_heading_metadata
Revises: 0019_trgm_for_fuzzy_authority
Create Date: 2026-06-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0020_mazal_heading_metadata"
down_revision = "0019_trgm_for_fuzzy_authority"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add main_marc_tag to mazal_authorities.  Nullable so existing rows
    # (imported before this migration) remain valid until re-import fills them.
    op.add_column(
        "mazal_authorities",
        sa.Column("main_marc_tag", sa.Text, nullable=True),
    )

    # Btree composite index — supports the ORDER BY in match_person:
    #   ORDER BY CASE main_marc_tag WHEN '100' THEN 1 … END,
    #            (dates IS NOT NULL) DESC
    # Hash-only indexes (migrated in 0018) remain for exact-match fast path.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_mazal_auth_type_name "
        "ON mazal_name_index (entity_type, normalized_name);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_mazal_auth_type_name;")
    op.drop_column("mazal_authorities", "main_marc_tag")
