"""Add worker-claim column and active-job unique index to run_jobs."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0030_run_job_claims"
down_revision = "0029_wikibase_user_access"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("run_jobs", sa.Column("claimed_by", sa.Text(), nullable=True))
    op.create_index(
        "uq_run_jobs_active_kind",
        "run_jobs",
        ["run_id", "kind"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )


def downgrade() -> None:
    op.drop_index("uq_run_jobs_active_kind", table_name="run_jobs")
    op.drop_column("run_jobs", "claimed_by")
