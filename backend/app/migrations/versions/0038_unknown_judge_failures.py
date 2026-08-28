"""Expose persisted judge failures as unknown verdicts."""

from alembic import op

revision = "0038_unknown_judge_failures"
down_revision = "0037_wikidata_accept_foreign"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE wikidata_item_overrides
        SET ai_verdict = jsonb_set(
            jsonb_set(
                jsonb_set(
                    ai_verdict,
                    '{overall}',
                    to_jsonb('unknown'::text),
                    true
                ),
                '{judge_failure}',
                'true'::jsonb,
                true
            ),
            '{verification_error}',
            to_jsonb(COALESCE(ai_verdict->>'verification_error', ai_verdict->>'error', '')),
            true
        ) || jsonb_build_object('public_verdict_migration', 'w158_unknown_v1')
        WHERE ai_verdict->>'overall' = 'verification_failed';
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE wikidata_item_overrides
        SET ai_verdict = jsonb_set(
            ai_verdict - 'public_verdict_migration',
            '{overall}',
            to_jsonb('verification_failed'::text),
            true
        )
        WHERE ai_verdict->>'public_verdict_migration' = 'w158_unknown_v1';
        """
    )
