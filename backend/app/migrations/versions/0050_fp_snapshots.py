"""hmo_item_write_fingerprints.canonical_snapshot — persist resume checkpoint.

The canonical persist verifies each item with a live read-back; storing the
verified snapshot next to the payload fingerprint makes the loop resumable:
a re-run reuses stored snapshots instead of re-reading ~18.5k items.
"""

import sqlalchemy as sa
from alembic import op

revision = "0050_fp_snapshots"
down_revision = "0049_hmo_item_write_fingerprints"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "hmo_item_write_fingerprints",
        sa.Column("canonical_snapshot", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("hmo_item_write_fingerprints", "canonical_snapshot")
