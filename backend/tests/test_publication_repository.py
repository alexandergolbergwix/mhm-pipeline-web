"""Integration tests for the SQL publication repository adapter."""

from __future__ import annotations

import importlib
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

from app.models.publication import PublicationExecution, PublicationExecutionAction
from app.publication import (
    AuditQuery,
    DryRunCommand,
    EntityPageQuery,
    EntitySelection,
    PrepareRequest,
    ProfileRef,
    PublicationEntityInput,
    PublicationModule,
    PublishCommand,
    ResumeCommand,
    ReviewCommand,
    SourceSnapshotRef,
    SummaryQuery,
    TargetRef,
)
from app.publication.gateway import (
    FakeWikidataGateway,
    GatewayWriteRequest,
    TargetObservation,
    WriteOutcome,
)
from app.publication.sql_repository import SqlAlchemyPublicationRepository
from app.publication.testing import StaticProjectionSource


class _TransactionBoundaryGateway(FakeWikidataGateway):
    def __init__(self, db_session, **kwargs) -> None:
        super().__init__(**kwargs)
        self._db_session = db_session
        self.transactions_at_write: list[bool] = []

    async def write(self, request: GatewayWriteRequest) -> WriteOutcome:
        self.transactions_at_write.append(self._db_session.in_transaction())
        return await super().write(request)


def test_publication_migration_chains_from_the_current_head() -> None:
    migration = importlib.import_module("app.migrations.versions.0040_publication_core")

    assert migration.revision == "0040_publication_core"
    assert migration.down_revision == "0039_public_abstain_provider_err"


@pytest.mark.asyncio
async def test_sql_repository_persists_the_publication_lifecycle(
    db_session,
    sample_run,
) -> None:
    run_id = str(sample_run["run_id"])
    entities = (
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
    gateway = _TransactionBoundaryGateway(
        db_session,
        observations={
            "manuscript:1": TargetObservation.absent("manuscript:1"),
            "work:2": TargetObservation.present_owned("work:2", qid="Q200"),
        },
    )
    repository = SqlAlchemyPublicationRepository(db_session)
    module = PublicationModule(
        projection_source=StaticProjectionSource({"snapshot-sql": entities}),
        repository=repository,
        gateway=gateway,
        clock=lambda: datetime(2026, 9, 5, 9, 0, tzinfo=UTC),
    )
    prepared_operation = await module.prepare(
        PrepareRequest(
            run_id=run_id,
            source_snapshot=SourceSnapshotRef(
                snapshot_id="snapshot-sql",
                revision="approved-v9",
                digest="9" * 64,
            ),
            profile=ProfileRef(name="mhm-wikidata", version="1"),
            target=TargetRef(site="www.wikidata.org", environment="production"),
            actor_id=str(sample_run["user_id"]),
            idempotency_key="prepare-sql",
        )
    )
    prepared = await module.read(SummaryQuery(prepared_operation.publication_id))
    await module.advance(
        prepared.publication_id,
        ReviewCommand(
            release_id=prepared.release_id,
            expected_release_digest=prepared.release_digest,
            selection=EntitySelection.all(),
            decision="approve",
            actor_id=str(sample_run["user_id"]),
            reason="The SQL integration test approves the immutable Release.",
            idempotency_key="review-sql",
        ),
    )
    reviewed = await module.read(SummaryQuery(prepared.publication_id))
    await module.advance(
        prepared.publication_id,
        DryRunCommand(
            approval_set_id=reviewed.approval_set_id or "",
            expected_approval_digest=reviewed.approval_digest or "",
            credential_ref="credential:wikidata:test",
            actor_id=str(sample_run["user_id"]),
            idempotency_key="dry-run-sql",
        ),
    )
    publishable = await module.read(SummaryQuery(prepared.publication_id))
    await module.advance(
        prepared.publication_id,
        PublishCommand(
            plan_id=publishable.plan_id or "",
            dry_run_receipt_id=publishable.dry_run_receipt_id or "",
            expected_receipt_digest=publishable.dry_run_receipt_digest or "",
            credential_ref="credential:wikidata:test",
            actor_id=str(sample_run["user_id"]),
            idempotency_key="publish-sql",
        ),
    )
    queued = await module.read(SummaryQuery(prepared.publication_id))
    assert queued.execution_status == "queued"
    assert gateway.write_calls == ()
    await module.advance(
        prepared.publication_id,
        ResumeCommand(
            execution_id=queued.execution_id or "",
            credential_ref="credential:wikidata:test",
            actor_id="publication-worker",
            idempotency_key="worker-sql",
        ),
    )
    await db_session.commit()

    restarted = PublicationModule(
        projection_source=StaticProjectionSource({}),
        repository=SqlAlchemyPublicationRepository(db_session),
        gateway=FakeWikidataGateway(),
    )
    completed = await restarted.read(SummaryQuery(prepared.publication_id))
    first_page = await restarted.read(
        EntityPageQuery(
            publication_id=prepared.publication_id,
            release_id=prepared.release_id,
            limit=1,
        )
    )
    second_page = await restarted.read(
        EntityPageQuery(
            publication_id=prepared.publication_id,
            release_id=prepared.release_id,
            cursor=first_page.next_cursor,
            limit=1,
        )
    )
    audit = await restarted.read(AuditQuery(prepared.publication_id, limit=100))

    assert completed.state == "completed"
    assert completed.execution_status == "succeeded"
    assert [first_page.items[0].entity_key, second_page.items[0].entity_key] == [
        "manuscript:1",
        "work:2",
    ]
    assert [entry.state for entry in audit.items] == [
        "in_flight",
        "succeeded",
        "in_flight",
        "succeeded",
    ]
    assert gateway.transactions_at_write == [False, False]

    await db_session.execute(
        update(PublicationExecutionAction)
        .where(
            PublicationExecutionAction.execution_id == uuid.UUID(completed.execution_id),
        )
        .values(
            state="pending",
            lease_owner=None,
            lease_expires_at=None,
        )
    )
    await db_session.execute(
        update(PublicationExecution)
        .where(PublicationExecution.id == uuid.UUID(completed.execution_id))
        .values(status="running")
    )
    await db_session.commit()
    now = datetime(2026, 9, 5, 9, 5, tzinfo=UTC)
    first_claim = await repository.claim_execution_actions(
        completed.execution_id,
        worker_id="worker-1",
        now=now,
        lease_duration=timedelta(minutes=1),
        limit=1,
    )
    second_claim = await repository.claim_execution_actions(
        completed.execution_id,
        worker_id="worker-2",
        now=now,
        lease_duration=timedelta(minutes=1),
        limit=1,
    )
    no_claim = await repository.claim_execution_actions(
        completed.execution_id,
        worker_id="worker-3",
        now=now,
        lease_duration=timedelta(minutes=1),
        limit=1,
    )

    assert len(first_claim) == 1
    assert len(second_claim) == 1
    assert first_claim[0].action_key != second_claim[0].action_key
    assert no_claim == ()
