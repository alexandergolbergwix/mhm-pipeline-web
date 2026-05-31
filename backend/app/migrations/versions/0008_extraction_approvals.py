"""extraction: per-entity approvals + overrides + AI verdict snapshot

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-31
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "extraction_approvals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("control_number", sa.String(length=64), nullable=False),
        sa.Column("source",         sa.String(length=32), nullable=False),
        sa.Column("text",           sa.String(length=512), nullable=False),
        sa.Column("start", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("end",   sa.Integer(), nullable=False, server_default="0"),

        # Original prediction snapshot
        sa.Column("type", sa.String(length=64), nullable=True),
        sa.Column("role", sa.String(length=64), nullable=True),
        sa.Column("confidence",       sa.Float(), nullable=True),
        sa.Column("model_confidence", sa.Float(), nullable=True),

        # Curator overrides
        sa.Column("override_type", sa.String(length=64), nullable=True),
        sa.Column("override_role", sa.String(length=64), nullable=True),

        # Approval state
        sa.Column("approved",    sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
        sa.Column(
            "approved_by", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),

        # Latest AI verdict
        sa.Column("ai_verdict",
                  postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("ai_verdict_at", sa.DateTime(timezone=True), nullable=True),

        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),

        sa.UniqueConstraint(
            "run_id", "control_number", "source", "text", "start", "end",
            name="uq_extraction_approval_key",
        ),
    )
    op.create_index(
        "ix_extraction_approval_run",
        "extraction_approvals", ["run_id"],
    )
    op.create_index(
        "ix_extraction_approval_run_source",
        "extraction_approvals", ["run_id", "source"],
    )


def downgrade() -> None:
    op.drop_index("ix_extraction_approval_run_source", table_name="extraction_approvals")
    op.drop_index("ix_extraction_approval_run",        table_name="extraction_approvals")
    op.drop_table("extraction_approvals")
