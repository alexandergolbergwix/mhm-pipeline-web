"""Store a validated source-scoped choice of a public person identity."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0054_person_identity_choice"
down_revision = "0053_extraction_rejection"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "wikidata_item_overrides", sa.Column("person_identity_choice", JSONB(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("wikidata_item_overrides", "person_identity_choice")
