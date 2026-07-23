from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
revision = "0036_hmo_canonical_contract"
down_revision = "0035_hmo_canonical_entities"
branch_labels = None
depends_on = None
def upgrade() -> None:
    op.add_column("hmo_canonical_entities", sa.Column("source_uri", sa.String(1024), nullable=False, server_default=""))
    op.add_column("hmo_canonical_entities", sa.Column("entity_type", sa.String(128), nullable=False, server_default=""))
    for name, default in (("labels", "{}"), ("descriptions", "{}"), ("aliases", "{}"), ("claims", "[]"), ("authority_evidence", "[]"), ("provenance", "{}")):
        op.add_column("hmo_canonical_entities", sa.Column(name, postgresql.JSONB, nullable=False, server_default=sa.text("'" + default + "'::jsonb")))
    op.add_column("hmo_canonical_entities", sa.Column("status", sa.String(32), nullable=False, server_default="live"))
    op.alter_column("hmo_canonical_entities", "source_uri", server_default=None)
    op.alter_column("hmo_canonical_entities", "entity_type", server_default=None)
    for name in ("labels", "descriptions", "aliases", "claims", "authority_evidence", "provenance", "status"):
        op.alter_column("hmo_canonical_entities", name, server_default=None)
def downgrade() -> None:
    for name in ("status", "provenance", "authority_evidence", "claims", "aliases", "descriptions", "labels", "entity_type", "source_uri"):
        op.drop_column("hmo_canonical_entities", name)
