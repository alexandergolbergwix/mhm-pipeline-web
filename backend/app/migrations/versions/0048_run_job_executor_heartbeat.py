"""run_jobs.executor_heartbeat_at — Modal-executor-only liveness signal (W-254)."""

import sqlalchemy as sa
from alembic import op

revision = "0048_run_job_executor_heartbeat"
down_revision = "0047_wikidata_item_rows"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "run_jobs",
        sa.Column("executor_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("run_jobs", "executor_heartbeat_at")
