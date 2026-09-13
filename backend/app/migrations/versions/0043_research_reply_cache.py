"""Research agent reply cache: reuse planner answers for identical questions."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision = "0043_research_reply_cache"
down_revision = "0042_research_agent"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "research_agent_reply_cache",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("text_hash", sa.String(length=64), nullable=False),
        sa.Column("answer_text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("project_id", "text_hash", name="uq_reply_cache_project_hash"),
    )
    op.create_index(
        "ix_research_agent_reply_cache_project_id",
        "research_agent_reply_cache",
        ["project_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_research_agent_reply_cache_project_id",
        table_name="research_agent_reply_cache",
    )
    op.drop_table("research_agent_reply_cache")
