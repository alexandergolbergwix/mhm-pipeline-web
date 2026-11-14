"""Retain source-scoped choices with the item override and its history."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0055_source_review_choices"
down_revision = "0054_person_identity_choice"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "wikidata_item_overrides", sa.Column("source_review_choices", JSONB(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("wikidata_item_overrides", "source_review_choices")
