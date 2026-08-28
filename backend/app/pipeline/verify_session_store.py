"""Load verify sessions from disk or the completed job row (Heroku-safe).

Per-dyno ``/tmp`` state is not visible across web dynos. Background verify
jobs therefore embed a ``session_snapshot`` in ``run_jobs.result`` at finish
time; session GET handlers fall back to that snapshot when the trace dir is
missing on the serving dyno.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.run_job import (
    JOB_KIND_AUTHORITY_VERIFY,
    JOB_KIND_HMO_ITEM_VERIFY,
    JOB_KIND_NER_VERIFY,
    JOB_KIND_WIKIDATA_VERIFY,
    JOB_STATUS_CANCELLED,
    JOB_STATUS_FAILED,
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
    # Include failed/cancelled so a dyno-interrupted verify (Rule W-130) can
    # still hydrate prior TRACE/snapshot verdicts for Continue.
    rows = (
        await db.execute(
            select(RunJob)
            .where(
                RunJob.run_id == run_id,
                RunJob.kind == job_kind,
                RunJob.status.in_((
                    JOB_STATUS_SUCCEEDED,
                    JOB_STATUS_FAILED,
                    JOB_STATUS_CANCELLED,
                )),
            )
            .order_by(RunJob.finished_at.desc())
        )
    ).scalars().all()
    for job in rows:
        if str((job.params or {}).get("session_id") or "") != session_id:
            continue
        result = job.result or {}
        snap = result.get("session_snapshot")
        if isinstance(snap, dict) and (snap.get("session_id") or snap.get("verdicts")):
            return snap
        progress = job.progress if isinstance(job.progress, dict) else {}
        prog_snap = progress.get("session_snapshot")
        if isinstance(prog_snap, dict) and (
            prog_snap.get("session_id") or prog_snap.get("verdicts")
        ):
            return prog_snap
    return None


async def load_verify_session(
    db: AsyncSession,
    *,
    run_id: uuid.UUID,
    session_id: str,
    channel: str,
    job_kind: str,
    slim: bool = True,
) -> dict[str, Any] | None:
    disk = await asyncio.to_thread(
        read_verify_session, channel, str(run_id), session_id,
    )
    disk_verdicts = len((disk or {}).get("verdicts") or [])
    job_snap: dict[str, Any] | None = None
    if disk_verdicts == 0:
        job_snap = await fetch_verify_session_from_job(
            db, run_id=run_id, session_id=session_id, job_kind=job_kind,
        )
    job_verdicts = len((job_snap or {}).get("verdicts") or [])
    if job_snap and job_verdicts > disk_verdicts:
        data = job_snap
    elif disk is not None and (disk.get("events") or disk.get("verdicts")):
        data = disk
    else:
        data = job_snap
    if data is None:
        return None
    if slim:
        return slim_api_verify_session(data)
    return data


def slim_api_verify_session(data: dict[str, Any]) -> dict[str, Any]:
    """API response: compact verdicts only — never ship TRACE events (W-133)."""
    return slim_job_session_snapshot({
        "session_id": data.get("session_id"),
        "run_id": data.get("run_id"),
        "verdicts": data.get("verdicts") or [],
        "events": [],
    })


def snapshot_from_collected_events(
    *,
    run_id: str,
    session_id: str,
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    return build_verify_session_payload(run_id, session_id, events)


def _compact_verdict_for_job(row: dict[str, Any]) -> dict[str, Any]:
    """Keep VerdictsTable fields; drop MARC/evidence megabytes (Rule W-128)."""
    from app.pipeline.ai_verdict_cache_common import normalise_public_verdict

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
    judge_error = str(row.get("error") or verd_in.get("error") or "").strip()
    if str(verd_in.get("overall") or "").lower() == "verification_failed":
        verd_in = normalise_public_verdict({**verd_in, "error": judge_error})
    reasoning = str(verd_in.get("reasoning") or "")
    if not reasoning and judge_error:
        # Dropping a falsy reasoning while keeping the axes is what made a judge
        # failure render as a reasonless `fail` in the UI (Rule W-158).
        reasoning = f"Judge failure: {judge_error}"
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
            "judge_failure", "verification_error",
        )
        if verd_in.get(k) not in (None, "")
    }
    if reasoning:
        verd_out["reasoning"] = reasoning
    if judge_error:
        verd_out["error"] = judge_error
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
