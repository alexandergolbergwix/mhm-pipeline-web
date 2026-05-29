"""phase4: runs + run_records + authority_matches

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-29
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False, index=True,
        ),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("record_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("match_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "run_records",
        sa.Column(
            "run_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("runs.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("control_number", sa.String(length=64), primary_key=True),
        sa.Column("marc", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    )

    op.create_table(
        "authority_matches",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("runs.id", ondelete="CASCADE"),
            nullable=False, index=True,
        ),
        sa.Column("control_number", sa.String(length=64), nullable=False, index=True),
        sa.Column("entity_text", sa.Text(), nullable=False),
        sa.Column("entity_kind", sa.String(length=32), nullable=False, server_default="person"),
        sa.Column("role", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("matched_name", sa.Text(), nullable=False, server_default=""),
        sa.Column("mazal_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("viaf_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("wikidata_qid", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("confidence", sa.String(length=16), nullable=False, server_default="low"),
        sa.Column("source", sa.String(length=32), nullable=False, server_default=""),
        sa.Column(
            "payload", postgresql.JSONB(astext_type=sa.Text()),
            nullable=False, server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("approved", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "approved_by", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("authority_matches")
    op.drop_table("run_records")
    op.drop_table("runs")
