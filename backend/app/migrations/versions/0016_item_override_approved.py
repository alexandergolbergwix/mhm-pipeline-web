"""item_override approved: curator item-level approval flag

Adds a nullable Boolean column ``approved`` to ``wikidata_item_overrides``.
None = not yet reviewed; True = approved for QS/upload export;
False = explicitly rejected. Independent of the authority-match
``approved`` column.

Revision ID: 0016_item_override_approved
Revises: 0015_wikidata_studio_cache
Create Date: 2026-06-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0016_item_override_approved"
down_revision = "0015_wikidata_studio_cache"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "wikidata_item_overrides",
        sa.Column("approved", sa.Boolean(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("wikidata_item_overrides", "approved")
