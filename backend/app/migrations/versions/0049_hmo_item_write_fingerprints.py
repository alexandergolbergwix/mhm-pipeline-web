"""hmo_item_write_fingerprints — per-item write-dedup for the HMO upload.

A content hash of what the last write set (payload labels, descriptions,
resolved claim triples). 'Update published entries' uploads skip the wiki
call when the live item's recorded fingerprint equals the current payload's.
"""

import sqlalchemy as sa
from alembic import op

revision = "0049_hmo_item_write_fingerprints"
down_revision = "0048_run_job_executor_heartbeat"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hmo_item_write_fingerprints",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("run_id", sa.UUID(), nullable=False, index=True),
        sa.Column("local_id", sa.String(256), nullable=False),
        sa.Column("wikibase_id", sa.String(32), nullable=True),
        sa.Column("payload_fingerprint", sa.String(64), nullable=False),
        sa.Column(
            "written_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.UniqueConstraint(
            "run_id", "local_id", name="uq_hmo_item_fp_run_local",
        ),
    )


def downgrade() -> None:
    op.drop_table("hmo_item_write_fingerprints")
