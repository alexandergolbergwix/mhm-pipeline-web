"""Add ai_verdict columns to wikidata_item_overrides."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0033_wikidata_override_ai_verdict"
down_revision = "0032_hmo_coverage_cache"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "wikidata_item_overrides",
        sa.Column(
            "ai_verdict",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "wikidata_item_overrides",
        sa.Column("ai_verdict_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("wikidata_item_overrides", "ai_verdict_at")
    op.drop_column("wikidata_item_overrides", "ai_verdict")
