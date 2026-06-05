"""Create mazal_authorities, mazal_name_index, kima_places, kima_name_index tables.

These tables hold the Mazal (NLI authority) and KIMA (Hebrew place-names)
datasets imported from the desktop's SQLite indexes.  They replace the
SQLite / Modal authority backend with a direct Heroku Postgres lookup so
every authority call benefits from sub-millisecond Postgres latency,
the existing Redis+Postgres inference cache, and no extra running cost.

Revision ID: 0018_authority_pg_tables
Revises: 0017_rdf_triple_overrides
Create Date: 2026-06-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0018_authority_pg_tables"
down_revision = "0017_rdf_triple_overrides"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Mazal authority records ────────────────────────────────────────
    op.create_table(
        "mazal_authorities",
        sa.Column("nli_id", sa.Text, primary_key=True),
        sa.Column("entity_type", sa.Text, nullable=False),
        sa.Column("preferred_name_heb", sa.Text),
        sa.Column("preferred_name_lat", sa.Text),
        sa.Column("dates", sa.Text),
        sa.Column("aleph_id", sa.Text),
    )

    # ── Mazal name variants (for normalized-name lookup) ───────────────
    op.create_table(
        "mazal_name_index",
        sa.Column("id", sa.BigInteger, autoincrement=True, primary_key=True),
        sa.Column("normalized_name", sa.Text, nullable=False),
        sa.Column("nli_id", sa.Text, nullable=False),
        sa.Column("entity_type", sa.Text, nullable=False),
        sa.Column("script", sa.Text),
    )
    # Hash indexes for exact-match lookups — no btree 8191-byte limit.
    op.execute(
        "CREATE INDEX idx_mazal_name_type ON mazal_name_index "
        "USING hash (normalized_name)"
    )
    op.execute(
        "CREATE INDEX idx_mazal_type_name ON mazal_name_index (entity_type)"
    )

    # ── KIMA place records ────────────────────────────────────────────
    op.create_table(
        "kima_places",
        sa.Column("kima_id", sa.Integer, primary_key=True),
        sa.Column("primary_heb", sa.Text),
        sa.Column("primary_rom", sa.Text),
        sa.Column("wikidata_id", sa.Text),
        sa.Column("viaf_id", sa.Text),
        sa.Column("geonames_id", sa.Text),
        sa.Column("mazal_nli_id", sa.Text),
        sa.Column("lat", sa.Double(precision=53)),
        sa.Column("lon", sa.Double(precision=53)),
    )

    # ── KIMA name variants ────────────────────────────────────────────
    op.create_table(
        "kima_name_index",
        sa.Column("id", sa.BigInteger, autoincrement=True, primary_key=True),
        sa.Column("normalized_name", sa.Text, nullable=False),
        sa.Column("kima_id", sa.Integer, nullable=False),
        sa.Column("script", sa.Text),
    )
    op.execute(
        "CREATE INDEX idx_kima_name ON kima_name_index USING hash (normalized_name)"
    )


def downgrade() -> None:
    op.drop_table("kima_name_index")
    op.drop_table("kima_places")
    op.drop_table("mazal_name_index")
    op.drop_table("mazal_authorities")
