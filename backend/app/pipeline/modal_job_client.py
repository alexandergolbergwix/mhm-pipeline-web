"""Modal dispatch for heavy job kinds (Rule W-237).

When ``MODAL_JOBS_URL`` is configured, ``rdf_build`` and ``hmo_item_build``
execute on the ``mhm-jobs`` Modal app instead of the Heroku dyno: the web
process stays the claimer/arbiter (admission, heartbeat, cancel) and the
Modal function does the compute, writing progress and the terminal state
directly to Postgres. The local runner is always the fallback — a Modal
outage, dispatch failure, or poll timeout degrades to the pre-existing
Heroku path (Rule W-15: Modal is a deploy target, never a hard dependency).

Protocol (see ``modal/modal_jobs.py``):

1. ``POST {MODAL_JOBS_URL}/run`` with ``{job_id, kind}`` and
   ``Authorization: Bearer {MODAL_JOBS_TOKEN}`` → ``202`` means the detached
   Modal function spawned.
2. This process then polls the ``run_jobs`` row until it reaches a terminal
   status, heartbeating so ``fail_stale_jobs`` does not reap the running
   row (the poll runs inside the claiming process's owned task). If the
   poll exceeds the Modal function's timeout budget the local runner takes
   over — the RDF build resumes from its checkpoint.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import timedelta
from typing import Any, Awaitable, Callable

import httpx
from sqlalchemy import func, select, update

from app.settings import get_settings

logger = logging.getLogger(__name__)

# Job kinds with a Modal executor. Everything else always runs locally
# (wikidata builds / publication stay Heroku-side — curator decision).
MODAL_JOB_KINDS = frozenset({"rdf_build", "hmo_item_build"})

# Modal web endpoint: dispatch must be quick (it only spawns).
_DISPATCH_TIMEOUT_S = 20.0
# The detached Modal function runs with timeout=14400; poll past it.
_POLL_TIMEOUT_S = 15000.0
# Seconds between row polls (also the cadence at which progress is relayed).
_POLL_INTERVAL_S = 10.0
_TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled"})
# A modal-executor claim whose heartbeat is older than this is dead and
# may be taken over (by a re-dispatch or the local runner).
_STALE_AFTER_S = 120


def _now_utc() -> Any:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


def _stale_cutoff() -> Any:
    return _now_utc() - timedelta(seconds=_STALE_AFTER_S)


async def _dispatch(job_id: str, kind: str) -> bool:
    """Spawn the detached Modal run. Returns True on 202."""
    settings = get_settings()
    base = settings.modal_jobs_url.rstrip("/")
    async with httpx.AsyncClient(timeout=_DISPATCH_TIMEOUT_S) as client:
        resp = await client.post(
            base,  # the fastapi_endpoint mounts at the subdomain root
            headers={"Content-Type": "application/json"},
            json={
                "job_id": job_id,
                "kind": kind,
                "token": settings.modal_jobs_token,
            },
        )
    if resp.status_code == 202:
        return True
    logger.warning(
        "modal dispatch for %s job %s rejected: %s %s",
        kind, job_id, resp.status_code, resp.text[:200],
    )
    return False


async def run_on_modal(
    job_id: uuid.UUID,
    kind: str,
    *,
    on_progress: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> bool:
    """Execute one job on Modal. Return False to run it locally instead.

    Exclusive execution is enforced with a dispatch lease (Rule W-237):
    the claiming process moves the row's ``claimed_by`` to
    ``modal-dispatch`` (only from its own claim, or from a stale modal
    executor), and the Modal container atomically takes
    ``modal-executor:<uuid>``. A second dispatch finds the lease held and
    polls instead of spawning a rival container.

    True only when the row reached a terminal status. While polling, the
    claiming process stays alive (its heartbeat covers the running row
    for ``fail_stale_jobs``).
    """
    settings = get_settings()
    if not settings.modal_jobs_url or kind not in MODAL_JOB_KINDS:
        return False

    from app.db import session_scope  # noqa: PLC0415
    from app.models.run_job import RunJob  # noqa: PLC0415
    from app.pipeline.run_job_service import WORKER_ID  # noqa: PLC0415

    async def _lease_for_dispatch() -> bool:
        async with session_scope() as db:
            res = await db.execute(
                update(RunJob)
                .where(
                    RunJob.id == job_id,
                    RunJob.status == "running",
                    (RunJob.claimed_by == WORKER_ID)
                    | (
                        RunJob.claimed_by.like("modal-executor:%")
                        & (RunJob.updated_at < _stale_cutoff())
                    ),
                )
                .values(claimed_by="modal-dispatch", updated_at=func.now())
                .execution_options(synchronize_session=False),
            )
            await db.commit()
        return (res.rowcount or 0) == 1

    try:
        holds_lease = await _lease_for_dispatch()
    except Exception as exc:  # noqa: BLE001 — network errors degrade locally
        logger.warning("modal lease failed for %s %s: %s", kind, job_id, exc)
        return False
    if not holds_lease:
        # Either another executor holds a fresh lease or the row changed
        # state — poll until terminal instead of spawning a rival.
        logger.info("modal: lease held elsewhere for %s job %s — polling", kind, job_id)
        return await _poll_until_terminal(job_id, on_progress)
    try:
        accepted = await _dispatch(str(job_id), kind)
    except Exception as exc:  # noqa: BLE001 — network errors degrade locally
        logger.warning("modal dispatch failed for %s %s: %s", kind, job_id, exc)
        return False
    if not accepted:
        return False

    logger.info("modal: dispatched %s job %s", kind, job_id)
    return await _poll_until_terminal(job_id, on_progress)


async def _poll_until_terminal(
    job_id: uuid.UUID,
    on_progress: Callable[[dict[str, Any]], Awaitable[None]] | None,
) -> bool:
    """Poll the row to a terminal status while heartbeating via ownership.

    When the poll budget lapses but a Modal container is still alive
    (fresh ``modal-executor`` heartbeat), keep waiting — running the job
    locally in parallel would reintroduce the double-writer bug. Only a
    stale modal claim falls back to local execution.
    """
    started = time.monotonic()
    from app.db import session_scope  # noqa: PLC0415
    from app.models.run_job import RunJob  # noqa: PLC0415

    last_progress: dict[str, Any] = {}
    while True:
        await asyncio.sleep(_POLL_INTERVAL_S)
        async with session_scope() as db:
            job = (
                await db.execute(select(RunJob).where(RunJob.id == job_id))
            ).scalar_one_or_none()
            if job is None:
                logger.warning("modal poll: job %s vanished", job_id)
                return False
            progress = dict(job.progress or {})
            status = str(job.status)
            claimed_by = str(job.claimed_by or "")
            updated_at = job.updated_at
        if progress != last_progress and on_progress is not None:
            try:
                await on_progress(progress)
            except Exception:  # noqa: BLE001 — progress relay must not kill the poll
                logger.exception("modal poll progress relay failed")
            last_progress = progress
        if status in _TERMINAL_STATUSES:
            logger.info("modal: job %s finished as %s", job_id, status)
            return True
        poll_expired = time.monotonic() - started >= _POLL_TIMEOUT_S
        # A live Modal container keeps updated_at fresh via its progress writes.
        modal_alive = claimed_by.startswith("modal-executor:") and (
            updated_at is not None and updated_at >= _stale_cutoff()
        )
        if poll_expired and not modal_alive:
            logger.warning(
                "modal poll for job %s expired with no live container — local fallback",
                job_id,
            )
            return False
