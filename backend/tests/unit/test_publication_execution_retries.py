"""Execution workers survive dead database connections (hardening H1)."""
from __future__ import annotations

import pytest
from sqlalchemy.exc import InterfaceError

from app.pipeline.wikidata_publication_execution_job import (
    _run_with_connection_retries,
)


@pytest.mark.asyncio
async def test_worker_retries_on_a_dead_connection_and_succeeds(monkeypatch):
    monkeypatch.setattr(
        "app.pipeline.wikidata_publication_execution_job._CONNECTION_RETRY_BACKOFF_S",
        0.0,
    )
    attempts: list[int] = []

    async def attempt():
        attempts.append(1)
        if len(attempts) < 3:
            raise InterfaceError(None, Exception("connection is closed"), "rollback")
        return "summary"

    result = await _run_with_connection_retries("publication-job:test:w0", attempt)

    assert result == "summary"
    assert len(attempts) == 3


@pytest.mark.asyncio
async def test_worker_raises_after_the_bounded_attempts(monkeypatch):
    monkeypatch.setattr(
        "app.pipeline.wikidata_publication_execution_job._CONNECTION_RETRY_BACKOFF_S",
        0.0,
    )
    attempts: list[int] = []

    async def attempt():
        attempts.append(1)
        raise InterfaceError(None, Exception("connection is closed"), "rollback")

    with pytest.raises(InterfaceError):
        await _run_with_connection_retries("publication-job:test:w0", attempt)

    assert len(attempts) == 3


@pytest.mark.asyncio
async def test_worker_does_not_retry_unrelated_failures():
    attempts: list[int] = []

    async def attempt():
        attempts.append(1)
        raise ValueError("a programming error is not a dead connection")

    with pytest.raises(ValueError):
        await _run_with_connection_retries("publication-job:test:w0", attempt)

    assert len(attempts) == 1


@pytest.mark.asyncio
async def test_absent_observations_expire_once_the_plan_executes() -> None:
    """An `absent` cache entry must not deny items the pass created (H4)."""
    from datetime import UTC, datetime

    from app.publication.repository import (
        DryRunReceiptRecord,
        ExecutionRecord,
        InMemoryPublicationRepository,
        PlanActionRecord,
        PlanRecord,
        PublicationRecord,
    )
    from app.publication.types import ProfileRef, SourceSnapshotRef, TargetRef

    repository = InMemoryPublicationRepository()
    publication = PublicationRecord(
        publication_id="publication-1",
        run_id="48ba6c13-115c-4763-bff1-c08b9031b518",
        source_snapshot=SourceSnapshotRef("snapshot-7", "v7", "4" * 64),
        profile=ProfileRef("mhm-wikidata", "1"),
        target=TargetRef("www.wikidata.org", "production"),
        state="dry_run_ready",
        latest_release_id="release-1",
        idempotency_key="prepare-1",
    )
    await repository.begin_plan("plan-1")
    await repository.add_plan_actions("plan-1", (
        PlanActionRecord(
            plan_id="plan-1", entity_key="manuscript:1", entity_digest="d1",
            action="create", observation_status="absent",
            target_qid=None, target_fingerprint=None, target_revision=None,
            allow_foreign_update=False, detail=None,
        ),
        PlanActionRecord(
            plan_id="plan-1", entity_key="work:2", entity_digest="d2",
            action="update", observation_status="present_owned",
            target_qid="Q200", target_fingerprint="f2", target_revision=5,
            allow_foreign_update=False, detail=None,
        ),
    ))
    now = datetime(2026, 10, 17, tzinfo=UTC)
    await repository.complete_plan(publication, PlanRecord(
        plan_id="plan-1", publication_id="publication-1",
        release_id="release-1", release_digest="r" * 64,
        approval_set_id="approval-1", approval_digest="a" * 64,
        plan_digest="p" * 64, idempotency_key="dry-run-1",
        create_count=1, update_count=1, skip_count=0, blocked_count=0,
        receipt=DryRunReceiptRecord(
            receipt_id="receipt-1", plan_id="plan-1",
            release_digest="r" * 64, approval_digest="a" * 64,
            plan_digest="p" * 64, receipt_digest="c" * 64,
            passed=False, created_at=now, expires_at=now,
        ),
    ))

    before = await repository.previous_plan_observations("publication-1")
    assert before["manuscript:1"].observation_status == "absent"

    await repository.complete_execution(publication, ExecutionRecord(
        execution_id="execution-1", publication_id="publication-1",
        plan_id="plan-1", receipt_id="receipt-1", receipt_digest="c" * 64,
        actor_id="curator-1", idempotency_key="publish-1",
        status="running", total_count=2,
    ))

    after = await repository.previous_plan_observations("publication-1")
    assert "manuscript:1" not in after
    assert after["work:2"].observation_status == "present_owned"
