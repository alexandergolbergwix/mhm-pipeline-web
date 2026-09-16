"""authority_matches.enriched_at — per-entity enrichment timestamp (R33).

Lets a re-run skip entities whose enrichment is still fresh instead of
re-matching all 5k+ entities on every attempt (Rule W-241).
"""

from alembic import op
import sqlalchemy as sa

revision = "0044_authority_enriched_at"
down_revision = "0043_research_reply_cache"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "authority_matches",
        sa.Column("enriched_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("authority_matches", "enriched_at")
