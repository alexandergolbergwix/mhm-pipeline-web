"""extraction_override_text: curator text override on extraction_approvals

Revision ID: 0014_extraction_override_text
Revises: 0013_fix_jsonb_null
Create Date: 2026-06-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014_extraction_override_text"
down_revision = "0013_fix_jsonb_null"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "extraction_approvals",
        sa.Column("override_text", sa.String(length=512), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("extraction_approvals", "override_text")
