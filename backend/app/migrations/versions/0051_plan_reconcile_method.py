"""publication_plan_actions.reconcile_method — how the target was found.

F3/F4 route blocked items on the reconcile method: identifier equality
(p3959/shelfmark, identifier) is strong evidence, a label/title string match
needs the AI judge, and a NULL method means a legacy row or a live preflight
that found the target mid-write.
"""

import sqlalchemy as sa
from alembic import op

revision = "0051_plan_reconcile_method"
down_revision = "0050_fp_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "publication_plan_actions",
        sa.Column("reconcile_method", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("publication_plan_actions", "reconcile_method")
