"""Start, poll, and cancel background run jobs."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, func, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session_scope
from app.models.run_job import (
    ACTIVE_JOB_STATUSES,
    JOB_KIND_AUTHORITY_RE_ENRICH,
    JOB_KIND_AUTHORITY_VERIFY,
    JOB_KIND_EXTRACTION,
    JOB_KIND_HMO_COVERAGE,
    JOB_KIND_HMO_ITEM_BULK_APPROVE,
    JOB_KIND_HMO_ITEM_BUILD,
    JOB_KIND_HMO_ITEM_UPLOAD,
    JOB_KIND_HMO_ITEM_VERIFY,
    JOB_KIND_HMO_MANIFEST_BUILD,
    JOB_KIND_HMO_MANIFEST_UPLOAD,
    JOB_KIND_HMO_SCHEMA_BOOTSTRAP,
    JOB_KIND_WIKIDATA_ITEM_BULK_APPROVE,
    JOB_KIND_NER_VERIFY,
    JOB_KIND_RDF_BUILD,
    JOB_KIND_WIKIDATA_STUDIO_BUILD,
    JOB_KIND_WIKIDATA_UPLOAD,
    JOB_KIND_WIKIDATA_VERIFY,
    JOB_STATUS_CANCELLED,
    JOB_STATUS_FAILED,
    JOB_STATUS_QUEUED,
    JOB_STATUS_RUNNING,
    JOB_STATUS_SUCCEEDED,
    RunJob,
)

logger = logging.getLogger(__name__)

_background_tasks: dict[str, asyncio.Task[None]] = {}

# Worker identity. The dyno/host prefix lets a restarted process instantly
# reclaim rows left by its own dead predecessor (Heroku replaces web.1 with
# a new web.1); the uuid suffix distinguishes live processes. Caveat: with
# WEB_CONCURRENCY>1 sibling uvicorn workers share the prefix, so a restarted
# sibling could steal a live sibling's job at startup — acceptable while
# WEB_CONCURRENCY stays 1 (the current production setup).
WORKER_DYNO = os.environ.get("DYNO") or socket.gethostname()
WORKER_ID = f"{WORKER_DYNO}:{uuid.uuid4().hex[:8]}"

# Serialises admission checks + queued claims across asyncio tasks (SQLite dev/CI)
# and Postgres dynos (``pg_advisory_xact_lock`` inside the same transaction).
_admission_async_lock = asyncio.Lock()
_ADMISSION_PG_LOCK_KEY = 0x4D484D4A  # "MHMJ" — job admission gate

CAPACITY_WAIT_PHASE = "queued"
CAPACITY_WAIT_MESSAGE = "Waiting for capacity…"

SLOT_VERIFY = "verify"
SLOT_BUILD = "build"
SLOT_UPLOAD = "upload"
SLOT_LIGHT = "light"
SLOT_OTHER = "other"

_VERIFY_KINDS = frozenset({
    JOB_KIND_NER_VERIFY,
    JOB_KIND_AUTHORITY_VERIFY,
    JOB_KIND_WIKIDATA_VERIFY,
    JOB_KIND_HMO_ITEM_VERIFY,
})
_BUILD_KINDS = frozenset({
    JOB_KIND_RDF_BUILD,
    JOB_KIND_WIKIDATA_STUDIO_BUILD,
    JOB_KIND_HMO_COVERAGE,
    JOB_KIND_HMO_ITEM_BUILD,
    JOB_KIND_HMO_MANIFEST_BUILD,
    JOB_KIND_HMO_SCHEMA_BOOTSTRAP,
    JOB_KIND_AUTHORITY_RE_ENRICH,
})
_UPLOAD_KINDS = frozenset({
    JOB_KIND_HMO_ITEM_UPLOAD,
    JOB_KIND_WIKIDATA_UPLOAD,
    JOB_KIND_HMO_MANIFEST_UPLOAD,
})
_LIGHT_KINDS = frozenset({
    JOB_KIND_EXTRACTION,
    JOB_KIND_HMO_ITEM_BULK_APPROVE,
    JOB_KIND_WIKIDATA_ITEM_BULK_APPROVE,
})


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


def _max_running_global() -> int:
    return _int_env("RUN_JOB_MAX_RUNNING", 2)


def _max_running_for_slot(slot: str) -> int:
    defaults = {
        SLOT_VERIFY: 1,
        SLOT_BUILD: 1,
        SLOT_UPLOAD: 1,
        SLOT_LIGHT: 2,
    }
    env_names = {
        SLOT_VERIFY: "RUN_JOB_MAX_VERIFY",
        SLOT_BUILD: "RUN_JOB_MAX_BUILD",
        SLOT_UPLOAD: "RUN_JOB_MAX_UPLOAD",
        SLOT_LIGHT: "RUN_JOB_MAX_LIGHT",
    }
    if slot not in defaults:
        return _max_running_global()
    return _int_env(env_names[slot], defaults[slot])


def _admission_slot_class(kind: str) -> str:
    if kind in _VERIFY_KINDS or kind.endswith("_verify"):
        return SLOT_VERIFY
    if kind in _BUILD_KINDS:
        return SLOT_BUILD
    if kind in _UPLOAD_KINDS:
        return SLOT_UPLOAD
    if kind in _LIGHT_KINDS:
        return SLOT_LIGHT
    return SLOT_OTHER


def _kinds_for_slot(slot: str) -> frozenset[str]:
    if slot == SLOT_VERIFY:
        return _VERIFY_KINDS
    if slot == SLOT_BUILD:
        return _BUILD_KINDS
    if slot == SLOT_UPLOAD:
        return _UPLOAD_KINDS
    if slot == SLOT_LIGHT:
        return _LIGHT_KINDS
    return frozenset()


def _admission_allows(global_running: int, class_running: int, kind: str) -> bool:
    if global_running >= _max_running_global():
        return False
    slot = _admission_slot_class(kind)
    if slot == SLOT_OTHER:
        return True
    return class_running < _max_running_for_slot(slot)


async def _acquire_admission_lock(db: AsyncSession) -> None:
    from app.db import engine  # noqa: PLC0415

    if engine.dialect.name == "postgresql":
        await db.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": _ADMISSION_PG_LOCK_KEY},
        )


async def _count_running_jobs(
    db: AsyncSession,
    *,
    slot: str | None = None,
) -> int:
    stmt = (
        select(func.count())
        .select_from(RunJob)
        .where(RunJob.status == JOB_STATUS_RUNNING)
    )
    if slot is not None:
        kinds = _kinds_for_slot(slot)
        if kinds:
            stmt = stmt.where(RunJob.kind.in_(tuple(kinds)))
    return int((await db.execute(stmt)).scalar_one())


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def find_active_job(
    db: AsyncSession,
    *,
    run_id: uuid.UUID,
    kind: str,
) -> RunJob | None:
    row = (
        await db.execute(
            select(RunJob).where(
                RunJob.run_id == run_id,
                RunJob.kind == kind,
                RunJob.status.in_(tuple(ACTIVE_JOB_STATUSES)),
            )
        )
    ).scalar_one_or_none()
    return row


async def create_job(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    kind: str,
    params: dict[str, Any],
    created_by: uuid.UUID,
) -> RunJob:
    existing = await find_active_job(db, run_id=run_id, kind=kind)
    if existing is not None:
        raise ActiveJobError(existing.id)

    job = RunJob(
        project_id=project_id,
        run_id=run_id,
        kind=kind,
        status=JOB_STATUS_QUEUED,
        params=params,
        progress={},
        created_by=created_by,
    )
    db.add(job)
    try:
        await db.commit()
    except IntegrityError as exc:
        # Two concurrent starts raced past find_active_job; the partial
        # unique index on active (run_id, kind) makes the loser land here.
        await db.rollback()
        existing = await find_active_job(db, run_id=run_id, kind=kind)
        if existing is not None:
            raise ActiveJobError(existing.id) from exc
        raise
    await db.refresh(job)
    spawn_job(job.id)
    return job


class ActiveJobError(Exception):
    def __init__(self, job_id: uuid.UUID) -> None:
        self.job_id = job_id
        super().__init__(f"active job already exists: {job_id}")


# The maintenance loop heartbeats owned rows every minute, so staleness no
# longer depends on how often a worker reports progress — 5 minutes of
# silence means the owning process is gone.
STALE_JOB_AFTER = timedelta(minutes=5)
MAINTENANCE_INTERVAL_SECONDS = 60
# A queued row this old whose creator never claimed it is an orphan.
ORPHAN_GRACE = timedelta(seconds=90)


async def recover_interrupted_jobs() -> int:
    """Re-spawn queued/running jobs after a dyno restart.

    In-memory ``asyncio`` tasks are lost on process recycle but the Postgres
    rows stay ``running`` — without this hook the UI polls a zombie forever.
    Safe on multi-dyno setups: ``_try_claim_job`` rejects rows owned by a
    live foreign process, so a spawn here only proceeds for rows this dyno
    may legitimately take over.
    """
    async with session_scope() as db:
        rows = (
            await db.execute(
                select(RunJob).where(
                    RunJob.status.in_(tuple(ACTIVE_JOB_STATUSES)),
                )
            )
        ).scalars().all()
        job_ids = [row.id for row in rows]

    for job_id in job_ids:
        spawn_job(job_id)
    if job_ids:
        logger.info("re-spawned %d interrupted run job(s)", len(job_ids))
    return len(job_ids)


async def recover_resumable_verify_jobs() -> int:
    """Re-queue failed verify jobs that still have cached progress (Rule W-134).

    Covers deploys that landed before auto-requeue-on-stale: rows may still be
    ``failed`` with ``result.resumable`` even though verdicts are in Postgres.
    Skips rows when another active job already exists for the same run+kind
    (partial unique index ``uq_run_jobs_active_kind``).
    """
    from app.pipeline.verify_resume import (  # noqa: PLC0415
        VERIFY_JOB_KINDS,
        apply_verify_job_auto_resume,
    )

    cutoff = _now() - timedelta(hours=24)
    spawn_ids: list[uuid.UUID] = []
    async with session_scope() as db:
        rows = (
            await db.execute(
                select(RunJob).where(
                    RunJob.kind.in_(tuple(VERIFY_JOB_KINDS)),
                    RunJob.status == JOB_STATUS_FAILED,
                    RunJob.finished_at.is_not(None),
                    RunJob.finished_at >= cutoff,
                    RunJob.cancel_requested_at.is_(None),
                )
            )
        ).scalars().all()
        for job in rows:
            result = job.result if isinstance(job.result, dict) else {}
            if not result.get("resumable"):
                continue
            active = await find_active_job(db, run_id=job.run_id, kind=job.kind)
            if active is not None and active.id != job.id:
                logger.info(
                    "skip auto-resume for verify job %s: active job %s already exists",
                    job.id,
                    active.id,
                )
                continue
            if apply_verify_job_auto_resume(job):
                spawn_ids.append(job.id)
        if spawn_ids:
            try:
                await db.commit()
            except IntegrityError:
                await db.rollback()
                logger.warning(
                    "auto-resume commit hit active-job unique index; skipping %d row(s)",
                    len(spawn_ids),
                )
                spawn_ids = []

    for job_id in spawn_ids:
        spawn_job(job_id)
    if spawn_ids:
        logger.info("auto-resumed %d verify job(s) after interrupt", len(spawn_ids))
    return len(spawn_ids)


async def fail_stale_jobs() -> int:
    """Mark long-running jobs with no recent heartbeat as failed.

    Verify kinds with cached progress (Rule W-134) are re-queued for automatic
    resume instead of left in ``failed`` for manual Continue.
    """
    from app.pipeline.verify_resume import (  # noqa: PLC0415
        STALE_GENERIC_ERROR,
        STALE_VERIFY_RETRY_ERROR,
        apply_verify_job_auto_resume,
        is_verify_job_kind,
        progress_counts,
        resumable_verify_result,
    )

    cutoff = _now() - STALE_JOB_AFTER
    requeue_spawn: list[uuid.UUID] = []
    async with session_scope() as db:
        rows = (
            await db.execute(
                select(RunJob).where(
                    RunJob.status == JOB_STATUS_RUNNING,
                    RunJob.updated_at < cutoff,
                )
            )
        ).scalars().all()
        if not rows:
            return 0

        finished = _now()
        for job in rows:
            if is_verify_job_kind(job.kind):
                if job.cancel_requested_at is not None:
                    job.status = JOB_STATUS_CANCELLED
                    job.error = "Verification cancelled"
                    job.finished_at = finished
                    continue
                if apply_verify_job_auto_resume(job):
                    requeue_spawn.append(job.id)
                    continue
                progress = job.progress if isinstance(job.progress, dict) else {}
                judged, total = progress_counts(progress)
                session_id = str(
                    progress.get("session_id")
                    or (job.params or {}).get("session_id")
                    or "",
                )
                job.result = resumable_verify_result(
                    session_id=session_id or None,
                    judged=judged,
                    total=total,
                    session_snapshot=None,
                    interrupted=True,
                )
                job.error = STALE_VERIFY_RETRY_ERROR
                job.progress = {
                    **progress,
                    "phase": "interrupted",
                    "processed": judged,
                    "total": total or judged,
                    "message": job.error,
                    "session_id": session_id or progress.get("session_id"),
                }
                job.status = JOB_STATUS_FAILED
                job.finished_at = finished
            else:
                job.error = STALE_GENERIC_ERROR
                job.status = JOB_STATUS_FAILED
                job.finished_at = finished
        await db.commit()
        count = len(rows)

    for job_id in requeue_spawn:
        spawn_job(job_id)
    if requeue_spawn:
        logger.info("auto-requeued %d stale verify job(s)", len(requeue_spawn))

    if count:
        logger.warning("marked %d stale run job(s) as failed", count - len(requeue_spawn))
        await admit_waiting_jobs()
    return count


async def _try_claim_job(db: AsyncSession, job_id: uuid.UUID) -> bool:
    """Atomically take ownership of a job row.

    A queued row is always claimable. A running row is claimable only when
    it is unowned (pre-claim-column rows), already ours, left behind by a
    previous incarnation of this dyno, or stale. The single conditional
    UPDATE makes the cross-process race deterministic: exactly one claimant
    sees ``rowcount == 1``.
    """
    res = await db.execute(
        update(RunJob)
        .where(
            RunJob.id == job_id,
            or_(
                RunJob.status == JOB_STATUS_QUEUED,
                and_(
                    RunJob.status == JOB_STATUS_RUNNING,
                    or_(
                        RunJob.claimed_by.is_(None),
                        RunJob.claimed_by == WORKER_ID,
                        RunJob.claimed_by.like(f"{WORKER_DYNO}:%"),
                        RunJob.updated_at < _now() - STALE_JOB_AFTER,
                    ),
                ),
            ),
        )
        .values(
            status=JOB_STATUS_RUNNING,
            claimed_by=WORKER_ID,
            started_at=func.coalesce(RunJob.started_at, func.now()),
            updated_at=func.now(),
        )
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    return (res.rowcount or 0) == 1


async def _mark_waiting_for_capacity(db: AsyncSession, job_id: uuid.UUID) -> None:
    progress = {
        "phase": CAPACITY_WAIT_PHASE,
        "message": CAPACITY_WAIT_MESSAGE,
    }
    await db.execute(
        update(RunJob)
        .where(
            RunJob.id == job_id,
            RunJob.status == JOB_STATUS_QUEUED,
        )
        .values(progress=progress, updated_at=func.now())
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    job = await db.get(RunJob, job_id)
    if job is not None:
        await _notify_job_update(db, job)


async def _try_claim_queued_job(
    db: AsyncSession,
    job_id: uuid.UUID,
    kind: str,
) -> bool:
    """Claim a queued row only when global + class admission slots are free."""
    global_running = await _count_running_jobs(db)
    slot = _admission_slot_class(kind)
    class_running = (
        await _count_running_jobs(db, slot=slot)
        if slot != SLOT_OTHER
        else 0
    )
    if not _admission_allows(global_running, class_running, kind):
        await _mark_waiting_for_capacity(db, job_id)
        return False

    res = await db.execute(
        update(RunJob)
        .where(
            RunJob.id == job_id,
            RunJob.status == JOB_STATUS_QUEUED,
        )
        .values(
            status=JOB_STATUS_RUNNING,
            claimed_by=WORKER_ID,
            started_at=func.coalesce(RunJob.started_at, func.now()),
            updated_at=func.now(),
        )
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    return (res.rowcount or 0) == 1


async def _try_claim_with_admission(
    db: AsyncSession,
    job_id: uuid.UUID,
    kind: str,
    *,
    status: str,
) -> bool:
    """Admission-gated claim for new work; running-row reclaim skips the gate."""
    async with _admission_async_lock:
        await _acquire_admission_lock(db)
        if status == JOB_STATUS_QUEUED:
            return await _try_claim_queued_job(db, job_id, kind)
        return await _try_claim_job(db, job_id)


async def admit_waiting_jobs() -> int:
    """Re-spawn queued rows that are waiting for a concurrency slot.

    Each spawn attempts admission again; excess spawns exit immediately after
    stamping the capacity-wait progress message.
    """
    async with session_scope() as db:
        rows = (
            await db.execute(
                select(RunJob.id)
                .where(RunJob.status == JOB_STATUS_QUEUED)
                .order_by(RunJob.created_at.asc())
            )
        ).scalars().all()

    spawned = 0
    for job_id in rows:
        key = str(job_id)
        task = _background_tasks.get(key)
        if task is not None and not task.done():
            continue
        spawn_job(job_id)
        spawned += 1
    if spawned:
        logger.info("admitted %d queued run job spawn(s)", spawned)
    return spawned


async def _heartbeat_owned_jobs() -> int:
    """Bump ``updated_at`` on rows whose asyncio task is alive in this process.

    Decouples staleness from worker progress cadence: a job that is busy but
    quiet (e.g. the RDF build's TTL-write tail) still heartbeats. The
    status/claimed_by guards make the races no-ops — a job that finished
    between snapshot and UPDATE is skipped, and a row stolen after a
    false-stale verdict is not zombie-heartbeated.
    """
    live_ids = [
        uuid.UUID(key)
        for key, task in _background_tasks.items()
        if not task.done()
    ]
    if not live_ids:
        return 0
    async with session_scope() as db:
        res = await db.execute(
            update(RunJob)
            .where(
                RunJob.id.in_(live_ids),
                RunJob.status == JOB_STATUS_RUNNING,
                RunJob.claimed_by == WORKER_ID,
            )
            .values(updated_at=func.now())
            .execution_options(synchronize_session=False)
        )
        await db.commit()
        return res.rowcount or 0


async def _respawn_orphaned_jobs() -> int:
    """Spawn queued rows whose creator died before claiming them.

    Cross-dyno double-spawn is harmless: the losing spawn's
    ``_try_claim_job`` returns False and its task exits immediately.
    """
    cutoff = _now() - ORPHAN_GRACE
    async with session_scope() as db:
        rows = (
            await db.execute(
                select(RunJob.id).where(
                    RunJob.status == JOB_STATUS_QUEUED,
                    RunJob.updated_at < cutoff,
                )
            )
        ).scalars().all()
    spawned = 0
    for job_id in rows:
        if str(job_id) not in _background_tasks:
            spawn_job(job_id)
            spawned += 1
    if spawned:
        logger.info("re-spawned %d orphaned queued run job(s)", spawned)
    return spawned


async def run_job_maintenance_tick() -> None:
    """One reconcile pass: heartbeat, then reap, then respawn orphans.

    Heartbeat runs first so this process's live-but-quiet jobs can never be
    reaped by its own tick.
    """
    await _heartbeat_owned_jobs()
    await fail_stale_jobs()
    await _respawn_orphaned_jobs()
    await admit_waiting_jobs()


async def run_job_maintenance_loop() -> None:
    """Periodic job-table reconcile, started from the app lifespan.

    Sleeps first — the lifespan already runs a startup pass, so an
    immediate tick would be redundant.
    """
    while True:
        await asyncio.sleep(MAINTENANCE_INTERVAL_SECONDS)
        try:
            await run_job_maintenance_tick()
        except Exception:  # noqa: BLE001 — the loop must survive any tick failure
            logger.exception("run job maintenance tick failed")


def spawn_job(job_id: uuid.UUID) -> None:
    key = str(job_id)
    prev = _background_tasks.get(key)
    if prev is not None and not prev.done():
        return
    task = asyncio.create_task(_execute_job(job_id), name=f"run-job-{key}")
    _background_tasks[key] = task

    def _done(t: asyncio.Task[None]) -> None:
        _background_tasks.pop(key, None)
        if not t.cancelled() and t.exception() is not None:
            logger.exception("run job %s crashed", key, exc_info=t.exception())

    task.add_done_callback(_done)


async def _execute_job(job_id: uuid.UUID) -> None:
    kind: str | None = None
    try:
        async with session_scope() as db:
            job = await db.get(RunJob, job_id)
            if job is None:
                return
            kind = job.kind
            if not await _try_claim_with_admission(
                db, job_id, kind, status=job.status,
            ):
                return

        if kind in (JOB_KIND_AUTHORITY_RE_ENRICH, JOB_KIND_AUTHORITY_VERIFY):
            from app.settings import get_settings  # noqa: PLC0415
            if not get_settings().legacy_authority_mutations_enabled:
                logger.warning("legacy_authority_job_retired", extra={"job_id": str(job_id), "kind": kind})
                await _fail_job(
                    job_id,
                    "standalone Authority jobs are retired; rebuild or verify canonical HMO entities",
                )
                return

        if kind == JOB_KIND_AUTHORITY_RE_ENRICH:
            from app.pipeline.authority_re_enrich_job import (  # noqa: PLC0415
                run_authority_re_enrich_job,
            )
            await run_authority_re_enrich_job(job_id)
        elif kind == JOB_KIND_EXTRACTION:
            from app.pipeline.extraction_job import run_extraction_job  # noqa: PLC0415
            await run_extraction_job(job_id)
        elif kind in (
            JOB_KIND_NER_VERIFY, JOB_KIND_AUTHORITY_VERIFY, JOB_KIND_WIKIDATA_VERIFY,
            JOB_KIND_HMO_ITEM_VERIFY,
        ):
            from app.pipeline.verify_job import run_verify_job  # noqa: PLC0415
            await run_verify_job(job_id)
        elif kind == JOB_KIND_RDF_BUILD:
            from app.pipeline.rdf_build_job import run_rdf_build_job  # noqa: PLC0415
            await run_rdf_build_job(job_id)
        elif kind == JOB_KIND_WIKIDATA_STUDIO_BUILD:
            from app.pipeline.wikidata_studio_build_job import (  # noqa: PLC0415
                run_wikidata_studio_build_job,
            )
            await run_wikidata_studio_build_job(job_id)
        elif kind == JOB_KIND_WIKIDATA_UPLOAD:
            from app.pipeline.wikidata_upload_job import run_wikidata_upload_job  # noqa: PLC0415
            await run_wikidata_upload_job(job_id)
        elif kind == JOB_KIND_HMO_SCHEMA_BOOTSTRAP:
            from app.pipeline.hmo_schema_bootstrap_job import (  # noqa: PLC0415
                run_hmo_schema_bootstrap_job,
            )
            await run_hmo_schema_bootstrap_job(job_id)
        elif kind == JOB_KIND_HMO_COVERAGE:
            from app.pipeline.hmo_coverage_job import run_hmo_coverage_job  # noqa: PLC0415
            await run_hmo_coverage_job(job_id)
        elif kind == JOB_KIND_HMO_ITEM_UPLOAD:
            from app.pipeline.hmo_item_upload_job import (  # noqa: PLC0415
                run_hmo_item_upload_job,
            )
            await run_hmo_item_upload_job(job_id)
        elif kind == JOB_KIND_HMO_ITEM_BUILD:
            from app.pipeline.hmo_item_build_job import (  # noqa: PLC0415
                run_hmo_item_build_job,
            )
            await run_hmo_item_build_job(job_id)
        elif kind == JOB_KIND_HMO_MANIFEST_BUILD:
            from app.pipeline.hmo_manifest_build_job import (  # noqa: PLC0415
                run_hmo_manifest_build_job,
            )
            await run_hmo_manifest_build_job(job_id)
        elif kind == JOB_KIND_HMO_MANIFEST_UPLOAD:
            from app.pipeline.hmo_manifest_upload_job import (  # noqa: PLC0415
                run_hmo_manifest_upload_job,
            )
            await run_hmo_manifest_upload_job(job_id)
        elif kind in (JOB_KIND_HMO_ITEM_BULK_APPROVE, JOB_KIND_WIKIDATA_ITEM_BULK_APPROVE):
            from app.pipeline.studio_item_bulk_approve_job import (  # noqa: PLC0415
                run_studio_item_bulk_approve_job,
            )
            await run_studio_item_bulk_approve_job(job_id)
        else:
            await _fail_job(job_id, f"unknown job kind {kind!r}")
    except Exception as exc:  # noqa: BLE001
        logger.exception("run job %s failed", job_id)
        await _fail_job(job_id, str(exc))


async def _fail_job(job_id: uuid.UUID, message: str) -> None:
    async with session_scope() as db:
        job = await db.get(RunJob, job_id)
        if job is None or job.status in (JOB_STATUS_SUCCEEDED, JOB_STATUS_CANCELLED):
            return
        job.status = JOB_STATUS_FAILED
        job.error = message
        job.finished_at = _now()
        await db.commit()
    await admit_waiting_jobs()


async def finish_job(
    job_id: uuid.UUID,
    *,
    status: str,
    result: dict[str, Any] | None = None,
    error: str | None = None,
    progress: dict[str, Any] | None = None,
) -> None:
    async with session_scope() as db:
        job = await db.get(RunJob, job_id)
        if job is None:
            return
        job.status = status
        job.result = result
        job.error = error
        job.finished_at = _now()
        if progress is not None:
            job.progress = progress
        await db.commit()
        await _notify_job_update(db, job)
    await admit_waiting_jobs()


async def update_job_progress(job_id: uuid.UUID, progress: dict[str, Any]) -> None:
    async with session_scope() as db:
        job = await db.get(RunJob, job_id)
        if job is None:
            return
        job.progress = progress
        await db.commit()
        await _notify_job_update(db, job)


async def _notify_job_update(db: AsyncSession, job: RunJob) -> None:
    """Push a live update through the existing project WebSocket broker.

    Reuses ``app.realtime``'s Postgres NOTIFY channel (no new
    infrastructure) so any client with the project's WebSocket room open
    sees job progress immediately instead of waiting for the next poll
    tick. Polling (``GET /runs/{run_id}/jobs/{job_id}``) stays as the
    source of truth and fallback — this is a latency improvement, not a
    replacement.

    ``pg_notify`` is Postgres-only — the test suite (and any future
    non-Postgres dev setup) runs on SQLite, which has no such function.
    Skip cleanly there rather than raising into a session shared by the
    caller (an unhandled DBAPI error here would leave that connection's
    transaction aborted for whatever runs next on it).

    ``updated_at`` uses ``onupdate=func.now()`` (server-generated), so
    the just-committed value is expired on the in-memory object; the
    caller always calls this right after ``db.commit()``, before any
    further attribute access. ``serialise_job`` is a plain sync
    function, so touching an expired column from it would try to
    lazy-load outside the asyncio greenlet bridge and raise
    ``MissingGreenlet``. Refresh (an awaited, greenlet-safe DB call)
    first so every column is already loaded by the time we serialise.
    """
    from app.db import engine  # noqa: PLC0415
    from app.realtime import NOTIFY_CHANNEL  # noqa: PLC0415

    if engine.dialect.name != "postgresql":
        return

    await db.refresh(job)

    payload = {
        "type": "run_job_update",
        "project_id": str(job.project_id),
        "job": serialise_job(job),
    }
    try:
        await db.execute(
            text("SELECT pg_notify(:channel, :payload)"),
            {"channel": NOTIFY_CHANNEL, "payload": json.dumps(payload, default=str)},
        )
        await db.commit()
    except Exception:  # noqa: BLE001 — a missed live-push must never fail the job
        logger.debug("failed to publish run_job_update notify", exc_info=True)
        await db.rollback()


async def is_cancel_requested(job_id: uuid.UUID) -> bool:
    async with session_scope() as db:
        job = await db.get(RunJob, job_id)
        return job is not None and job.cancel_requested_at is not None


async def request_cancel(db: AsyncSession, job_id: uuid.UUID) -> RunJob | None:
    job = await db.get(RunJob, job_id)
    if job is None:
        return None
    if job.status not in ACTIVE_JOB_STATUSES:
        return job
    job.cancel_requested_at = _now()
    await db.commit()
    await db.refresh(job)
    return job


def _public_params(params: dict[str, Any] | None) -> dict[str, Any]:
    raw = params or {}
    return {k: v for k, v in raw.items() if not str(k).startswith("_")}


def serialise_job(
    job: RunJob,
    *,
    include_session_snapshot: bool = False,
) -> dict[str, Any]:
    """Serialize a job for the API.

    List endpoints MUST omit ``session_snapshot`` (Rule W-130 / W-128): a run
    with several verify jobs each embedding hundreds of compact verdicts was
    enough to push the Basic dyno into R14 when the curator opened the
    verify modal (``listForRun(active=false)``). Single-job GET may include
    the slim snapshot for session hydration.
    """
    progress = job.progress or {}
    result = job.result
    # Never ship TRACE events on the wire — job polls H12'd at ~1.8 MB (W-128).
    if isinstance(result, dict) and isinstance(result.get("session_snapshot"), dict):
        if include_session_snapshot:
            from app.pipeline.verify_session_store import (  # noqa: PLC0415
                slim_job_session_snapshot,
            )

            result = {
                **result,
                "session_snapshot": slim_job_session_snapshot(result["session_snapshot"]),
            }
        else:
            result = {k: v for k, v in result.items() if k != "session_snapshot"}
    if isinstance(progress, dict) and isinstance(progress.get("session_snapshot"), dict):
        if include_session_snapshot:
            from app.pipeline.verify_session_store import (  # noqa: PLC0415
                slim_job_session_snapshot,
            )

            progress = {
                **progress,
                "session_snapshot": slim_job_session_snapshot(progress["session_snapshot"]),
            }
        else:
            progress = {k: v for k, v in progress.items() if k != "session_snapshot"}
    return {
        "id":              str(job.id),
        "project_id":      str(job.project_id),
        "run_id":          str(job.run_id),
        "kind":            job.kind,
        "status":          job.status,
        "progress":        progress,
        "params":          _public_params(job.params),
        "result":          result,
        "error":           job.error,
        "created_by":      str(job.created_by) if job.created_by else None,
        "started_at":      job.started_at.isoformat() if job.started_at else None,
        "finished_at":     job.finished_at.isoformat() if job.finished_at else None,
        "cancel_requested_at": (
            job.cancel_requested_at.isoformat() if job.cancel_requested_at else None
        ),
        "created_at":      job.created_at.isoformat() if job.created_at else None,
        "updated_at":      job.updated_at.isoformat() if job.updated_at else None,
    }
