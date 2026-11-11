"""Preserve explicit extraction rejection without reclassifying pending rows."""

import sqlalchemy as sa
from alembic import op

revision = "0053_extraction_rejection"
down_revision = "0052_research_agent_run_journal"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "extraction_approvals",
        sa.Column("rejected", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_check_constraint(
        "ck_extraction_review_exclusive", "extraction_approvals", "NOT (approved AND rejected)"
    )


def downgrade() -> None:
    op.drop_constraint("ck_extraction_review_exclusive", "extraction_approvals", type_="check")
    op.drop_column("extraction_approvals", "rejected")
