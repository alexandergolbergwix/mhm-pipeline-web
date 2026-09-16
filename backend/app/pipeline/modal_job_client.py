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
from typing import Any, Awaitable, Callable

import httpx
from sqlalchemy import select

from app.settings import get_settings

logger = logging.getLogger(__name__)

# Job kinds with a Modal executor. Everything else always runs locally
# (wikidata builds / publication stay Heroku-side — curator decision).
MODAL_JOB_KINDS = frozenset({"rdf_build", "hmo_item_build"})

# Modal web endpoint: dispatch must be quick (it only spawns).
_DISPATCH_TIMEOUT_S = 20.0
# The detached Modal function runs with timeout=7200; poll a little past it.
_POLL_TIMEOUT_S = 7500.0
# Seconds between row polls (also the cadence at which progress is relayed).
_POLL_INTERVAL_S = 10.0
_TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled"})


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

    True only when Modal accepted the dispatch AND the row reached a
    terminal status. While polling, the claiming process stays alive (its
    heartbeat covers the running row for ``fail_stale_jobs``).
    """
    settings = get_settings()
    if not settings.modal_jobs_url or kind not in MODAL_JOB_KINDS:
        return False
    try:
        accepted = await _dispatch(str(job_id), kind)
    except Exception as exc:  # noqa: BLE001 — network errors degrade locally
        logger.warning("modal dispatch failed for %s %s: %s", kind, job_id, exc)
        return False
    if not accepted:
        return False

    logger.info("modal: dispatched %s job %s", kind, job_id)
    started = time.monotonic()
    from app.db import session_scope  # noqa: PLC0415
    from app.models.run_job import RunJob  # noqa: PLC0415

    last_progress: dict[str, Any] = {}
    while time.monotonic() - started < _POLL_TIMEOUT_S:
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
        if progress != last_progress and on_progress is not None:
            try:
                await on_progress(progress)
            except Exception:  # noqa: BLE001 — progress relay must not kill the poll
                logger.exception("modal poll progress relay failed")
            last_progress = progress
        if status in _TERMINAL_STATUSES:
            logger.info("modal: %s job %s finished as %s", kind, job_id, status)
            return True

    logger.warning(
        "modal poll for %s job %s exceeded %.0fs — falling back to local execution",
        kind, job_id, _POLL_TIMEOUT_S,
    )
    return False
