"""Add ai_verdict columns to wikidata_item_overrides."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0033_wikidata_ai_verdict"
down_revision = "0032_hmo_coverage_cache"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return any(c["name"] == column for c in insp.get_columns(table))


def upgrade() -> None:
    if not _has_column("wikidata_item_overrides", "ai_verdict"):
        op.add_column(
            "wikidata_item_overrides",
            sa.Column(
                "ai_verdict",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=True,
            ),
        )
    if not _has_column("wikidata_item_overrides", "ai_verdict_at"):
        op.add_column(
            "wikidata_item_overrides",
            sa.Column("ai_verdict_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    if _has_column("wikidata_item_overrides", "ai_verdict_at"):
        op.drop_column("wikidata_item_overrides", "ai_verdict_at")
    if _has_column("wikidata_item_overrides", "ai_verdict"):
        op.drop_column("wikidata_item_overrides", "ai_verdict")
