"""email_throttle: per-recipient cooldown + daily cap

Revision ID: 0011
Revises: 0010_access_requests
Create Date: 2026-06-01

One small bookkeeping table that gates outbound mail per recipient.
Keyed by ``(recipient_index, bucket_day)`` — the blind index keeps the
plaintext address out of the DB, the date bucket keeps the row easy to
find with a single equality lookup.

Limits live in app code (``PER_MINUTE_COOLDOWN_SECONDS = 60`` and
``PER_DAY_CAP = 5``), not the schema, so they can be tuned without a
migration.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: str | None = "0010_access_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "email_throttle",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("recipient_index", sa.LargeBinary(length=32), nullable=False),
        sa.Column("bucket_day", sa.Date(), nullable=False),
        sa.Column("count_today", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "last_sent_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "recipient_index", "bucket_day", name="uq_email_throttle_recipient_day",
        ),
    )


def downgrade() -> None:
    op.drop_table("email_throttle")
