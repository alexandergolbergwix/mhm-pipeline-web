"""Discriminate legacy and canonical Wikidata Studio caches."""
from alembic import op
import sqlalchemy as sa
revision = "0034_wikidata_cache_source"
down_revision = "0033_wikidata_ai_verdict"
branch_labels = None
depends_on = None
def upgrade() -> None:
    op.add_column("wikidata_studio_cache", sa.Column("source", sa.String(length=16), nullable=False, server_default="legacy"))
    op.drop_constraint("uq_wikidata_studio_cache_run_mode", "wikidata_studio_cache", type_="unique")
    op.create_unique_constraint("uq_wikidata_studio_cache_run_mode", "wikidata_studio_cache", ["run_id", "approved_only", "source"])
def downgrade() -> None:
    op.drop_constraint("uq_wikidata_studio_cache_run_mode", "wikidata_studio_cache", type_="unique")
    op.create_unique_constraint("uq_wikidata_studio_cache_run_mode", "wikidata_studio_cache", ["run_id", "approved_only"])
    op.drop_column("wikidata_studio_cache", "source")
