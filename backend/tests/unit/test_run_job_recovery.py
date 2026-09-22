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
    JOB_STATUS_CANCELLED,
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
    recover_resumable_verify_jobs,
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
async def test_recover_resumable_verify_skips_when_active_job_exists(
    db_session, sample_run,
) -> None:
    active = await _add_job(
        db_session,
        sample_run,
        kind=JOB_KIND_WIKIDATA_VERIFY,
        status=JOB_STATUS_QUEUED,
    )
    failed = RunJob(
        project_id=sample_run["project_id"],
        run_id=sample_run["run_id"],
        kind=JOB_KIND_WIKIDATA_VERIFY,
        status=JOB_STATUS_FAILED,
        params={"session_id": "old-sess", "action_id": "audit_wikidata_item"},
        progress={"processed": 40, "total": 313},
        result={"resumable": True, "judged": 40, "total": 313},
        created_by=sample_run["user_id"],
        finished_at=_now(),
    )
    db_session.add(failed)
    await db_session.commit()

    spawned: list[uuid.UUID] = []
    with patch(
        "app.pipeline.run_job_service.spawn_job",
        side_effect=lambda job_id: spawned.append(job_id),
    ):
        count = await recover_resumable_verify_jobs()

    assert count == 0
    assert spawned == []
    await db_session.refresh(failed)
    assert failed.status == JOB_STATUS_FAILED
    await db_session.refresh(active)
    assert active.status == JOB_STATUS_QUEUED


@pytest.mark.asyncio
async def test_recover_resumable_verify_dedupes_same_run_kind(
    db_session, sample_run,
) -> None:
    older = RunJob(
        project_id=sample_run["project_id"],
        run_id=sample_run["run_id"],
        kind=JOB_KIND_WIKIDATA_VERIFY,
        status=JOB_STATUS_FAILED,
        params={"session_id": "old-a", "action_id": "audit_wikidata_item"},
        progress={"processed": 40, "total": 313},
        result={"resumable": True, "judged": 40, "total": 313},
        created_by=sample_run["user_id"],
        finished_at=_now() - timedelta(hours=1),
    )
    newer = RunJob(
        project_id=sample_run["project_id"],
        run_id=sample_run["run_id"],
        kind=JOB_KIND_WIKIDATA_VERIFY,
        status=JOB_STATUS_FAILED,
        params={"session_id": "old-b", "action_id": "audit_wikidata_item"},
        progress={"processed": 61, "total": 313},
        result={"resumable": True, "judged": 61, "total": 313},
        created_by=sample_run["user_id"],
        finished_at=_now(),
    )
    db_session.add_all([older, newer])
    await db_session.commit()

    spawned: list[uuid.UUID] = []
    with patch(
        "app.pipeline.run_job_service.spawn_job",
        side_effect=lambda job_id: spawned.append(job_id),
    ):
        count = await recover_resumable_verify_jobs()

    assert count == 1
    assert spawned == [newer.id]
    await db_session.refresh(newer)
    await db_session.refresh(older)
    assert newer.status == JOB_STATUS_QUEUED
    assert older.status == JOB_STATUS_FAILED


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


@pytest.mark.asyncio
async def test_heavy_cap_blocks_build_while_verify_running(
    db_session, sample_run, monkeypatch,
) -> None:
    """R26: a build and a bulk verify may never run concurrently (R14/R15)."""
    monkeypatch.setenv("RUN_JOB_MAX_HEAVY", "1")
    monkeypatch.setenv("RUN_JOB_MAX_RUNNING", "10")
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
async def test_web_role_defers_heavy_job_until_grace(
    db_session, sample_run, monkeypatch,
) -> None:
    """R27: a web process leaves heavy kinds to the worker until the grace
    window lapses, then claims the job itself (self-healing)."""
    monkeypatch.setenv("RUN_JOB_ROLE", "web")
    # Isolation: close out heavy rows left by earlier tests.
    from app.models.run_job import RunJob as _RJ2
    await db_session.execute(
        update(_RJ2)
        .where(_RJ2.status == JOB_STATUS_RUNNING)
        .values(status=JOB_STATUS_FAILED, error="test isolation")
    )
    await db_session.execute(
        update(_RJ2)
        .where(_RJ2.status == JOB_STATUS_QUEUED, _RJ2.kind == JOB_KIND_RDF_BUILD)
        .values(status=JOB_STATUS_CANCELLED)
    )
    await db_session.commit()


    monkeypatch.setenv("RUN_JOB_WORKER_GRACE", "0")

    queued = await _add_job(
        db_session, sample_run,
        kind=JOB_KIND_RDF_BUILD,
        status=JOB_STATUS_QUEUED,
    )

    claimed = await _try_claim_with_admission(
        db_session, queued.id, JOB_KIND_RDF_BUILD, status=JOB_STATUS_QUEUED,
    )
    assert claimed is True
    await db_session.refresh(queued)
    assert queued.status == JOB_STATUS_RUNNING


@pytest.mark.asyncio
async def test_web_role_leaves_fresh_heavy_job_for_worker(
    db_session, sample_run, monkeypatch,
) -> None:
    """R27: a freshly queued heavy job waits for the worker's tick."""
    monkeypatch.setenv("RUN_JOB_ROLE", "web")
    # Isolation: close out heavy rows left by earlier tests.
    from app.models.run_job import RunJob as _RJ2
    await db_session.execute(
        update(_RJ2)
        .where(_RJ2.status == JOB_STATUS_RUNNING)
        .values(status=JOB_STATUS_FAILED, error="test isolation")
    )
    await db_session.execute(
        update(_RJ2)
        .where(_RJ2.status == JOB_STATUS_QUEUED, _RJ2.kind == JOB_KIND_RDF_BUILD)
        .values(status=JOB_STATUS_CANCELLED)
    )
    await db_session.commit()


    monkeypatch.setenv("RUN_JOB_WORKER_GRACE", "9999")

    queued = await _add_job(
        db_session, sample_run,
        kind=JOB_KIND_RDF_BUILD,
        status=JOB_STATUS_QUEUED,
    )

    claimed = await _try_claim_with_admission(
        db_session, queued.id, JOB_KIND_RDF_BUILD, status=JOB_STATUS_QUEUED,
    )
    assert claimed is False
    await db_session.refresh(queued)
    assert queued.status == JOB_STATUS_QUEUED
    assert queued.progress.get("message") == CAPACITY_WAIT_MESSAGE


@pytest.mark.asyncio
async def test_web_role_executes_light_kinds(
    db_session, sample_run, monkeypatch,
) -> None:
    """R27: light kinds (bulk approve) still run on the web dyno."""
    monkeypatch.setenv("RUN_JOB_ROLE", "web")
    # Isolation: close out heavy rows left by earlier tests.
    from app.models.run_job import RunJob as _RJ2
    await db_session.execute(
        update(_RJ2)
        .where(_RJ2.status == JOB_STATUS_RUNNING)
        .values(status=JOB_STATUS_FAILED, error="test isolation")
    )
    await db_session.execute(
        update(_RJ2)
        .where(_RJ2.status == JOB_STATUS_QUEUED, _RJ2.kind == JOB_KIND_RDF_BUILD)
        .values(status=JOB_STATUS_CANCELLED)
    )
    await db_session.commit()


    monkeypatch.setenv("RUN_JOB_WORKER_GRACE", "9999")

    queued = await _add_job(
        db_session, sample_run,
        kind=JOB_KIND_EXTRACTION,
        status=JOB_STATUS_QUEUED,
    )

    claimed = await _try_claim_with_admission(
        db_session, queued.id, JOB_KIND_EXTRACTION, status=JOB_STATUS_QUEUED,
    )
    assert claimed is True


@pytest.mark.asyncio
async def test_web_role_self_heals_starved_heavy_job(
    db_session, sample_run, monkeypatch,
) -> None:
    """R27 regression (2026-09-15): a heavy job queued for hours with no
    worker must be claimed by web — the capacity-wait stamp refreshes
    updated_at, so the grace must measure from created_at, and a stale
    worker claim must not count as an alive worker."""
    monkeypatch.setenv("RUN_JOB_ROLE", "web")
    # Isolation: close out heavy rows left by earlier tests.
    from app.models.run_job import RunJob as _RJ2
    await db_session.execute(
        update(_RJ2)
        .where(_RJ2.status == JOB_STATUS_RUNNING)
        .values(status=JOB_STATUS_FAILED, error="test isolation")
    )
    await db_session.execute(
        update(_RJ2)
        .where(_RJ2.status == JOB_STATUS_QUEUED, _RJ2.kind == JOB_KIND_RDF_BUILD)
        .values(status=JOB_STATUS_CANCELLED)
    )
    await db_session.commit()


    monkeypatch.setenv("RUN_JOB_WORKER_GRACE", "120")

    # A worker claimed a verify job once but its heartbeat is long stale
    # (worker dyno gone) — it must not count as an alive worker.
    stale_worker_job = await _add_job(
        db_session, sample_run,
        kind=JOB_KIND_WIKIDATA_VERIFY,
        status=JOB_STATUS_RUNNING,
        claimed_by="worker.1:deadbeef",
    )
    await _backdate(db_session, stale_worker_job.id, by=timedelta(minutes=10))

    queued = await _add_job(
        db_session, sample_run,
        kind=JOB_KIND_RDF_BUILD,
        status=JOB_STATUS_QUEUED,
    )
    # Simulate hours of "Waiting for capacity…" churn: updated_at is
    # fresh (the wait stamp refreshes it every tick), created_at is old.
    from app.models.run_job import RunJob as _RJ
    await db_session.execute(
        update(_RJ)
        .where(_RJ.id == queued.id)
        .values(created_at=_now() - timedelta(hours=3), updated_at=_now())
    )
    await db_session.commit()

    # The maintenance tick reaps the stale row (fail_stale_jobs) before
    # admit_waiting_jobs — mirror that order here, with the spawn loop
    # captured so the background runner does not race this test.
    from app.pipeline.run_job_service import fail_stale_jobs  # noqa: PLC0415
    monkeypatch.setattr(
        run_job_service, "spawn_job", lambda job_id: None,
    )
    assert await fail_stale_jobs() >= 1

    claimed = await _try_claim_with_admission(
        db_session, queued.id, JOB_KIND_RDF_BUILD, status=JOB_STATUS_QUEUED,
    )
    assert claimed is True
    await db_session.refresh(queued)
    assert queued.status == JOB_STATUS_RUNNING


@pytest.mark.asyncio
async def test_cancel_finalizes_queued_job_without_owner(
    db_session, sample_run, monkeypatch,
) -> None:
    """R28 regression (2026-09-16): a queued job waiting for capacity has no
    owner to poll the cancel flag — the maintenance pass must finalize it."""
    from app.pipeline.run_job_service import cancel_requested_queued_jobs

    monkeypatch.setenv("RUN_JOB_ROLE", "web")
    monkeypatch.setenv("RUN_JOB_WORKER_GRACE", "9999")

    queued = await _add_job(
        db_session, sample_run,
        kind=JOB_KIND_RDF_BUILD,
        status=JOB_STATUS_QUEUED,
    )
    from app.pipeline.run_job_service import request_cancel
    await request_cancel(db_session, queued.id)
    await db_session.refresh(queued)
    assert queued.status == JOB_STATUS_QUEUED  # flag only, until the tick

    finalized = await cancel_requested_queued_jobs()
    assert finalized == 1
    await db_session.refresh(queued)
    assert queued.status == "cancelled"
    assert queued.error == "Cancelled by user"


@pytest.mark.asyncio
async def test_cancel_requested_queued_job_is_never_claimed(
    db_session, sample_run, monkeypatch,
    monkeypatch2=None,
) -> None:
    """Even with all slots free, a queued job with a cancel request must
    not be claimed (web role after grace, or worker role)."""
    monkeypatch.setenv("RUN_JOB_WORKER_GRACE", "0")

    queued = await _add_job(
        db_session, sample_run,
        kind=JOB_KIND_RDF_BUILD,
        status=JOB_STATUS_QUEUED,
    )
    from app.pipeline.run_job_service import request_cancel
    await request_cancel(db_session, queued.id)

    claimed = await _try_claim_with_admission(
        db_session, queued.id, JOB_KIND_RDF_BUILD, status=JOB_STATUS_QUEUED,
    )
    assert claimed is False
    await db_session.refresh(queued)
    assert queued.status == JOB_STATUS_QUEUED


@pytest.mark.asyncio
async def test_force_cancel_finalizes_running_job_with_old_flag(
    db_session, sample_run, monkeypatch,
) -> None:
    """R28 safety net: a running row whose cancel flag outlived the grace is
    finalized by the maintenance pass even when its executor wedged in a
    non-polling stretch (2026-09-17: a build crawled 20+ minutes past Cancel)."""
    monkeypatch.setattr(run_job_service, "RUN_JOB_CANCEL_FORCE_AFTER_S", 300)

    running = await _add_job(
        db_session, sample_run, kind=JOB_KIND_RDF_BUILD,
        status=JOB_STATUS_RUNNING,
    )
    from app.pipeline.run_job_service import request_cancel
    await request_cancel(db_session, running.id)
    await db_session.execute(
        update(RunJob)
        .where(RunJob.id == running.id)
        .values(cancel_requested_at=run_job_service._now() - timedelta(seconds=400))
        .execution_options(synchronize_session=False)
    )
    await db_session.commit()

    finalized = await run_job_service.cancel_requested_running_jobs()
    assert finalized == 1
    await db_session.refresh(running)
    assert running.status == "cancelled"
    assert running.error == "Cancelled by user"


@pytest.mark.asyncio
async def test_force_cancel_leaves_fresh_flag_and_flagless_rows_alone(
    db_session, sample_run, monkeypatch,
) -> None:
    """A healthy runner finalizes within seconds — the net must not race it."""
    monkeypatch.setattr(run_job_service, "RUN_JOB_CANCEL_FORCE_AFTER_S", 300)

    fresh = await _add_job(
        db_session, sample_run, kind=JOB_KIND_RDF_BUILD,
        status=JOB_STATUS_RUNNING,
    )
    from app.pipeline.run_job_service import request_cancel
    await request_cancel(db_session, fresh.id)

    flagless = await _add_job(
        db_session, sample_run, kind=JOB_KIND_WIKIDATA_UPLOAD,
        status=JOB_STATUS_RUNNING,
    )

    finalized = await run_job_service.cancel_requested_running_jobs()
    assert finalized == 0
    await db_session.refresh(fresh)
    await db_session.refresh(flagless)
    assert fresh.status == JOB_STATUS_RUNNING
    assert flagless.status == JOB_STATUS_RUNNING


@pytest.mark.asyncio
async def test_cancel_watcher_throttles_and_raises(monkeypatch) -> None:
    """The watcher polls the flag at most once per window and raises
    JobCancelledError for the runner loop to finalize (Rule R28)."""
    polls = {"n": 0}

    async def fake_is_cancel(job_id):
        polls["n"] += 1
        return True

    monkeypatch.setattr(run_job_service, "is_cancel_requested", fake_is_cancel)
    check = run_job_service.cancel_watcher(uuid.uuid4())

    with pytest.raises(run_job_service.JobCancelledError):
        await check()
    assert polls["n"] == 1

    # Inside the 1 s window the flag is not polled again (R34: the throttle
    # window resets after the query completes).
    await check()
    assert polls["n"] == 1
