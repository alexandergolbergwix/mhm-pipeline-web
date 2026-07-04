"""Create wikibase_cloud_writes audit table."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0028_wikibase_cloud_writes"
down_revision = "0027_notify_broadcast_trim"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "wikibase_cloud_writes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("operation", sa.String(length=16), nullable=False),
        sa.Column("target_kind", sa.String(length=16), nullable=False),
        sa.Column("target_key", sa.Text(), nullable=False),
        sa.Column("wikibase_id", sa.String(length=64), nullable=True),
        sa.Column("outcome_message", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["job_id"], ["run_jobs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_wikibase_cloud_writes_actor_user_id",
        "wikibase_cloud_writes",
        ["actor_user_id"],
    )
    op.create_index(
        "ix_wikibase_cloud_writes_project_id",
        "wikibase_cloud_writes",
        ["project_id"],
    )
    op.create_index(
        "ix_wikibase_cloud_writes_run_id",
        "wikibase_cloud_writes",
        ["run_id"],
    )
    op.create_index(
        "ix_wikibase_cloud_writes_created_at",
        "wikibase_cloud_writes",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_wikibase_cloud_writes_created_at", table_name="wikibase_cloud_writes")
    op.drop_index("ix_wikibase_cloud_writes_run_id", table_name="wikibase_cloud_writes")
    op.drop_index("ix_wikibase_cloud_writes_project_id", table_name="wikibase_cloud_writes")
    op.drop_index("ix_wikibase_cloud_writes_actor_user_id", table_name="wikibase_cloud_writes")
    op.drop_table("wikibase_cloud_writes")
