"""Add per-item foreign-modify accept flags for Wikidata Studio."""

from alembic import op
import sqlalchemy as sa

revision = "0037_wikidata_accept_foreign"
down_revision = "0036_hmo_canonical_contract"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "wikidata_item_overrides",
        sa.Column("accept_foreign_modify", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "wikidata_item_overrides",
        sa.Column("accepted_foreign_qid", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("wikidata_item_overrides", "accepted_foreign_qid")
    op.drop_column("wikidata_item_overrides", "accept_foreign_modify")
