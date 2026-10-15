"""Regression tests for the queued Publication Execution worker."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.models.publication import PublicationExecution
from app.models.run_job import (
    JOB_KIND_WIKIDATA_PUBLICATION_EXECUTION,
    JOB_STATUS_FAILED,
    JOB_STATUS_RUNNING,
    JOB_STATUS_SUCCEEDED,
    RunJob,
)
from app.pipeline.wikidata_publication_execution_job import (
    run_wikidata_publication_execution_job,
)


@pytest.mark.asyncio
async def test_execution_worker_uses_references_and_never_a_plaintext_secret(
    sample_run,
    db_session,
    monkeypatch,
) -> None:
    execution_id = str(uuid.uuid4())
    publication_id = str(uuid.uuid4())
    actor_id = str(sample_run["user_id"])
    job = RunJob(
        project_id=sample_run["project_id"],
        run_id=sample_run["run_id"],
        kind=JOB_KIND_WIKIDATA_PUBLICATION_EXECUTION,
        status=JOB_STATUS_RUNNING,
        params={
            "publication_id": publication_id,
            "execution_id": execution_id,
            "actor_id": actor_id,
        },
        progress={},
        created_by=sample_run["user_id"],
    )
    db_session.add(job)
    await db_session.commit()

    seen: dict[str, object] = {}

    class FakeRuntime:
        def __init__(self, *, session, gateway_factory, execution_progress=None) -> None:
            seen["session"] = session
            seen["gateway_factory"] = gateway_factory

        async def execute(self, **kwargs):
            seen["execute"] = kwargs
            return SimpleNamespace(
                execution=SimpleNamespace(
                    execution_id=execution_id,
                    status="succeeded",
                    processed=2,
                    total=2,
                    current_entity_label=None,
                )
            )

    monkeypatch.setattr(
        "app.pipeline.wikidata_publication_execution_job.PublicationRuntime",
        FakeRuntime,
    )
    await run_wikidata_publication_execution_job(job.id)

    await db_session.refresh(job)
    assert job.status == JOB_STATUS_SUCCEEDED
    assert job.params == {
        "publication_id": publication_id,
        "execution_id": execution_id,
        "actor_id": actor_id,
    }
    assert seen["execute"] == {
        "run_id": sample_run["run_id"],
        "publication_id": publication_id,
        "execution_id": execution_id,
        "actor_id": actor_id,
        "worker_id": f"publication-job:{job.id}",
    }


@pytest.mark.asyncio
async def test_execution_worker_reports_completed_item_failures_without_pause(
    sample_run,
    db_session,
    monkeypatch,
) -> None:
    execution_id = str(uuid.uuid4())
    publication_id = str(uuid.uuid4())
    actor_id = str(sample_run["user_id"])
    job = RunJob(
        project_id=sample_run["project_id"],
        run_id=sample_run["run_id"],
        kind=JOB_KIND_WIKIDATA_PUBLICATION_EXECUTION,
        status=JOB_STATUS_RUNNING,
        params={
            "publication_id": publication_id,
            "execution_id": execution_id,
            "actor_id": actor_id,
        },
        progress={},
        created_by=sample_run["user_id"],
    )
    db_session.add(job)
    await db_session.commit()

    class CompletedWithFailuresRuntime:
        def __init__(self, *, session, gateway_factory, execution_progress=None) -> None:
            pass

        async def execute(self, **kwargs):
            return SimpleNamespace(
                execution=SimpleNamespace(
                    execution_id=execution_id,
                    status="failed",
                    processed=2,
                    total=2,
                    current_entity_label=None,
                    failed=1,
                )
            )

    monkeypatch.setattr(
        "app.pipeline.wikidata_publication_execution_job.PublicationRuntime",
        CompletedWithFailuresRuntime,
    )
    await run_wikidata_publication_execution_job(job.id)

    await db_session.refresh(job)
    assert job.status == JOB_STATUS_FAILED
    assert job.error == (
        "The Publication Execution finished with 1 item that needs attention. "
        "Review the audit before starting a new Release."
    )


@pytest.mark.asyncio
async def test_execution_worker_explains_pause_after_safe_items_finish(
    sample_run,
    db_session,
    monkeypatch,
) -> None:
    execution_id = str(uuid.uuid4())
    publication_id = str(uuid.uuid4())
    actor_id = str(sample_run["user_id"])
    job = RunJob(
        project_id=sample_run["project_id"],
        run_id=sample_run["run_id"],
        kind=JOB_KIND_WIKIDATA_PUBLICATION_EXECUTION,
        status=JOB_STATUS_RUNNING,
        params={
            "publication_id": publication_id,
            "execution_id": execution_id,
            "actor_id": actor_id,
        },
        progress={},
        created_by=sample_run["user_id"],
    )
    db_session.add(job)
    await db_session.commit()

    class PausedRuntime:
        def __init__(self, *, session, gateway_factory, execution_progress=None) -> None:
            pass

        async def execute(self, **kwargs):
            return SimpleNamespace(
                execution=SimpleNamespace(
                    execution_id=execution_id,
                    status="paused",
                    processed=2,
                    total=2,
                    current_entity_label=None,
                    failed=1,
                )
            )

    monkeypatch.setattr(
        "app.pipeline.wikidata_publication_execution_job.PublicationRuntime",
        PausedRuntime,
    )
    await run_wikidata_publication_execution_job(job.id)

    await db_session.refresh(job)
    assert job.status == JOB_STATUS_FAILED
    assert job.error == (
        "The Publication Execution processed all safe items and paused with 1 item "
        "unresolved. Resume it to retry the unresolved items."
    )


@pytest.mark.asyncio
async def test_execution_worker_pauses_durable_execution_when_runtime_fails(
    sample_run,
    db_session,
    monkeypatch,
) -> None:
    execution_id = uuid.uuid4()
    publication_id = uuid.uuid4()
    actor_id = str(sample_run["user_id"])
    db_session.add(PublicationExecution(
        id=execution_id,
        publication_id=publication_id,
        plan_id=uuid.uuid4(),
        receipt_id=uuid.uuid4(),
        receipt_digest="r" * 64,
        actor_id=actor_id,
        idempotency_key="worker-failure",
        status="running",
        total_count=1,
    ))
    job = RunJob(
        project_id=sample_run["project_id"],
        run_id=sample_run["run_id"],
        kind=JOB_KIND_WIKIDATA_PUBLICATION_EXECUTION,
        status=JOB_STATUS_RUNNING,
        params={
            "publication_id": str(publication_id),
            "execution_id": str(execution_id),
            "actor_id": actor_id,
        },
        progress={},
        created_by=sample_run["user_id"],
    )
    db_session.add(job)
    await db_session.commit()

    class FailingRuntime:
        def __init__(self, *, session, gateway_factory, execution_progress=None) -> None:
            pass

        async def execute(self, **kwargs):
            raise RuntimeError("database schema mismatch")

    monkeypatch.setattr(
        "app.pipeline.wikidata_publication_execution_job.PublicationRuntime",
        FailingRuntime,
    )
    with pytest.raises(RuntimeError, match="database schema mismatch"):
        await run_wikidata_publication_execution_job(job.id)

    await db_session.refresh(job)
    execution = await db_session.get(PublicationExecution, execution_id)
    assert execution is not None
    assert execution.status == "paused"
    assert job.status == JOB_STATUS_RUNNING


@pytest.mark.asyncio
async def test_execution_records_created_qids_into_the_ledger(
    sample_run, db_session, monkeypatch,
) -> None:
    """Succeeded actions land in the global ledger for phase-2 linking."""
    import uuid as _uuid

    from sqlalchemy import select

    from app.models.publication import (
        Publication,
        PublicationEntityRow,
        PublicationExecution,
        PublicationExecutionAction,
        PublicationPlan,
        PublicationRelease,
    )
    from app.models.wikibase_entity_mapping import WikibaseEntityMapping
    from app.pipeline.wikidata_publication_execution_job import (
        run_wikidata_publication_execution_job,
    )

    publication_id = _uuid.uuid4()
    release_id = _uuid.uuid4()
    plan_id = _uuid.uuid4()
    execution_id = _uuid.uuid4()
    actor_id = str(sample_run["user_id"])
    db_session.add_all([
        Publication(
            id=publication_id,
            run_id=sample_run["run_id"],
            source_snapshot_id="run:legacy:0:0",
            source_revision="approved-v7",
            source_digest="4" * 64,
            profile_name="mhm-wikidata",
            profile_version="1-nodes",
            target_site="test.wikidata.org",
            target_environment="test",
            state="completed",
            latest_release_id=release_id,
            idempotency_key="ledger-test",
        ),
        PublicationRelease(id=release_id, publication_id=publication_id, status="sealed",
                           entity_count=1, finding_count=0),
        PublicationPlan(id=plan_id, publication_id=publication_id, status="sealed",
                        release_id=release_id, plan_digest="d" * 64),
        PublicationExecution(id=execution_id, publication_id=publication_id,
                             plan_id=plan_id, status="succeeded", total_count=1,
                             receipt_id=_uuid.uuid4(), receipt_digest="e" * 64,
                             actor_id=actor_id, idempotency_key="exec-ledger"),
        PublicationEntityRow(release_id=release_id, entity_key="QDraft_MS_1",
                          entity_type="manuscript", entity_digest="1" * 64,
                          document={}),
        PublicationExecutionAction(
            execution_id=execution_id, action_key="a1", entity_key="QDraft_MS_1",
            ordinal=1, phase="write", state="succeeded", action="create",
            result_qid="Q9001", attempt_count=1,
        ),
    ])
    await db_session.commit()

    class _Runtime:
        def __init__(self, *, session, gateway_factory, execution_progress=None):
            pass

        async def execute(self, **kwargs):
            return SimpleNamespace(execution=SimpleNamespace(
                status="succeeded", processed=1, total=1,
                current_entity_label=None,
            ))

    import app.pipeline.wikidata_publication_execution_job as job_module

    monkeypatch.setattr(job_module, "PublicationRuntime", _Runtime)
    job_id = _uuid.uuid4()
    db_session.add(RunJob(
        id=job_id, project_id=sample_run["project_id"], run_id=sample_run["run_id"],
        kind="wikidata_publication_execution", status="running",
        created_by=sample_run["user_id"],
        params={"publication_id": str(publication_id),
                "execution_id": str(execution_id), "actor_id": actor_id},
    ))
    await db_session.commit()

    await run_wikidata_publication_execution_job(job_id)

    from app.db import session_scope

    async with session_scope() as verify_db:
        row = (
            await verify_db.execute(
                select(WikibaseEntityMapping).where(
                    WikibaseEntityMapping.ontology_uri
                    == "wikidata-test:marc:QDraft_MS_1",
                )
            )
        ).scalar_one_or_none()
    assert row is not None
    assert row.wikibase_id == "Q9001"
