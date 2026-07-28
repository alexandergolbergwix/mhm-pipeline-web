"""Startup recovery, claiming, and maintenance for background jobs."""

from __future__ import annotations

import uuid
from datetime import timedelta

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import update

from app.db import session_scope
from app.models.run_job import (
    JOB_KIND_EXTRACTION,
    JOB_KIND_HMO_ITEM_VERIFY,
    JOB_KIND_NER_VERIFY,
    JOB_KIND_RDF_BUILD,
    JOB_KIND_WIKIDATA_UPLOAD,
    JOB_KIND_WIKIDATA_VERIFY,
    JOB_STATUS_FAILED,
    JOB_STATUS_QUEUED,
    JOB_STATUS_RUNNING,
    JOB_STATUS_SUCCEEDED,
    RunJob,
)
from app.pipeline import run_job_service
from app.pipeline.run_job_service import (
    CAPACITY_WAIT_MESSAGE,
    STALE_JOB_AFTER,
    WORKER_DYNO,
    WORKER_ID,
    ActiveJobError,
    _heartbeat_owned_jobs,
    _now,
    _try_claim_job,
    _try_claim_with_admission,
    admit_waiting_jobs,
    create_job,
    finish_job,
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

    async def _noop_admit() -> int:
        return 0

    monkeypatch.setattr(run_job_service, "admit_waiting_jobs", _noop_admit)

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


@pytest.mark.asyncio
async def test_admission_blocks_second_verify_while_first_running(
    db_session, sample_run, monkeypatch,
) -> None:
    monkeypatch.setenv("RUN_JOB_MAX_VERIFY", "1")
    monkeypatch.setenv("RUN_JOB_MAX_RUNNING", "10")

    await _add_job(
        db_session, sample_run,
        kind=JOB_KIND_WIKIDATA_VERIFY,
        status=JOB_STATUS_RUNNING,
        claimed_by=WORKER_ID,
    )
    second = await _add_job(
        db_session, sample_run,
        kind=JOB_KIND_NER_VERIFY,
        status=JOB_STATUS_QUEUED,
    )

    claimed = await _try_claim_with_admission(
        db_session, second.id, JOB_KIND_NER_VERIFY, status=JOB_STATUS_QUEUED,
    )
    assert claimed is False

    await db_session.refresh(second)
    assert second.status == JOB_STATUS_QUEUED
    assert second.progress.get("message") == CAPACITY_WAIT_MESSAGE


@pytest.mark.asyncio
async def test_admission_global_cap_blocks_mixed_jobs(
    db_session, sample_run, monkeypatch,
) -> None:
    monkeypatch.setenv("RUN_JOB_MAX_RUNNING", "1")
    monkeypatch.setenv("RUN_JOB_MAX_VERIFY", "1")
    monkeypatch.setenv("RUN_JOB_MAX_BUILD", "1")

    await _add_job(
        db_session, sample_run,
        kind=JOB_KIND_WIKIDATA_VERIFY,
        status=JOB_STATUS_RUNNING,
        claimed_by=WORKER_ID,
    )
    queued_build = await _add_job(
        db_session, sample_run,
        kind=JOB_KIND_RDF_BUILD,
        status=JOB_STATUS_QUEUED,
    )

    claimed = await _try_claim_with_admission(
        db_session, queued_build.id, JOB_KIND_RDF_BUILD, status=JOB_STATUS_QUEUED,
    )
    assert claimed is False

    await db_session.refresh(queued_build)
    assert queued_build.status == JOB_STATUS_QUEUED
    assert queued_build.progress.get("message") == CAPACITY_WAIT_MESSAGE


@pytest.mark.asyncio
async def test_finish_job_admits_next_queued_verify(
    db_session, sample_run, monkeypatch,
) -> None:
    monkeypatch.setenv("RUN_JOB_MAX_VERIFY", "1")
    monkeypatch.setenv("RUN_JOB_MAX_RUNNING", "10")

    running = await _add_job(
        db_session, sample_run,
        kind=JOB_KIND_HMO_ITEM_VERIFY,
        status=JOB_STATUS_RUNNING,
        claimed_by=WORKER_ID,
    )
    waiting = await _add_job(
        db_session, sample_run,
        kind=JOB_KIND_NER_VERIFY,
        status=JOB_STATUS_QUEUED,
    )
    waiting.progress = {"phase": "queued", "message": CAPACITY_WAIT_MESSAGE}
    await db_session.commit()

    spawned: list[uuid.UUID] = []
    monkeypatch.setattr(
        run_job_service, "spawn_job", lambda job_id: spawned.append(job_id),
    )

    await finish_job(running.id, status=JOB_STATUS_SUCCEEDED, result={})

    assert waiting.id in spawned

    claimed = await _try_claim_with_admission(
        db_session, waiting.id, JOB_KIND_NER_VERIFY, status=JOB_STATUS_QUEUED,
    )
    assert claimed is True
    await db_session.refresh(waiting)
    assert waiting.status == JOB_STATUS_RUNNING


@pytest.mark.asyncio
async def test_maintenance_tick_calls_admit_waiting_jobs(
    db_session, sample_run, monkeypatch,
) -> None:
    await _add_job(db_session, sample_run, status=JOB_STATUS_QUEUED)
    admit_calls: list[int] = []
    real_admit = run_job_service.admit_waiting_jobs

    async def _track_admit() -> int:
        admit_calls.append(1)
        return await real_admit()

    async def _noop_int() -> int:
        return 0

    monkeypatch.setattr(run_job_service, "admit_waiting_jobs", _track_admit)
    monkeypatch.setattr(run_job_service, "_heartbeat_owned_jobs", _noop_int)
    monkeypatch.setattr(run_job_service, "fail_stale_jobs", _noop_int)
    monkeypatch.setattr(run_job_service, "_respawn_orphaned_jobs", _noop_int)

    await run_job_maintenance_tick()
    assert len(admit_calls) == 1


@pytest.mark.asyncio
async def test_fail_stale_verify_job_auto_requeues(db_session, sample_run) -> None:
    from app.pipeline.run_job_service import fail_stale_jobs  # noqa: PLC0415

    job = await _add_job(
        db_session,
        sample_run,
        kind=JOB_KIND_WIKIDATA_VERIFY,
        status=JOB_STATUS_RUNNING,
        claimed_by=WORKER_ID,
    )
    job.params = {"session_id": "sess-wd", "action_id": "audit_wikidata_item"}
    job.progress = {
        "phase": "running",
        "processed": 61,
        "total": 313,
        "session_id": "sess-wd",
        "message": "judging",
    }
    await db_session.commit()
    await _backdate(db_session, job.id, by=STALE_JOB_AFTER + timedelta(seconds=30))

    spawned: list[uuid.UUID] = []

    def _track_spawn(job_id: uuid.UUID) -> None:
        spawned.append(job_id)

    with (
        patch("app.pipeline.run_job_service.spawn_job", side_effect=_track_spawn),
        patch("app.pipeline.run_job_service.admit_waiting_jobs", new=AsyncMock(return_value=0)),
    ):
        count = await fail_stale_jobs()
    assert count == 1
    await db_session.refresh(job)
    assert job.status == JOB_STATUS_QUEUED
    assert job.error is None
    assert job.params["override_cache"] is False
    assert job.params["session_id"] != "sess-wd"
    assert job.progress["processed"] == 61
    assert job.progress["total"] == 313
    assert "Auto-resuming" in (job.progress.get("message") or "")
    assert len(spawned) == 1
    assert spawned[0] == job.id


@pytest.mark.asyncio
async def test_fail_stale_non_verify_keeps_generic_message(db_session, sample_run) -> None:
    from app.pipeline.run_job_service import fail_stale_jobs  # noqa: PLC0415

    job = await _add_job(
        db_session,
        sample_run,
        kind=JOB_KIND_RDF_BUILD,
        status=JOB_STATUS_RUNNING,
        claimed_by=WORKER_ID,
    )
    await _backdate(db_session, job.id, by=STALE_JOB_AFTER + timedelta(seconds=30))

    count = await fail_stale_jobs()
    assert count == 1
    await db_session.refresh(job)
    assert job.status == JOB_STATUS_FAILED
    assert job.result is None
    assert "Cancel and start again" in (job.error or "")
