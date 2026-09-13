"""Research Assistant threads, canvas artifacts, and tool grants."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision = "0042_research_agent"
down_revision = "0041_publication_action_time"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "research_agent_threads",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(200), nullable=False, server_default="Research session"),
        sa.Column("messages", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("canvas_state", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_research_agent_threads_project_id", "research_agent_threads", ["project_id"])
    op.create_index("ix_research_agent_threads_run_id", "research_agent_threads", ["run_id"])
    op.create_index("ix_research_agent_threads_user_id", "research_agent_threads", ["user_id"])

    op.create_table(
        "research_agent_artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "thread_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("research_agent_threads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("artifact_key", sa.String(120), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("title", sa.String(200), nullable=False, server_default=""),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("content", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_by", sa.String(16), nullable=False, server_default="agent"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "thread_id", "artifact_key", "version",
            name="uq_research_agent_artifact_version",
        ),
    )
    op.create_index(
        "ix_research_agent_artifacts_thread_id",
        "research_agent_artifacts",
        ["thread_id"],
    )

    op.create_table(
        "research_agent_grants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "thread_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("research_agent_threads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("role", sa.String(32), nullable=False, server_default="viewer"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("wiki_wrapped", sa.LargeBinary(), nullable=True),
        sa.Column("wiki_nonce", sa.LargeBinary(12), nullable=True),
        sa.Column("wiki_source", sa.String(32), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
    )
    op.create_index(
        "ix_research_agent_grants_thread_id",
        "research_agent_grants",
        ["thread_id"],
    )
    op.create_index(
        "ix_research_agent_grants_user_id",
        "research_agent_grants",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_research_agent_grants_user_id", table_name="research_agent_grants")
    op.drop_index("ix_research_agent_grants_thread_id", table_name="research_agent_grants")
    op.drop_table("research_agent_grants")
    op.drop_index("ix_research_agent_artifacts_thread_id", table_name="research_agent_artifacts")
    op.drop_table("research_agent_artifacts")
    op.drop_index("ix_research_agent_threads_user_id", table_name="research_agent_threads")
    op.drop_index("ix_research_agent_threads_run_id", table_name="research_agent_threads")
    op.drop_index("ix_research_agent_threads_project_id", table_name="research_agent_threads")
    op.drop_table("research_agent_threads")
