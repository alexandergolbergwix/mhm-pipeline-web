import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.models.publication import (
    Publication,
    PublicationDryRunReceipt,
    PublicationExecution,
    PublicationPlan,
    PublicationPlanAction,
    PublicationWriteIntent,
    PublicationWriteReceipt,
)
from app.publication.reference_repair_prior_writes import has_unresolved_prior_write


@pytest.mark.asyncio
async def test_legacy_rejection_requires_later_verified_update_to_the_same_item(sample_run, db_session):
    qid = "Q123"
    actor_id = str(sample_run["user_id"])
    old_time = datetime(2026, 9, 10, tzinfo=UTC)
    digest = "a" * 64
    publication = Publication(
        id=uuid.uuid4(), run_id=sample_run["run_id"], source_snapshot_id="snapshot",
        source_revision="source", source_digest=digest, profile_name="mhm-wikidata",
        profile_version="1", target_site="www.wikidata.org", target_environment="production",
        state="active", idempotency_key="reference-recovery", latest_release_id=uuid.uuid4(),
    )
    db_session.add(publication)
    await db_session.flush()

    async def record_attempt(name, base_revision, state, when, detail=None):
        plan = PublicationPlan(id=uuid.uuid4(), publication_id=publication.id)
        db_session.add(plan)
        await db_session.flush()
        dry_run = PublicationDryRunReceipt(
            id=uuid.uuid4(), plan_id=plan.id, release_digest=digest, approval_digest=digest,
            plan_digest=digest, receipt_digest=name, passed=True, created_at=when,
            expires_at=when + timedelta(days=1),
        )
        db_session.add_all([dry_run, PublicationPlanAction(
            plan_id=plan.id, entity_key="manuscript", entity_digest=digest, action="update",
            observation_status="present", target_qid=qid, target_revision=base_revision,
        )])
        await db_session.flush()
        execution = PublicationExecution(
            id=uuid.uuid4(), publication_id=publication.id, plan_id=plan.id, receipt_id=dry_run.id,
            receipt_digest=name, actor_id=actor_id, idempotency_key=name, status="paused",
            total_count=1,
        )
        db_session.add(execution)
        await db_session.flush()
        intent = PublicationWriteIntent(
            id=uuid.uuid4(), execution_id=execution.id, entity_key="manuscript",
            action="update", target_qid=qid, mutation_digest=digest, request_key=name,
            attempt=1, state=state, detail=detail, created_at=when, updated_at=when,
        )
        db_session.add(intent)
        await db_session.flush()
        return intent

    await record_attempt(
        "old", 10, "outcome_unknown", old_time,
        detail="'Bad value type wikibase-entityid, expected string'",
    )
    assert await has_unresolved_prior_write(db_session, qid, actor_id)

    later_time = old_time + timedelta(days=30)
    later = await record_attempt("later", 20, "succeeded", later_time)
    later.result_fingerprint = digest
    receipt = PublicationWriteReceipt(
        id=uuid.uuid4(), intent_id=later.id, status="succeeded", qid=qid,
        fingerprint=digest, created_at=later_time,
    )
    db_session.add(receipt)
    await db_session.flush()
    assert not await has_unresolved_prior_write(db_session, qid, actor_id)
    assert await has_unresolved_prior_write(db_session, qid, "different-actor")

    receipt.fingerprint = "b" * 64
    await db_session.flush()
    assert await has_unresolved_prior_write(db_session, qid, actor_id)

    receipt.fingerprint = digest
    await db_session.flush()
    await record_attempt("in-flight", 20, "in_flight", later_time)
    assert await has_unresolved_prior_write(db_session, qid, actor_id)
