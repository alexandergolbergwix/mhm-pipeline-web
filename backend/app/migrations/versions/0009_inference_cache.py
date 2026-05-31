"""cache: cross-user inference cache (NER + genre + authority + AI verdict)

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-31

One table for every cache-able call we make to an external service or
ML model. Keyed by (kind, query_hash) — content-addressed, no per-user
namespacing, so any team member's first call serves every subsequent
caller for free.

Kinds (initial set):
    ner.person          — Person NER on a text segment
    ner.provenance      — Provenance NER on a MARC 561 segment
    ner.contents        — Contents NER on a MARC 505 segment
    genre.classify      — Multi-label genre classifier on (title + notes)
    authority.mazal     — NLI/Mazal name match
    authority.viaf      — VIAF SRU + cluster fetch
    authority.wikidata  — Wikidata SPARQL match + date backfill
    authority.kima      — KIMA place name → Wikidata URI
    ai_verdict          — eval-agent verdict per candidate

TTL policy lives in app code (cache_lookup_or_call), not the schema.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "inference_cache",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("kind",       sa.String(length=48), nullable=False),
        sa.Column("query_hash", sa.CHAR(64),          nullable=False),
        sa.Column(
            "query_summary",
            postgresql.JSONB(astext_type=sa.Text()), nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "result",
            postgresql.JSONB(astext_type=sa.Text()), nullable=False,
        ),
        sa.Column(
            "created_by", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("hit_count",   sa.Integer(),            nullable=False, server_default="0"),
        sa.Column("created_at",  sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("last_hit_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("expires_at",  sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("kind", "query_hash", name="uq_inference_cache_key"),
    )
    op.create_index(
        "ix_inference_cache_kind_expires",
        "inference_cache", ["kind", "expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_inference_cache_kind_expires", table_name="inference_cache")
    op.drop_table("inference_cache")
