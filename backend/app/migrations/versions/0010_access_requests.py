"""phase: self-service access requests

Revision ID: 0010_access_requests
Revises: 0009
Create Date: 2026-06-01

Public "request access" pipeline with admin approval. The row carries
the four PII fields (email / name / affiliation / justification) in
the same blind-index + AES-GCM shape as ``invitations`` so a DB dump
cannot expose who applied or what they wrote.

Two opaque tokens live per row:

* ``confirm_token_hash`` — double opt-in: the applicant clicks the
  emailed link to confirm the email is theirs. 24h TTL by default.
* ``decision_token_hash`` — magic-link the admin clicks from the
  notification email to land directly on the approve/deny screen.
  7d TTL by default.

Both are stored only as SHA-256; the plaintext never persists.

Status lifecycle:
    pending_email_confirm → pending_admin → approved | denied

Indexes:
    ix_access_requests_email_index — lookups on inbound POST for
        the enumeration-resistant "is there already a row for this
        email" check.
    ix_access_requests_status_created — the admin queue sorts by
        status then created_at DESC; this index serves both filters.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010_access_requests"
down_revision: str | None = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "access_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        # PII — blind index + AES-GCM ciphertext.
        sa.Column("email_index", sa.LargeBinary(length=32), nullable=False),
        sa.Column("email_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column("name_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column("affiliation_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column("justification_encrypted", sa.LargeBinary(), nullable=False),
        # State machine.
        sa.Column("status", sa.String(length=32), nullable=False),
        # Double opt-in confirm token (24h TTL).
        sa.Column("confirm_token_hash", sa.LargeBinary(length=32), nullable=False, unique=True),
        sa.Column("confirm_token_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        # Admin decision magic-link token (7d TTL). Nullable because it
        # is only minted after the applicant confirms their email.
        sa.Column("decision_token_hash", sa.LargeBinary(length=32), nullable=True, unique=True),
        sa.Column("decision_token_expires_at", sa.DateTime(timezone=True), nullable=True),
        # Decision audit.
        sa.Column(
            "reviewed_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("denial_reason", sa.String(length=1024), nullable=True),
        # Abuse-triage audit. IPv6-max is 45 chars; UA capped at 512.
        sa.Column("client_ip", sa.String(length=45), nullable=False),
        sa.Column("user_agent", sa.String(length=512), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_access_requests_email_index",
        "access_requests",
        ["email_index"],
    )
    op.create_index(
        "ix_access_requests_status_created",
        "access_requests",
        ["status", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_access_requests_status_created", table_name="access_requests")
    op.drop_index("ix_access_requests_email_index", table_name="access_requests")
    op.drop_table("access_requests")
