"""Preserve explicit extraction rejection without reclassifying pending rows."""

import sqlalchemy as sa
from alembic import op

revision = "0053_extraction_rejection"
down_revision = "0052_research_agent_run_journal"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("extraction_approvals") as batch:
        batch.add_column(sa.Column("rejected", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.create_check_constraint("ck_extraction_review_exclusive", "NOT (approved AND rejected)")


def downgrade() -> None:
    with op.batch_alter_table("extraction_approvals") as batch:
        batch.drop_constraint("ck_extraction_review_exclusive", type_="check")
        batch.drop_column("rejected")
