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
    JOB_KIND_HMO_ITEM_VERIFY,
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
    JOB_KIND_HMO_ITEM_VERIFY: "hmo-item-verify-sessions",
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


def _compact_verdict_for_job(row: dict[str, Any]) -> dict[str, Any]:
    """Keep VerdictsTable fields; drop MARC/evidence megabytes (Rule W-128)."""
    cand_in = row.get("candidate") if isinstance(row.get("candidate"), dict) else {}
    verd_in = row.get("verdict") if isinstance(row.get("verdict"), dict) else {}
    if not verd_in and row.get("overall") is not None:
        verd_in = {
            "overall": row.get("overall"),
            "name_ok": row.get("name_ok"),
            "type_ok": row.get("type_ok"),
            "role_ok": row.get("role_ok"),
            "reasoning": row.get("reasoning"),
            "model": row.get("model"),
            "judged_at": row.get("judged_at"),
            "confidence": row.get("confidence"),
        }
    reasoning = str(verd_in.get("reasoning") or "")
    if len(reasoning) > 800:
        reasoning = reasoning[:797] + "…"
    cand_out = {
        k: cand_in.get(k)
        for k in (
            "_local_id", "_item_id", "_match_id", "_entity_id",
            "local_id", "label", "entity_type", "control_number",
            "qid", "existing_qid",
        )
        if cand_in.get(k) not in (None, "")
    }
    verd_out = {
        k: verd_in.get(k)
        for k in (
            "overall", "name_ok", "type_ok", "role_ok", "confidence",
            "model", "judged_at", "evaluator",
        )
        if verd_in.get(k) not in (None, "")
    }
    if reasoning:
        verd_out["reasoning"] = reasoning
    out: dict[str, Any] = {}
    if cand_out:
        out["candidate"] = cand_out
    if verd_out:
        out["verdict"] = verd_out
    for k in ("sub_type", "evaluator", "record_id", "control_number"):
        if row.get(k) not in (None, ""):
            out[k] = row[k]
    return out or dict(row)


def slim_job_session_snapshot(snap: dict[str, Any]) -> dict[str, Any]:
    """Wire-safe snapshot: compact verdicts, no TRACE events (Rule W-128)."""
    verdicts = [
        _compact_verdict_for_job(v)
        for v in (snap.get("verdicts") or [])
        if isinstance(v, dict)
    ]
    return {
        "session_id": snap.get("session_id"),
        "run_id": snap.get("run_id"),
        "verdicts": verdicts,
        "events": [],
    }
