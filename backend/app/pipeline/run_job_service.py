"""Start, poll, and cancel background run jobs."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session_scope
from app.models.run_job import (
    ACTIVE_JOB_STATUSES,
    JOB_KIND_AUTHORITY_RE_ENRICH,
    JOB_KIND_AUTHORITY_VERIFY,
    JOB_KIND_EXTRACTION,
    JOB_KIND_HMO_SCHEMA_BOOTSTRAP,
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
    await db.commit()
    await db.refresh(job)
    spawn_job(job.id)
    return job


class ActiveJobError(Exception):
    def __init__(self, job_id: uuid.UUID) -> None:
        self.job_id = job_id
        super().__init__(f"active job already exists: {job_id}")


STALE_JOB_AFTER = timedelta(minutes=20)


async def recover_interrupted_jobs() -> int:
    """Re-spawn queued/running jobs after a dyno restart.

    In-memory ``asyncio`` tasks are lost on process recycle but the Postgres
    rows stay ``running`` — without this hook the UI polls a zombie forever.
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


async def fail_stale_jobs() -> int:
    """Mark long-running jobs with no recent heartbeat as failed."""
    cutoff = _now() - STALE_JOB_AFTER
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
        for job in rows:
            job.status = JOB_STATUS_FAILED
            job.error = (
                "Job interrupted — the server restarted or the worker stopped "
                "responding. Cancel and start again."
            )
            job.finished_at = _now()
        await db.commit()
        count = len(rows)
    logger.warning("marked %d stale run job(s) as failed", count)
    return count


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
            if job.status not in (JOB_STATUS_QUEUED, JOB_STATUS_RUNNING):
                return
            job.status = JOB_STATUS_RUNNING
            job.started_at = _now()
            await db.commit()

        if kind == JOB_KIND_AUTHORITY_RE_ENRICH:
            from app.pipeline.authority_re_enrich_job import (  # noqa: PLC0415
                run_authority_re_enrich_job,
            )
            await run_authority_re_enrich_job(job_id)
        elif kind == JOB_KIND_EXTRACTION:
            from app.pipeline.extraction_job import run_extraction_job  # noqa: PLC0415
            await run_extraction_job(job_id)
        elif kind in (JOB_KIND_NER_VERIFY, JOB_KIND_AUTHORITY_VERIFY, JOB_KIND_WIKIDATA_VERIFY):
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


def serialise_job(job: RunJob) -> dict[str, Any]:
    return {
        "id":              str(job.id),
        "project_id":      str(job.project_id),
        "run_id":          str(job.run_id),
        "kind":            job.kind,
        "status":          job.status,
        "progress":        job.progress or {},
        "params":          _public_params(job.params),
        "result":          job.result,
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
