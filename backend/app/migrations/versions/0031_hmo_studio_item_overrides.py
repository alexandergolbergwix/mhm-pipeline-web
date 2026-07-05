"""hmo_studio_item_overrides + shacl_report on item cache."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0031_hmo_studio_item_overrides"
down_revision = "0030_run_job_claims"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hmo_studio_item_overrides",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("local_id", sa.String(256), nullable=False),
        sa.Column(
            "labels",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "descriptions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "aliases",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "add_statements",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "remove_statements",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "statement_edits",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("approved", sa.Boolean(), nullable=True),
        sa.Column("ai_verdict", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("ai_verdict_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
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
    op.create_unique_constraint(
        "uq_hmo_item_override_run_local",
        "hmo_studio_item_overrides",
        ["run_id", "local_id"],
    )
    op.create_index(
        "ix_hmo_studio_item_overrides_run_id",
        "hmo_studio_item_overrides",
        ["run_id"],
    )

    op.add_column(
        "hmo_studio_item_cache",
        sa.Column(
            "shacl_report",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("hmo_studio_item_cache", "shacl_report")
    op.drop_index("ix_hmo_studio_item_overrides_run_id", table_name="hmo_studio_item_overrides")
    op.drop_constraint(
        "uq_hmo_item_override_run_local", "hmo_studio_item_overrides", type_="unique",
    )
    op.drop_table("hmo_studio_item_overrides")
