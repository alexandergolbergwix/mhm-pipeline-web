"""Modal dispatch for heavy job kinds (Rule W-237).

When ``MODAL_JOBS_URL`` is configured, ``rdf_build`` and ``hmo_item_build``
execute on the ``mhm-jobs`` Modal app instead of the Heroku dyno: the web
process stays the claimer/arbiter (admission, heartbeat, cancel) and the
Modal function does the compute, writing progress and the terminal state
directly to Postgres. The local runner is always the fallback — a Modal
outage, dispatch failure, or missed webhook degrades to the pre-existing
Heroku path (Rule W-15: Modal is a deploy target, never a hard dependency).

Execution protocol (see ``modal/modal_jobs.py``):

1. This process takes the exclusive **dispatch lease** — a conditional
   UPDATE moving ``claimed_by`` to ``modal-dispatch`` (from its own claim,
   or from a stale modal executor). A second dispatcher finds the lease
   held and waits instead of spawning a rival container.
2. ``POST {MODAL_JOBS_URL}`` with ``{job_id, kind, token, callback_url}``
   → ``202`` means the detached Modal function spawned. The container
   first atomically acquires ``claimed_by = modal-executor:<uuid>`` — a
   preemption restart waits for the dead container's lease to go stale,
   then takes over.
3. The web task waits on an ``asyncio.Event`` (no busy waiting): the
   container POSTs the completion webhook when the job reaches a terminal
   state. A slow 60 s row check is the only safety net; if the wait budget
   lapses with no live container, the local runner takes over (the RDF
   build resumes from its checkpoint).
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
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
# The detached Modal function runs with timeout=14400 (4 h).
_WAIT_BUDGET_S = 15000.0
# Safety-net row check while waiting for the completion webhook.
_SAFETY_TICK_S = 60.0
_TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled"})
# A modal-executor claim whose heartbeat is older than this is dead and
# may be taken over (by a re-dispatch or the local runner).
_STALE_AFTER_S = 120

# One waiter per in-flight Modal job, keyed by job id (this process only —
# WEB_CONCURRENCY is 1 in production, per run_job_service).
_WAITERS: dict[str, asyncio.Event] = {}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _stale_cutoff() -> datetime:
    return _now_utc() - timedelta(seconds=_STALE_AFTER_S)


def notify_modal_finished(job_id: uuid.UUID | str) -> None:
    """Wake the poller waiting for this job (called by the Modal webhook)."""
    event = _WAITERS.get(str(job_id))
    if event is not None:
        event.set()


async def _dispatch(job_id: str, kind: str, callback_url: str, token: str) -> bool:
    """Spawn the detached Modal run. Returns True on an accepted dispatch."""
    settings = get_settings()
    base = settings.modal_jobs_url.rstrip("/")
    async with httpx.AsyncClient(timeout=_DISPATCH_TIMEOUT_S) as client:
        resp = await client.post(
            base,  # the fastapi_endpoint mounts at the subdomain root
            headers={"Content-Type": "application/json"},
            json={
                "job_id": job_id,
                "kind": kind,
                "token": token,
                "callback_url": callback_url,
            },
        )
    # Modal's @modal.fastapi_endpoint answers 200 (not 202) with
    # {"ok": true, "spawned": true} when the detached runner spawned.
    # Treat any 2xx carrying ok=true (or the legacy 202) as accepted;
    # anything else is a rejection and the caller runs the job locally.
    if resp.status_code == 202:
        return True
    try:
        body = resp.json()
    except Exception:  # noqa: BLE001 — non-JSON body is never an accept
        body = None
    if 200 <= resp.status_code < 300 and isinstance(body, dict) and body.get("ok"):
        return True
    logger.warning(
        "modal dispatch for %s job %s rejected: %s %s",
        kind, job_id, resp.status_code, resp.text[:200],
    )
    return False


async def run_on_modal(
    job_id: uuid.UUID,
    run_id: uuid.UUID,
    kind: str,
    *,
    on_progress: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> bool:
    """Execute one job on Modal. Return False to run it locally instead.

    True only when the row reached a terminal status. While waiting, the
    claiming process stays alive (its heartbeat covers the running row for
    ``fail_stale_jobs``); completion arrives via webhook, with a slow row
    check as the safety net.
    """
    settings = get_settings()
    if not settings.modal_jobs_url or kind not in MODAL_JOB_KINDS:
        return False

    from app.db import session_scope  # noqa: PLC0415
    from app.models.run_job import RunJob  # noqa: PLC0415
    from app.pipeline.run_job_service import WORKER_ID  # noqa: PLC0415
    from sqlalchemy import update  # noqa: PLC0415

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
                .values(
                    claimed_by="modal-dispatch",
                    updated_at=datetime.now(timezone.utc),
                )
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
        # state — wait for it instead of spawning a rival container.
        logger.info("modal: lease held elsewhere for %s job %s — waiting", kind, job_id)
        return await _wait_for_terminal(job_id, on_progress)

    callback_url = (
        f"{settings.public_base_url.rstrip('/')}"
        f"/api/runs/{run_id}/jobs/{job_id}/modal-event"
    )
    try:
        accepted = await _dispatch(str(job_id), kind, callback_url, settings.modal_jobs_token)
    except Exception as exc:  # noqa: BLE001 — network errors degrade locally
        logger.warning("modal dispatch failed for %s %s: %s", kind, job_id, exc)
        return False
    if not accepted:
        return False

    logger.info("modal: dispatched %s job %s", kind, job_id)
    return await _wait_for_terminal(job_id, on_progress)


async def _wait_for_terminal(
    job_id: uuid.UUID,
    on_progress: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> bool:
    """Wait (event-driven) for the row to reach a terminal status.

    Woken by the Modal completion webhook; a 60 s row check is the safety
    net for a missed webhook. The wait budget matches the Modal function's
    timeout; past it, only a live container heartbeat keeps the wait alive
    — otherwise the caller falls back to local execution.
    """
    from app.db import session_scope  # noqa: PLC0415
    from app.models.run_job import RunJob  # noqa: PLC0415

    key = str(job_id)
    waiter = _WAITERS.setdefault(key, asyncio.Event())
    started = time.monotonic()
    last_progress: dict[str, Any] = {}

    try:
        while True:
            try:
                await asyncio.wait_for(waiter.wait(), timeout=_SAFETY_TICK_S)
            except asyncio.TimeoutError:
                pass
            waiter.clear()

            async with session_scope() as db:
                job = (
                    await db.execute(select(RunJob).where(RunJob.id == job_id))
                ).scalar_one_or_none()
                if job is None:
                    logger.warning("modal wait: job %s vanished", job_id)
                    return False
                progress = dict(job.progress or {})
                status = str(job.status)
                claimed_by = str(job.claimed_by or "")
                updated_at = job.updated_at

            if progress != last_progress and on_progress is not None:
                try:
                    await on_progress(progress)
                except Exception:  # noqa: BLE001 — relay must not kill the wait
                    logger.exception("modal progress relay failed")
                last_progress = progress
            if status in _TERMINAL_STATUSES:
                logger.info("modal: job %s finished as %s", job_id, status)
                return True

            budget_spent = time.monotonic() - started >= _WAIT_BUDGET_S
            container_alive = claimed_by.startswith("modal-executor:") and (
                updated_at is not None and updated_at >= _stale_cutoff()
            )
            if budget_spent and not container_alive:
                logger.warning(
                    "modal wait for job %s expired with no live container — local fallback",
                    job_id,
                )
                return False
    finally:
        _WAITERS.pop(key, None)
