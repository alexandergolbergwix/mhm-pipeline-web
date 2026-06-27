"""Load verify sessions from disk or the completed job row (Heroku-safe).

Per-dyno ``/tmp`` state is not visible across web dynos. Background verify
jobs therefore embed a ``session_snapshot`` in ``run_jobs.result`` at finish
time; session GET handlers fall back to that snapshot when the trace dir is
missing on the serving dyno.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.run_job import (
    JOB_KIND_AUTHORITY_VERIFY,
    JOB_KIND_NER_VERIFY,
    JOB_KIND_WIKIDATA_VERIFY,
    JOB_STATUS_SUCCEEDED,
    RunJob,
)
from app.pipeline.agent_runner import build_verify_session_payload, read_verify_session

VERIFY_JOB_CHANNELS: dict[str, str] = {
    JOB_KIND_NER_VERIFY: "extraction-verify-sessions",
    JOB_KIND_AUTHORITY_VERIFY: "ai-verify-sessions",
    JOB_KIND_WIKIDATA_VERIFY: "wikidata-verify-sessions",
}


async def fetch_verify_session_from_job(
    db: AsyncSession,
    *,
    run_id: uuid.UUID,
    session_id: str,
    job_kind: str,
) -> dict[str, Any] | None:
    rows = (
        await db.execute(
            select(RunJob)
            .where(
                RunJob.run_id == run_id,
                RunJob.kind == job_kind,
                RunJob.status == JOB_STATUS_SUCCEEDED,
            )
            .order_by(RunJob.finished_at.desc())
        )
    ).scalars().all()
    for job in rows:
        if str((job.params or {}).get("session_id") or "") != session_id:
            continue
        snap = (job.result or {}).get("session_snapshot")
        if isinstance(snap, dict) and snap.get("session_id"):
            return snap
    return None


async def load_verify_session(
    db: AsyncSession,
    *,
    run_id: uuid.UUID,
    session_id: str,
    channel: str,
    job_kind: str,
) -> dict[str, Any] | None:
    disk = read_verify_session(channel, str(run_id), session_id)
    job_snap = await fetch_verify_session_from_job(
        db, run_id=run_id, session_id=session_id, job_kind=job_kind,
    )
    disk_verdicts = len((disk or {}).get("verdicts") or [])
    job_verdicts = len((job_snap or {}).get("verdicts") or [])
    if job_snap and job_verdicts > disk_verdicts:
        return job_snap
    if disk is not None and (disk.get("events") or disk.get("verdicts")):
        return disk
    return job_snap


def snapshot_from_collected_events(
    *,
    run_id: str,
    session_id: str,
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    return build_verify_session_payload(run_id, session_id, events)
