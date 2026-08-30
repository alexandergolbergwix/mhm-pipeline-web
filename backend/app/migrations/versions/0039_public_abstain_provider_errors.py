"""Use public ``abstain`` for judge provider errors."""

# The helper formats only fixed table and column names from this migration.
# ruff: noqa: S608

from alembic import op

revision = "0039_public_abstain_provider_err"
down_revision = "0038_unknown_judge_failures"
branch_labels = None
depends_on = None


def _normalise_jsonb(column: str, table: str) -> None:
    op.execute(  # noqa: S608
        f"""
        UPDATE {table}
        SET {column} = {column} || jsonb_build_object(
            'overall', 'abstain',
            'judge_failure', true,
            'verification_status', 'provider_error',
            'verification_error', COALESCE(
                {column}->>'verification_error',
                {column}->>'error',
                'judge did not return a valid verdict'
            ),
            'public_verdict_migration', 'w211_abstain_v1'
        )
        WHERE {column} IS NOT NULL
          AND (
            COALESCE({column}->>'overall', '') NOT IN
                ('full', 'pass', 'partial', 'fail', 'abstain')
            OR {column}->>'judge_failure' = 'true'
            OR {column}->>'verification_status' = 'provider_error'
          );
        """
    )


def upgrade() -> None:
    _normalise_jsonb("ai_verdict", "wikidata_item_overrides")
    _normalise_jsonb("ai_verdict", "hmo_studio_item_overrides")
    _normalise_jsonb("ai_verdict", "extraction_approvals")
    op.execute(
        """
        UPDATE authority_matches
        SET payload = jsonb_set(
            payload,
            '{ai_verdict}',
            (
                payload->'ai_verdict'
            ) || jsonb_build_object(
                'overall', 'abstain',
                'judge_failure', true,
                'verification_status', 'provider_error',
                'verification_error', COALESCE(
                    payload->'ai_verdict'->>'verification_error',
                    payload->'ai_verdict'->>'error',
                    'judge did not return a valid verdict'
                ),
                'public_verdict_migration', 'w211_abstain_v1'
            ),
            true
        )
        WHERE payload->'ai_verdict' IS NOT NULL
          AND (
            COALESCE(payload->'ai_verdict'->>'overall', '') NOT IN
                ('full', 'pass', 'partial', 'fail', 'abstain')
            OR payload->'ai_verdict'->>'judge_failure' = 'true'
            OR payload->'ai_verdict'->>'verification_status' = 'provider_error'
          );
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE wikidata_item_overrides
        SET ai_verdict = (ai_verdict - 'verification_status' - 'public_verdict_migration')
            || jsonb_build_object('overall', 'unknown')
        WHERE ai_verdict->>'public_verdict_migration' = 'w211_abstain_v1';
        UPDATE hmo_studio_item_overrides
        SET ai_verdict = (ai_verdict - 'verification_status' - 'public_verdict_migration')
            || jsonb_build_object('overall', 'unknown')
        WHERE ai_verdict->>'public_verdict_migration' = 'w211_abstain_v1';
        UPDATE extraction_approvals
        SET ai_verdict = (ai_verdict - 'verification_status' - 'public_verdict_migration')
            || jsonb_build_object('overall', 'unknown')
        WHERE ai_verdict->>'public_verdict_migration' = 'w211_abstain_v1';
        UPDATE authority_matches
        SET payload = jsonb_set(
            payload,
            '{ai_verdict}',
            (payload->'ai_verdict' - 'verification_status' - 'public_verdict_migration')
                || jsonb_build_object('overall', 'unknown'),
            true
        )
        WHERE payload->'ai_verdict'->>'public_verdict_migration' = 'w211_abstain_v1';
        """
    )
