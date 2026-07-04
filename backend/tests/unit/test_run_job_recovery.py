"""Startup recovery, claiming, and maintenance for background jobs."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import update

from app.db import session_scope
from app.models.run_job import (
    JOB_KIND_EXTRACTION,
    JOB_KIND_RDF_BUILD,
    JOB_KIND_WIKIDATA_UPLOAD,
    JOB_STATUS_FAILED,
    JOB_STATUS_QUEUED,
    JOB_STATUS_RUNNING,
    JOB_STATUS_SUCCEEDED,
    RunJob,
)
from app.pipeline import run_job_service
from app.pipeline.run_job_service import (
    STALE_JOB_AFTER,
    WORKER_DYNO,
    WORKER_ID,
    ActiveJobError,
    _heartbeat_owned_jobs,
    _now,
    _try_claim_job,
    create_job,
    recover_interrupted_jobs,
    run_job_maintenance_tick,
)


class _FakeTask:
    def done(self) -> bool:
        return False


async def _add_job(
    db,
    sample_run,
    *,
    kind: str = JOB_KIND_RDF_BUILD,
    status: str = JOB_STATUS_RUNNING,
    claimed_by: str | None = None,
) -> RunJob:
    job = RunJob(
        project_id=sample_run["project_id"],
        run_id=sample_run["run_id"],
        kind=kind,
        status=status,
        params={},
        progress={},
        created_by=sample_run["user_id"],
        claimed_by=claimed_by,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return job


async def _backdate(db, job_id: uuid.UUID, *, by: timedelta) -> None:
    await db.execute(
        update(RunJob)
        .where(RunJob.id == job_id)
        .values(updated_at=_now() - by)
        .execution_options(synchronize_session=False)
    )
    await db.commit()


@pytest.mark.asyncio
async def test_recover_interrupted_jobs_respawns_active_rows(
    db_session, sample_run, monkeypatch,
) -> None:
    spawned: list[uuid.UUID] = []
    monkeypatch.setattr(
        "app.pipeline.run_job_service.spawn_job",
        lambda job_id: spawned.append(job_id),
    )

    job = RunJob(
        project_id=sample_run["project_id"],
        run_id=sample_run["run_id"],
        kind=JOB_KIND_RDF_BUILD,
        status=JOB_STATUS_RUNNING,
        params={},
        progress={"processed": 0, "total": 10},
        created_by=sample_run["user_id"],
    )
    db_session.add(job)
    await db_session.commit()

    count = await recover_interrupted_jobs()
    assert count == 1
    assert spawned == [job.id]


@pytest.mark.asyncio
async def test_try_claim_job_claims_queued_row(db_session, sample_run) -> None:
    job = await _add_job(db_session, sample_run, status=JOB_STATUS_QUEUED)

    assert await _try_claim_job(db_session, job.id) is True

    await db_session.refresh(job)
    assert job.status == JOB_STATUS_RUNNING
    assert job.claimed_by == WORKER_ID
    assert job.started_at is not None


@pytest.mark.asyncio
async def test_try_claim_job_rejects_fresh_foreign_running_row(
    db_session, sample_run,
) -> None:
    job = await _add_job(db_session, sample_run, claimed_by="other-dyno:aaaa1111")

    assert await _try_claim_job(db_session, job.id) is False

    await db_session.refresh(job)
    assert job.claimed_by == "other-dyno:aaaa1111"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "claimed_by",
    [None, WORKER_ID, f"{WORKER_DYNO}:deadbeef"],
)
async def test_try_claim_job_reclaims_unowned_or_own_dyno_rows(
    db_session, sample_run, claimed_by,
) -> None:
    job = await _add_job(db_session, sample_run, claimed_by=claimed_by)

    assert await _try_claim_job(db_session, job.id) is True

    await db_session.refresh(job)
    assert job.claimed_by == WORKER_ID


@pytest.mark.asyncio
async def test_try_claim_job_reclaims_stale_foreign_row(
    db_session, sample_run,
) -> None:
    job = await _add_job(db_session, sample_run, claimed_by="other-dyno:aaaa1111")
    await _backdate(db_session, job.id, by=STALE_JOB_AFTER + timedelta(minutes=1))

    assert await _try_claim_job(db_session, job.id) is True

    await db_session.refresh(job)
    assert job.claimed_by == WORKER_ID


@pytest.mark.asyncio
async def test_heartbeat_bumps_only_live_owned_running_rows(
    db_session, sample_run, monkeypatch,
) -> None:
    backdate_by = STALE_JOB_AFTER + timedelta(minutes=1)
    owned = await _add_job(db_session, sample_run, claimed_by=WORKER_ID)
    foreign = await _add_job(
        db_session, sample_run,
        kind=JOB_KIND_EXTRACTION, claimed_by="other-dyno:aaaa1111",
    )
    finished = await _add_job(
        db_session, sample_run,
        kind=JOB_KIND_WIKIDATA_UPLOAD,
        status=JOB_STATUS_SUCCEEDED, claimed_by=WORKER_ID,
    )
    for job in (owned, foreign, finished):
        await _backdate(db_session, job.id, by=backdate_by)
    monkeypatch.setattr(
        run_job_service,
        "_background_tasks",
        {str(j.id): _FakeTask() for j in (owned, foreign, finished)},
    )

    assert await _heartbeat_owned_jobs() == 1

    cutoff = _now() - STALE_JOB_AFTER
    async with session_scope() as db:
        assert (await db.get(RunJob, owned.id)).updated_at > cutoff
        assert (await db.get(RunJob, foreign.id)).updated_at < cutoff
        assert (await db.get(RunJob, finished.id)).updated_at < cutoff


@pytest.mark.asyncio
async def test_maintenance_tick_heartbeats_reaps_and_respawns(
    db_session, sample_run, monkeypatch,
) -> None:
    backdate_by = STALE_JOB_AFTER + timedelta(minutes=1)
    owned_live = await _add_job(db_session, sample_run, claimed_by=WORKER_ID)
    foreign_dead = await _add_job(
        db_session, sample_run,
        kind=JOB_KIND_EXTRACTION, claimed_by="other-dyno:aaaa1111",
    )
    orphan_queued = await _add_job(
        db_session, sample_run,
        kind=JOB_KIND_WIKIDATA_UPLOAD, status=JOB_STATUS_QUEUED,
    )
    for job in (owned_live, foreign_dead, orphan_queued):
        await _backdate(db_session, job.id, by=backdate_by)
    monkeypatch.setattr(
        run_job_service, "_background_tasks", {str(owned_live.id): _FakeTask()},
    )
    spawned: list[uuid.UUID] = []
    monkeypatch.setattr(
        run_job_service, "spawn_job", lambda job_id: spawned.append(job_id),
    )

    await run_job_maintenance_tick()

    async with session_scope() as db:
        assert (await db.get(RunJob, owned_live.id)).status == JOB_STATUS_RUNNING
        reaped = await db.get(RunJob, foreign_dead.id)
        assert reaped.status == JOB_STATUS_FAILED
        assert "interrupted" in (reaped.error or "")
        assert (await db.get(RunJob, orphan_queued.id)).status == JOB_STATUS_QUEUED
    assert spawned == [orphan_queued.id]


@pytest.mark.asyncio
async def test_create_job_race_loses_to_unique_index(
    db_session, sample_run, monkeypatch,
) -> None:
    existing = await _add_job(db_session, sample_run, status=JOB_STATUS_QUEUED)
    real_find = run_job_service.find_active_job

    async def _find_skipping_precheck(db, *, run_id, kind):
        # First call (create_job's pre-check) pretends no job exists so the
        # INSERT proceeds and hits the partial unique index; the recovery
        # lookup inside the except-branch uses the real implementation.
        if not getattr(_find_skipping_precheck, "raced", False):
            _find_skipping_precheck.raced = True
            return None
        return await real_find(db, run_id=run_id, kind=kind)

    monkeypatch.setattr(run_job_service, "find_active_job", _find_skipping_precheck)

    with pytest.raises(ActiveJobError) as exc_info:
        await create_job(
            db_session,
            project_id=sample_run["project_id"],
            run_id=sample_run["run_id"],
            kind=JOB_KIND_RDF_BUILD,
            params={},
            created_by=sample_run["user_id"],
        )
    assert exc_info.value.job_id == existing.id
