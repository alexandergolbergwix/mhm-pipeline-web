"""Store public Wikidata candidates with local fact indexes and scope progress."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0056_wikidata_mirror"
down_revision = "0055_source_review_choices"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "wikidata_mirror_entities",
        sa.Column("qid", sa.String(24), primary_key=True),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("document", JSONB(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
    )
    op.create_index(
        "ix_wikidata_mirror_entities_observed_at", "wikidata_mirror_entities", ["observed_at"]
    )
    op.create_table(
        "wikidata_mirror_facts",
        sa.Column(
            "qid",
            sa.String(24),
            sa.ForeignKey("wikidata_mirror_entities.qid", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("property_id", sa.String(24), primary_key=True),
        sa.Column("value_hash", sa.String(64), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
    )
    op.create_index(
        "ix_wd_mirror_fact_lookup", "wikidata_mirror_facts", ["property_id", "value_hash", "qid"]
    )
    op.create_table(
        "wikidata_mirror_scopes",
        sa.Column("scope_key", sa.String(64), primary_key=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("requested_count", sa.Integer(), nullable=False),
        sa.Column("stored_count", sa.Integer(), nullable=False),
        sa.Column("progress", JSONB(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("wikidata_mirror_scopes")
    op.drop_table("wikidata_mirror_facts")
    op.drop_table("wikidata_mirror_entities")
