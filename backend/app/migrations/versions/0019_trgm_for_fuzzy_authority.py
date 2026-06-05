"""Enable pg_trgm and add GIN trigram indexes on the authority name_index tables.

This enables fast fuzzy / proximity matching (similarity / % operator) for
Mazal and KIMA lookups as a fallback when the exact normalized_name misses.
The fuzzy logic lives in PostgresAuthorityBackend (threshold ~0.45 after our
aggressive niqqud/punct normalization).

The indexes are built on the already-imported data; on a busy dyno the
CREATE INDEX may take a few minutes but is non-blocking (CONCURRENTLY is
not used here because we are in a migration transaction and the table is
small-ish for KIMA; for Mazal 5 M rows it is acceptable during a release).

Revision ID: 0019_trgm_for_fuzzy_authority
Revises: 0018_authority_pg_tables
Create Date: 2026-06-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0019_trgm_for_fuzzy_authority"
down_revision = "0018_authority_pg_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Extension is usually available on Heroku Postgres; IF NOT EXISTS is safe.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")

    # GIN trigram indexes for % (similarity test) and similarity() function.
    # These are the only new objects; existing hash indexes for exact-match
    # remain and are preferred by the planner for exact queries.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_mazal_name_trgm "
        "ON mazal_name_index USING gin (normalized_name gin_trgm_ops);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_kima_name_trgm "
        "ON kima_name_index USING gin (normalized_name gin_trgm_ops);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_kima_name_trgm;")
    op.execute("DROP INDEX IF EXISTS idx_mazal_name_trgm;")
    # We do not drop the extension — it may be used by other things and
    # dropping extensions on Heroku can be surprising.
