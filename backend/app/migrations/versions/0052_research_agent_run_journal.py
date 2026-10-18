"""research_agent_run_journal — per-run observability for the remote agent."""

import sqlalchemy as sa
from alembic import op

revision = "0052_research_agent_run_journal"
down_revision = "0051_plan_reconcile_method"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "research_agent_run_journal",
        sa.Column("run_id", sa.String(length=64), primary_key=True),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("thread_id", sa.UUID(), nullable=True),
        sa.Column("stages", sa.JSON(), nullable=False),
        sa.Column("terminal_code", sa.String(length=48), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_research_agent_run_journal_project_id",
        "research_agent_run_journal",
        ["project_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_research_agent_run_journal_project_id",
                  table_name="research_agent_run_journal")
    op.drop_table("research_agent_run_journal")
