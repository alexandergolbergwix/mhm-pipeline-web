"""rule-based verification storage (HMO first).

- hmo_studio_item_overrides + item_overrides gain ``rule_verdict`` /
  ``rule_verdict_at`` (mirrors the ai_verdict columns; advisory).
- new ``user_rule_settings``: per-curator blocking-rule set + filter
  presets for the rule-verification UI.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0045_rule_verify"
down_revision = "0044_authority_enriched_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "hmo_studio_item_overrides",
        sa.Column("rule_verdict", pg.JSONB(), nullable=True),
    )
    op.add_column(
        "hmo_studio_item_overrides",
        sa.Column("rule_verdict_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "wikidata_item_overrides",
        sa.Column("rule_verdict", pg.JSONB(), nullable=True),
    )
    op.add_column(
        "wikidata_item_overrides",
        sa.Column("rule_verdict_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "user_rule_settings",
        sa.Column(
            "user_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("blocked_rules", pg.JSONB(), nullable=False, server_default="{}"),
        sa.Column("filter_presets", pg.JSONB(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("user_rule_settings")
    op.drop_column("wikidata_item_overrides", "rule_verdict_at")
    op.drop_column("wikidata_item_overrides", "rule_verdict")
    op.drop_column("hmo_studio_item_overrides", "rule_verdict_at")
    op.drop_column("hmo_studio_item_overrides", "rule_verdict")
