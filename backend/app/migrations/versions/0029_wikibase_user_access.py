"""Create wikibase_user_access table for per-user Wikibase authorization."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0029_wikibase_user_access"
down_revision = "0028_wikibase_cloud_writes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "wikibase_user_access",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("app_authorized", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("wiki_username", sa.String(length=255), nullable=True),
        sa.Column("wiki_account_status", sa.String(length=16), nullable=False, server_default="none"),
        sa.Column("wiki_account_error", sa.Text(), nullable=True),
        sa.Column("authorized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("wiki_provisioned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade() -> None:
    op.drop_table("wikibase_user_access")
