"""Behavior tests for the Wikidata publication module seam."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.publication import (
    AuditQuery,
    BlockingFindingsError,
    CancelCommand,
    CancelledExecutionError,
    DryRunCommand,
    EntityPageQuery,
    EntitySelection,
    ForeignQidConsent,
    PrepareRequest,
    ProfileRef,
    PublicationEntityInput,
    PublishCommand,
    ResumeCommand,
    ReviewCommand,
    SourceSnapshotRef,
    StaleDigestError,
    SummaryQuery,
    TargetRef,
)
from app.publication.gateway import FakeWikidataGateway, TargetObservation, WriteOutcome
from app.publication.testing import create_in_memory_publication_module


def _prepare_request() -> PrepareRequest:
    return PrepareRequest(
        run_id="48ba6c13-115c-4763-bff1-c08b9031b518",
        source_snapshot=SourceSnapshotRef(
            snapshot_id="snapshot-7",
            revision="approved-v7",
            digest="4" * 64,
        ),
        profile=ProfileRef(name="mhm-wikidata", version="1"),
        target=TargetRef(site="www.wikidata.org", environment="production"),
        actor_id="curator-1",
        idempotency_key="prepare-7",
    )


@pytest.mark.asyncio
async def test_prepare_creates_a_deterministic_immutable_release() -> None:
    entities = (
        PublicationEntityInput(
            entity_key="work:2",
            entity_type="work",
            document={"labels": {"he": "ספר ב"}, "claims": []},
        ),
        PublicationEntityInput(
            entity_key="manuscript:1",
            entity_type="manuscript",
            document={"claims": [], "labels": {"en": "Manuscript 1"}},
        ),
    )
    first = create_in_memory_publication_module(
        entities_by_snapshot={"snapshot-7": entities},
    )
    second = create_in_memory_publication_module(
        entities_by_snapshot={"snapshot-7": tuple(reversed(entities))},
    )

    first_operation = await first.prepare(_prepare_request())
    second_operation = await second.prepare(_prepare_request())
    first_summary = await first.read(SummaryQuery(first_operation.publication_id))
    second_summary = await second.read(SummaryQuery(second_operation.publication_id))

    assert first_summary.release_digest == second_summary.release_digest
    assert first_summary.entity_count == 2
    assert first_summary.state == "prepared"
    page = await first.read(
        EntityPageQuery(
            publication_id=first_operation.publication_id,
            release_id=first_summary.release_id,
            limit=100,
        )
    )
    assert [entity.entity_key for entity in page.items] == [
        "manuscript:1",
        "work:2",
    ]
    assert all(entity.entity_digest for entity in page.items)


@pytest.mark.asyncio
async def test_summary_reports_when_the_frozen_source_is_stale() -> None:
    module = create_in_memory_publication_module(
        entities_by_snapshot={
            "snapshot-7": (
                PublicationEntityInput(
                    entity_key="manuscript:1",
                    entity_type="manuscript",
                    document={"labels": {"en": "Manuscript 1"}},
                ),
            )
        },
        current_source_digests={"snapshot-7": "5" * 64},
    )

    operation = await module.prepare(_prepare_request())
    summary = await module.read(SummaryQuery(operation.publication_id))

    assert summary.source_current is False


@pytest.mark.asyncio
async def test_review_creates_a_digest_bound_approval_set() -> None:
    module = create_in_memory_publication_module(
        entities_by_snapshot={
            "snapshot-7": (
                PublicationEntityInput(
                    entity_key="manuscript:1",
                    entity_type="manuscript",
                    document={"labels": {"en": "Manuscript 1"}},
                ),
                PublicationEntityInput(
                    entity_key="work:2",
                    entity_type="work",
                    document={"labels": {"he": "ספר ב"}},
                ),
            )
        },
    )
    operation = await module.prepare(_prepare_request())
    prepared = await module.read(SummaryQuery(operation.publication_id))

    with pytest.raises(StaleDigestError):
        await module.advance(
            operation.publication_id,
            ReviewCommand(
                release_id=prepared.release_id,
                expected_release_digest="0" * 64,
                selection=EntitySelection.all(),
                decision="approve",
                actor_id="curator-1",
                reason="Reviewed against the approved source snapshot.",
                idempotency_key="review-7",
            ),
        )

    unchanged = await module.read(SummaryQuery(operation.publication_id))
    assert unchanged.state == "prepared"
    review = await module.advance(
        operation.publication_id,
        ReviewCommand(
            release_id=prepared.release_id,
            expected_release_digest=prepared.release_digest,
            selection=EntitySelection.all(),
            decision="approve",
            actor_id="curator-1",
            reason="Reviewed against the approved source snapshot.",
            idempotency_key="review-7",
        ),
    )
    reviewed = await module.read(SummaryQuery(operation.publication_id))

    assert review.resource_type == "approval_set"
    assert reviewed.state == "reviewed"
    assert reviewed.approval_set_id == review.resource_id
    assert reviewed.approval_digest
    assert reviewed.approved_count == 2
    assert reviewed.rejected_count == 0


@pytest.mark.asyncio
async def test_prepare_blocks_review_for_cross_entity_findings() -> None:
    module = create_in_memory_publication_module(
        entities_by_snapshot={
            "snapshot-7": (
                PublicationEntityInput(
                    entity_key="manuscript:1",
                    entity_type="manuscript",
                    document={"labels": {"en": "Manuscript 1"}},
                    identity_assertions=("shelfmark:shared",),
                    local_references=("work:missing",),
                ),
                PublicationEntityInput(
                    entity_key="manuscript:2",
                    entity_type="manuscript",
                    document={"labels": {"en": "Manuscript 2"}},
                    identity_assertions=("shelfmark:shared",),
                ),
            )
        },
    )
    operation = await module.prepare(_prepare_request())
    summary = await module.read(SummaryQuery(operation.publication_id))

    assert summary.finding_count == 2
    with pytest.raises(BlockingFindingsError):
        await module.advance(
            summary.publication_id,
            ReviewCommand(
                release_id=summary.release_id,
                expected_release_digest=summary.release_digest,
                selection=EntitySelection.all(),
                decision="approve",
                actor_id="curator-1",
                reason="This must not bypass corpus findings.",
                idempotency_key="blocked-review",
            ),
        )


@pytest.mark.asyncio
async def test_dry_run_reconciles_and_creates_a_receipt_without_writes() -> None:
    gateway = FakeWikidataGateway(
        observations={
            "manuscript:1": TargetObservation.absent("manuscript:1"),
            "work:2": TargetObservation.present_foreign(
                "work:2",
                qid="Q200",
                remote_revision=91,
            ),
        }
    )
    module = create_in_memory_publication_module(
        entities_by_snapshot={
            "snapshot-7": (
                PublicationEntityInput(
                    entity_key="manuscript:1",
                    entity_type="manuscript",
                    document={"labels": {"en": "Manuscript 1"}},
                ),
                PublicationEntityInput(
                    entity_key="work:2",
                    entity_type="work",
                    document={"labels": {"he": "ספר ב"}},
                ),
            )
        },
        gateway=gateway,
        clock=lambda: datetime(2026, 9, 5, 9, 0, tzinfo=UTC),
    )
    prepared_operation = await module.prepare(_prepare_request())
    prepared = await module.read(SummaryQuery(prepared_operation.publication_id))
    await module.advance(
        prepared.publication_id,
        ReviewCommand(
            release_id=prepared.release_id,
            expected_release_digest=prepared.release_digest,
            selection=EntitySelection.all(),
            decision="approve",
            actor_id="curator-1",
            reason="Ready for target reconciliation.",
            idempotency_key="review-before-dry-run",
        ),
    )
    reviewed = await module.read(SummaryQuery(prepared.publication_id))

    blocked_operation = await module.advance(
        prepared.publication_id,
        DryRunCommand(
            approval_set_id=reviewed.approval_set_id or "",
            expected_approval_digest=reviewed.approval_digest or "",
            credential_ref="credential:wikidata:curator-1",
            actor_id="curator-1",
            idempotency_key="dry-run-stale-consent",
            foreign_qid_consents=(
                ForeignQidConsent(
                    entity_key="work:2",
                    qid="Q200",
                    remote_revision=90,
                    entity_digest=(
                        await module.read(
                            EntityPageQuery(
                                publication_id=prepared.publication_id,
                                release_id=prepared.release_id,
                            )
                        )
                    ).items[1].entity_digest,
                ),
            ),
        ),
    )
    blocked = await module.read(SummaryQuery(prepared.publication_id))
    assert blocked_operation.resource_type == "plan"
    assert blocked.state == "dry_run_ready"
    assert blocked.plan_blocked_count == 1

    entity_page = await module.read(
        EntityPageQuery(
            publication_id=prepared.publication_id,
            release_id=prepared.release_id,
        )
    )
    work = next(entity for entity in entity_page.items if entity.entity_key == "work:2")
    operation = await module.advance(
        prepared.publication_id,
        DryRunCommand(
            approval_set_id=reviewed.approval_set_id or "",
            expected_approval_digest=reviewed.approval_digest or "",
            credential_ref="credential:wikidata:curator-1",
            actor_id="curator-1",
            idempotency_key="dry-run-7",
            foreign_qid_consents=(
                ForeignQidConsent(
                    entity_key="work:2",
                    qid="Q200",
                    remote_revision=91,
                    entity_digest=work.entity_digest,
                ),
            ),
        ),
    )
    summary = await module.read(SummaryQuery(prepared.publication_id))

    assert operation.resource_type == "plan"
    assert summary.state == "publishable"
    assert summary.plan_id == operation.resource_id
    assert summary.plan_digest
    assert summary.dry_run_receipt_id
    assert summary.dry_run_receipt_digest
    assert summary.plan_create_count == 1
    assert summary.plan_update_count == 1
    assert summary.plan_blocked_count == 0
    assert gateway.open_count == 2
    assert gateway.write_calls == ()


@pytest.mark.asyncio
async def test_publish_resumes_from_the_durable_write_journal() -> None:
    gateway = FakeWikidataGateway(
        observations={
            "manuscript:1": TargetObservation.absent("manuscript:1"),
        },
        write_outcomes={
            "manuscript:1": (WriteOutcome.outcome_unknown("The connection closed after send"),)
        },
        recovery_outcomes={
            "manuscript:1": WriteOutcome.succeeded(
                qid="Q9001",
                fingerprint="target-v1",
            )
        },
    )
    module = create_in_memory_publication_module(
        entities_by_snapshot={
            "snapshot-7": (
                PublicationEntityInput(
                    entity_key="manuscript:1",
                    entity_type="manuscript",
                    document={"labels": {"en": "Manuscript 1"}},
                ),
            )
        },
        gateway=gateway,
        clock=lambda: datetime(2026, 9, 5, 9, 0, tzinfo=UTC),
    )
    prepared_operation = await module.prepare(_prepare_request())
    prepared = await module.read(SummaryQuery(prepared_operation.publication_id))
    await module.advance(
        prepared.publication_id,
        ReviewCommand(
            release_id=prepared.release_id,
            expected_release_digest=prepared.release_digest,
            selection=EntitySelection.all(),
            decision="approve",
            actor_id="curator-1",
            reason="Ready to publish.",
            idempotency_key="execution-review",
        ),
    )
    reviewed = await module.read(SummaryQuery(prepared.publication_id))
    await module.advance(
        prepared.publication_id,
        DryRunCommand(
            approval_set_id=reviewed.approval_set_id or "",
            expected_approval_digest=reviewed.approval_digest or "",
            credential_ref="credential:wikidata:curator-1",
            actor_id="curator-1",
            idempotency_key="execution-dry-run",
        ),
    )
    publishable = await module.read(SummaryQuery(prepared.publication_id))

    publish = await module.advance(
        prepared.publication_id,
        PublishCommand(
            plan_id=publishable.plan_id or "",
            dry_run_receipt_id=publishable.dry_run_receipt_id or "",
            expected_receipt_digest=publishable.dry_run_receipt_digest or "",
            credential_ref="credential:wikidata:curator-1",
            actor_id="curator-1",
            idempotency_key="publish-7",
        ),
    )
    queued = await module.read(SummaryQuery(prepared.publication_id))

    assert publish.resource_type == "execution"
    assert queued.execution_status == "queued"
    assert gateway.write_calls == ()
    await module.advance(
        prepared.publication_id,
        ResumeCommand(
            execution_id=publish.resource_id,
            credential_ref="credential:wikidata:curator-1",
            actor_id="publication-worker",
            idempotency_key="worker-attempt-1",
        ),
    )
    paused = await module.read(SummaryQuery(prepared.publication_id))

    assert paused.state == "publishing"
    assert paused.execution_status == "paused"
    assert paused.execution_outcome_unknown_count == 1
    first_audit_page = await module.read(
        AuditQuery(publication_id=prepared.publication_id, limit=100)
    )
    assert [entry.state for entry in first_audit_page.items][-2:] == [
        "in_flight",
        "outcome_unknown",
    ]

    await module.advance(
        prepared.publication_id,
        ResumeCommand(
            execution_id=publish.resource_id,
            credential_ref="credential:wikidata:curator-1",
            actor_id="curator-1",
            idempotency_key="resume-7",
        ),
    )
    completed = await module.read(SummaryQuery(prepared.publication_id))

    assert completed.state == "completed"
    assert completed.execution_status == "succeeded"
    assert completed.execution_succeeded_count == 1
    assert len(gateway.write_calls) == 1
    assert len(gateway.recovery_calls) == 1


@pytest.mark.asyncio
async def test_cancel_stops_resume_before_gateway_recovery() -> None:
    gateway = FakeWikidataGateway(
        observations={"manuscript:1": TargetObservation.absent("manuscript:1")},
        write_outcomes={
            "manuscript:1": (
                WriteOutcome.outcome_unknown("The connection closed after send"),
            )
        },
    )
    module = create_in_memory_publication_module(
        entities_by_snapshot={
            "snapshot-7": (
                PublicationEntityInput(
                    entity_key="manuscript:1",
                    entity_type="manuscript",
                    document={"labels": {"en": "Manuscript 1"}},
                ),
            )
        },
        gateway=gateway,
        clock=lambda: datetime(2026, 9, 5, 9, 0, tzinfo=UTC),
    )
    prepared_operation = await module.prepare(_prepare_request())
    prepared = await module.read(SummaryQuery(prepared_operation.publication_id))
    await module.advance(
        prepared.publication_id,
        ReviewCommand(
            release_id=prepared.release_id,
            expected_release_digest=prepared.release_digest,
            selection=EntitySelection.all(),
            decision="approve",
            actor_id="curator-1",
            reason="Ready to publish.",
            idempotency_key="cancel-review",
        ),
    )
    reviewed = await module.read(SummaryQuery(prepared.publication_id))
    await module.advance(
        prepared.publication_id,
        DryRunCommand(
            approval_set_id=reviewed.approval_set_id or "",
            expected_approval_digest=reviewed.approval_digest or "",
            credential_ref="credential:wikidata:curator-1",
            actor_id="curator-1",
            idempotency_key="cancel-dry-run",
        ),
    )
    publishable = await module.read(SummaryQuery(prepared.publication_id))
    published = await module.advance(
        prepared.publication_id,
        PublishCommand(
            plan_id=publishable.plan_id or "",
            dry_run_receipt_id=publishable.dry_run_receipt_id or "",
            expected_receipt_digest=publishable.dry_run_receipt_digest or "",
            credential_ref="credential:wikidata:curator-1",
            actor_id="curator-1",
            idempotency_key="cancel-publish",
        ),
    )
    assert gateway.write_calls == ()
    await module.advance(
        prepared.publication_id,
        ResumeCommand(
            execution_id=published.resource_id,
            credential_ref="credential:wikidata:curator-1",
            actor_id="publication-worker",
            idempotency_key="cancel-worker-attempt",
        ),
    )

    await module.advance(
        prepared.publication_id,
        CancelCommand(
            execution_id=published.resource_id,
            actor_id="curator-1",
            reason="The curator revoked the publication.",
            idempotency_key="cancel-1",
        ),
    )
    cancelled = await module.read(SummaryQuery(prepared.publication_id))

    assert cancelled.state == "cancelled"
    assert cancelled.execution_status == "cancelled"
    with pytest.raises(CancelledExecutionError):
        await module.advance(
            prepared.publication_id,
            ResumeCommand(
                execution_id=published.resource_id,
                credential_ref="credential:wikidata:curator-1",
                actor_id="curator-1",
                idempotency_key="resume-after-cancel",
            ),
        )
    assert gateway.recovery_calls == ()


@pytest.mark.asyncio
async def test_worker_drains_more_than_one_claim_batch() -> None:
    entities = tuple(
        PublicationEntityInput(
            entity_key=f"manuscript:{index:03d}",
            entity_type="manuscript",
            document={"labels": {"en": f"Manuscript {index}"}},
        )
        for index in range(51)
    )
    gateway = FakeWikidataGateway(
        observations={
            entity.entity_key: TargetObservation.absent(entity.entity_key)
            for entity in entities
        }
    )
    module = create_in_memory_publication_module(
        entities_by_snapshot={"snapshot-7": entities},
        gateway=gateway,
        clock=lambda: datetime(2026, 9, 5, 9, 0, tzinfo=UTC),
    )
    prepared_operation = await module.prepare(_prepare_request())
    prepared = await module.read(SummaryQuery(prepared_operation.publication_id))
    await module.advance(
        prepared.publication_id,
        ReviewCommand(
            release_id=prepared.release_id,
            expected_release_digest=prepared.release_digest,
            selection=EntitySelection.all(),
            decision="approve",
            actor_id="curator-1",
            reason="Ready for a multi-batch execution.",
            idempotency_key="batch-review",
        ),
    )
    reviewed = await module.read(SummaryQuery(prepared.publication_id))
    await module.advance(
        prepared.publication_id,
        DryRunCommand(
            approval_set_id=reviewed.approval_set_id or "",
            expected_approval_digest=reviewed.approval_digest or "",
            credential_ref="credential:wikidata:curator-1",
            actor_id="curator-1",
            idempotency_key="batch-dry-run",
        ),
    )
    publishable = await module.read(SummaryQuery(prepared.publication_id))
    published = await module.advance(
        prepared.publication_id,
        PublishCommand(
            plan_id=publishable.plan_id or "",
            dry_run_receipt_id=publishable.dry_run_receipt_id or "",
            expected_receipt_digest=publishable.dry_run_receipt_digest or "",
            credential_ref="credential:wikidata:curator-1",
            actor_id="curator-1",
            idempotency_key="batch-publish",
        ),
    )

    await module.advance(
        prepared.publication_id,
        ResumeCommand(
            execution_id=published.resource_id,
            credential_ref="credential:wikidata:curator-1",
            actor_id="publication-worker",
            idempotency_key="batch-worker",
        ),
    )
    completed = await module.read(SummaryQuery(prepared.publication_id))

    assert completed.execution_status == "succeeded"
    assert completed.execution_succeeded_count == 51
    assert len(gateway.write_calls) == 51
