"""Add rdf_triple_overrides table for curator edits to RDF literal values.

Revision ID: 0017_rdf_triple_overrides
Revises: 0016_item_override_approved
Create Date: 2026-06-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0017_rdf_triple_overrides"
down_revision = "0016_item_override_approved"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rdf_triple_overrides",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "run_id",
            UUID(as_uuid=True),
            sa.ForeignKey("runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("subject_uri", sa.Text, nullable=False),
        sa.Column("predicate_uri", sa.Text, nullable=False),
        sa.Column("new_value", sa.Text, nullable=False),
        sa.Column("new_datatype", sa.Text, nullable=True),
        sa.Column("new_lang", sa.Text, nullable=True),
        sa.Column("old_value", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "created_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_rdf_triple_overrides_run_subject",
        "rdf_triple_overrides",
        ["run_id", "subject_uri"],
    )


def downgrade() -> None:
    op.drop_index("ix_rdf_triple_overrides_run_subject", "rdf_triple_overrides")
    op.drop_table("rdf_triple_overrides")
