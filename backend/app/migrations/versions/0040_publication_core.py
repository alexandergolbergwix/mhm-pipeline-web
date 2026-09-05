"""Add normalized state for durable Wikidata publication."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0040_publication_core"
down_revision = "0039_public_abstain_provider_err"
branch_labels = None
depends_on = None


def _uuid() -> postgresql.UUID:
    return postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "publications",
        sa.Column("id", _uuid(), nullable=False),
        sa.Column("run_id", _uuid(), nullable=False),
        sa.Column("source_snapshot_id", sa.String(256), nullable=False),
        sa.Column("source_revision", sa.String(256), nullable=False),
        sa.Column("source_digest", sa.String(64), nullable=False),
        sa.Column("profile_name", sa.String(128), nullable=False),
        sa.Column("profile_version", sa.String(64), nullable=False),
        sa.Column("target_site", sa.String(256), nullable=False),
        sa.Column("target_environment", sa.String(32), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("idempotency_key", sa.String(256), nullable=False),
        sa.Column("latest_release_id", _uuid(), nullable=False),
        sa.Column("latest_approval_set_id", _uuid(), nullable=True),
        sa.Column("latest_plan_id", _uuid(), nullable=True),
        sa.Column("latest_execution_id", _uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id",
            "idempotency_key",
            name="uq_publications_run_idempotency",
        ),
    )
    op.create_index("ix_publications_run_id", "publications", ["run_id"])

    op.create_table(
        "publication_releases",
        sa.Column("id", _uuid(), nullable=False),
        sa.Column("publication_id", _uuid(), nullable=False),
        sa.Column("status", sa.String(16), server_default="building", nullable=False),
        sa.Column("release_digest", sa.String(64), nullable=True),
        sa.Column("entity_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("finding_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("sealed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["publication_id"], ["publications.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "publication_id",
            "release_digest",
            name="uq_publication_releases_digest",
        ),
    )
    op.create_index(
        "ix_publication_releases_publication_id",
        "publication_releases",
        ["publication_id"],
    )

    op.create_table(
        "publication_entities",
        sa.Column("release_id", _uuid(), nullable=False),
        sa.Column("entity_key", sa.String(512), nullable=False),
        sa.Column("entity_type", sa.String(64), nullable=False),
        sa.Column("entity_digest", sa.String(64), nullable=False),
        sa.Column("document", postgresql.JSONB(), nullable=False),
        sa.Column("evidence_refs", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["release_id"], ["publication_releases.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("release_id", "entity_key"),
    )
    op.create_index(
        "ix_publication_entities_release_type_key",
        "publication_entities",
        ["release_id", "entity_type", "entity_key"],
    )

    op.create_table(
        "publication_identity_assertions",
        sa.Column("release_id", _uuid(), nullable=False),
        sa.Column("entity_key", sa.String(512), nullable=False),
        sa.Column("assertion", sa.String(512), nullable=False),
        sa.ForeignKeyConstraint(["release_id"], ["publication_releases.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("release_id", "entity_key", "assertion"),
    )
    op.create_index(
        "ix_publication_identity_release_assertion",
        "publication_identity_assertions",
        ["release_id", "assertion"],
    )

    op.create_table(
        "publication_entity_references",
        sa.Column("release_id", _uuid(), nullable=False),
        sa.Column("source_entity_key", sa.String(512), nullable=False),
        sa.Column("target_entity_key", sa.String(512), nullable=False),
        sa.ForeignKeyConstraint(["release_id"], ["publication_releases.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("release_id", "source_entity_key", "target_entity_key"),
    )

    op.create_table(
        "publication_findings",
        sa.Column("id", _uuid(), nullable=False),
        sa.Column("release_id", _uuid(), nullable=False),
        sa.Column("entity_key", sa.String(512), nullable=True),
        sa.Column("scope", sa.String(32), nullable=False),
        sa.Column("code", sa.String(96), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["release_id"], ["publication_releases.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_publication_findings_release_severity",
        "publication_findings",
        ["release_id", "severity"],
    )

    op.create_table(
        "publication_approval_sets",
        sa.Column("id", _uuid(), nullable=False),
        sa.Column("status", sa.String(16), server_default="building", nullable=False),
        sa.Column("publication_id", _uuid(), nullable=True),
        sa.Column("release_id", _uuid(), nullable=True),
        sa.Column("release_digest", sa.String(64), nullable=True),
        sa.Column("approval_digest", sa.String(64), nullable=True),
        sa.Column("actor_id", sa.String(128), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(256), nullable=True),
        sa.Column("approved_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("rejected_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("sealed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["publication_id"], ["publications.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["release_id"], ["publication_releases.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "publication_id",
            "idempotency_key",
            name="uq_publication_approval_idempotency",
        ),
    )
    op.create_index(
        "ix_publication_approval_sets_publication_id",
        "publication_approval_sets",
        ["publication_id"],
    )

    op.create_table(
        "publication_approval_decisions",
        sa.Column("approval_set_id", _uuid(), nullable=False),
        sa.Column("entity_key", sa.String(512), nullable=False),
        sa.Column("entity_digest", sa.String(64), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.ForeignKeyConstraint(
            ["approval_set_id"], ["publication_approval_sets.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("approval_set_id", "entity_key"),
    )

    op.create_table(
        "publication_plans",
        sa.Column("id", _uuid(), nullable=False),
        sa.Column("status", sa.String(16), server_default="building", nullable=False),
        sa.Column("publication_id", _uuid(), nullable=True),
        sa.Column("release_id", _uuid(), nullable=True),
        sa.Column("release_digest", sa.String(64), nullable=True),
        sa.Column("approval_set_id", _uuid(), nullable=True),
        sa.Column("approval_digest", sa.String(64), nullable=True),
        sa.Column("plan_digest", sa.String(64), nullable=True),
        sa.Column("idempotency_key", sa.String(256), nullable=True),
        sa.Column("create_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("update_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("skip_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("blocked_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("sealed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["publication_id"], ["publications.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["release_id"], ["publication_releases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["approval_set_id"],
            ["publication_approval_sets.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "publication_id",
            "idempotency_key",
            name="uq_publication_plan_idempotency",
        ),
    )
    op.create_index(
        "ix_publication_plans_publication_id",
        "publication_plans",
        ["publication_id"],
    )

    op.create_table(
        "publication_plan_actions",
        sa.Column("plan_id", _uuid(), nullable=False),
        sa.Column("entity_key", sa.String(512), nullable=False),
        sa.Column("entity_digest", sa.String(64), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("observation_status", sa.String(32), nullable=False),
        sa.Column("target_qid", sa.String(32), nullable=True),
        sa.Column("target_fingerprint", sa.String(128), nullable=True),
        sa.Column("target_revision", sa.BigInteger(), nullable=True),
        sa.Column(
            "allow_foreign_update",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["plan_id"], ["publication_plans.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("plan_id", "entity_key"),
    )
    op.create_index(
        "ix_publication_plan_actions_action",
        "publication_plan_actions",
        ["plan_id", "action"],
    )

    op.create_table(
        "publication_dry_run_receipts",
        sa.Column("id", _uuid(), nullable=False),
        sa.Column("plan_id", _uuid(), nullable=False),
        sa.Column("release_digest", sa.String(64), nullable=False),
        sa.Column("approval_digest", sa.String(64), nullable=False),
        sa.Column("plan_digest", sa.String(64), nullable=False),
        sa.Column("receipt_digest", sa.String(64), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["plan_id"], ["publication_plans.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plan_id"),
        sa.UniqueConstraint("receipt_digest"),
    )

    op.create_table(
        "publication_executions",
        sa.Column("id", _uuid(), nullable=False),
        sa.Column("publication_id", _uuid(), nullable=False),
        sa.Column("plan_id", _uuid(), nullable=False),
        sa.Column("receipt_id", _uuid(), nullable=False),
        sa.Column("receipt_digest", sa.String(64), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("idempotency_key", sa.String(256), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("total_count", sa.Integer(), nullable=False),
        sa.Column("succeeded_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "pre_send_retryable_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column("outcome_unknown_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("blocked_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["publication_id"], ["publications.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["plan_id"], ["publication_plans.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["receipt_id"], ["publication_dry_run_receipts.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "publication_id",
            "idempotency_key",
            name="uq_publication_execution_idempotency",
        ),
    )
    op.create_index(
        "ix_publication_executions_publication_id",
        "publication_executions",
        ["publication_id"],
    )

    op.create_table(
        "publication_execution_actions",
        sa.Column("execution_id", _uuid(), nullable=False),
        sa.Column("action_key", sa.String(768), nullable=False),
        sa.Column("entity_key", sa.String(512), nullable=False),
        sa.Column("ordinal", sa.BigInteger(), nullable=False),
        sa.Column("phase", sa.String(32), nullable=False),
        sa.Column("state", sa.String(32), server_default="pending", nullable=False),
        sa.Column("lease_owner", sa.String(256), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("target_qid", sa.String(32), nullable=True),
        sa.Column("target_fingerprint", sa.String(128), nullable=True),
        sa.Column("target_revision", sa.BigInteger(), nullable=True),
        sa.Column(
            "allow_foreign_update",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column("result_qid", sa.String(32), nullable=True),
        sa.Column("result_fingerprint", sa.String(128), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["execution_id"], ["publication_executions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("execution_id", "action_key"),
        sa.UniqueConstraint(
            "execution_id",
            "ordinal",
            name="uq_publication_execution_action_ordinal",
        ),
    )
    op.create_index(
        "ix_publication_execution_actions_claim",
        "publication_execution_actions",
        ["execution_id", "state", "next_attempt_at", "ordinal"],
    )

    op.create_table(
        "publication_write_intents",
        sa.Column("id", _uuid(), nullable=False),
        sa.Column("execution_id", _uuid(), nullable=False),
        sa.Column("entity_key", sa.String(512), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("target_qid", sa.String(32), nullable=True),
        sa.Column("mutation_digest", sa.String(64), nullable=False),
        sa.Column("request_key", sa.String(1024), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("result_qid", sa.String(32), nullable=True),
        sa.Column("result_fingerprint", sa.String(128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["execution_id"], ["publication_executions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "execution_id",
            "entity_key",
            "attempt",
            name="uq_publication_write_intent_attempt",
        ),
        sa.UniqueConstraint("request_key", name="uq_publication_write_request_key"),
    )
    op.create_index(
        "ix_publication_write_intents_state",
        "publication_write_intents",
        ["execution_id", "state"],
    )

    op.create_table(
        "publication_write_receipts",
        sa.Column("id", _uuid(), nullable=False),
        sa.Column("intent_id", _uuid(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("qid", sa.String(32), nullable=True),
        sa.Column("fingerprint", sa.String(128), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["intent_id"], ["publication_write_intents.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("intent_id"),
    )

    op.create_table(
        "publication_journal_events",
        sa.Column("sequence", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("publication_id", _uuid(), nullable=False),
        sa.Column("execution_id", _uuid(), nullable=False),
        sa.Column("intent_id", _uuid(), nullable=False),
        sa.Column("entity_key", sa.String(512), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["publication_id"], ["publications.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["execution_id"], ["publication_executions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["intent_id"], ["publication_write_intents.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("sequence"),
    )
    op.create_index(
        "ix_publication_journal_publication_sequence",
        "publication_journal_events",
        ["publication_id", "sequence"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_publication_journal_publication_sequence",
        table_name="publication_journal_events",
    )
    op.drop_table("publication_journal_events")
    op.drop_table("publication_write_receipts")
    op.drop_index(
        "ix_publication_write_intents_state",
        table_name="publication_write_intents",
    )
    op.drop_table("publication_write_intents")
    op.drop_index(
        "ix_publication_execution_actions_claim",
        table_name="publication_execution_actions",
    )
    op.drop_table("publication_execution_actions")
    op.drop_index(
        "ix_publication_executions_publication_id",
        table_name="publication_executions",
    )
    op.drop_table("publication_executions")
    op.drop_table("publication_dry_run_receipts")
    op.drop_index("ix_publication_plan_actions_action", table_name="publication_plan_actions")
    op.drop_table("publication_plan_actions")
    op.drop_index("ix_publication_plans_publication_id", table_name="publication_plans")
    op.drop_table("publication_plans")
    op.drop_table("publication_approval_decisions")
    op.drop_index(
        "ix_publication_approval_sets_publication_id",
        table_name="publication_approval_sets",
    )
    op.drop_table("publication_approval_sets")
    op.drop_index(
        "ix_publication_findings_release_severity",
        table_name="publication_findings",
    )
    op.drop_table("publication_findings")
    op.drop_table("publication_entity_references")
    op.drop_index(
        "ix_publication_identity_release_assertion",
        table_name="publication_identity_assertions",
    )
    op.drop_table("publication_identity_assertions")
    op.drop_index(
        "ix_publication_entities_release_type_key",
        table_name="publication_entities",
    )
    op.drop_table("publication_entities")
    op.drop_index(
        "ix_publication_releases_publication_id",
        table_name="publication_releases",
    )
    op.drop_table("publication_releases")
    op.drop_index("ix_publications_run_id", table_name="publications")
    op.drop_table("publications")
