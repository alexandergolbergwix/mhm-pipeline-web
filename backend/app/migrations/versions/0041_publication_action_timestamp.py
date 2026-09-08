"""Add the timestamp that the execution action repository requires."""

import sqlalchemy as sa
from alembic import op

revision = "0041_publication_action_time"
down_revision = "0040_publication_core"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "publication_execution_actions",
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("publication_execution_actions", "updated_at")
