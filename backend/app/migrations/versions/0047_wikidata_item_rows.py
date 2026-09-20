"""wikidata_studio_item_rows — per-item review read-model for cursor pagination."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = "0047_wikidata_item_rows"
down_revision = "0046_hmo_item_rows"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "wikidata_studio_item_rows",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", pg.UUID(as_uuid=True), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("approved_only", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("source", sa.String(16), nullable=False, server_default="legacy"),
        sa.Column("local_id", sa.String(256), nullable=False),
        sa.Column("ord", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("payload", pg.JSONB(), nullable=False, server_default="{}"),
        sa.Column("label_en", sa.String(600), nullable=True),
        sa.Column("label_he", sa.String(600), nullable=True),
        sa.Column("label_sort", sa.String(650), nullable=False, server_default=""),
        sa.Column("entity_type", sa.String(64), nullable=False, server_default=""),
        sa.Column("existing_qid", sa.String(32), nullable=False, server_default=""),
        sa.Column("source_uri", sa.String(600), nullable=False, server_default=""),
        sa.Column("record_ids", pg.JSONB(), nullable=False, server_default="[]"),
        sa.Column("statement_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "run_id", "approved_only", "source", "local_id",
            name="uq_wd_item_row_run_local",
        ),
    )
    op.create_index(
        "ix_wd_item_rows_run_sort",
        "wikidata_studio_item_rows",
        ["run_id", "approved_only", "source", "label_sort", "local_id"],
    )
    op.create_index(
        "ix_wd_item_rows_run_ord",
        "wikidata_studio_item_rows",
        ["run_id", "approved_only", "source", "ord"],
    )


def downgrade() -> None:
    op.drop_table("wikidata_studio_item_rows")
