from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
revision = "0035_hmo_canonical_entities"
down_revision = "0034_wikidata_cache_source"
branch_labels = None
depends_on = None
def upgrade() -> None:
    op.create_table("hmo_canonical_entities", sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True), sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False, index=True), sa.Column("local_id", sa.String(512), nullable=False), sa.Column("wikibase_id", sa.String(32), nullable=False), sa.Column("revision_id", sa.String(64)), sa.Column("source_fingerprint", sa.CHAR(64), nullable=False), sa.Column("snapshot", postgresql.JSONB, nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.UniqueConstraint("run_id", "local_id", name="uq_hmo_canonical_entity_run_local"))
def downgrade() -> None:
    op.drop_table("hmo_canonical_entities")
