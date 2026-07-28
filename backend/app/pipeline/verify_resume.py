"""Resume metadata for interrupted AI verify jobs (Rule W-130 / W-134)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.models.run_job import (
    JOB_KIND_AUTHORITY_VERIFY,
    JOB_KIND_HMO_ITEM_VERIFY,
    JOB_KIND_NER_VERIFY,
    JOB_KIND_WIKIDATA_VERIFY,
    JOB_STATUS_QUEUED,
    RunJob,
)
from app.pipeline.agent_runner import new_session_id

VERIFY_JOB_KINDS = frozenset({
    JOB_KIND_NER_VERIFY,
    JOB_KIND_AUTHORITY_VERIFY,
    JOB_KIND_WIKIDATA_VERIFY,
    JOB_KIND_HMO_ITEM_VERIFY,
})

STALE_VERIFY_RESUME_ERROR = (
    "Verification interrupted — the server restarted or the worker stopped "
    "responding. Cached verdicts were kept; resuming automatically."
)

STALE_VERIFY_RETRY_ERROR = (
    "Verification interrupted — the server restarted or the worker stopped "
    "responding. Start again; prior cache hits will be reused."
)

STALE_GENERIC_ERROR = (
    "Job interrupted — the server restarted or the worker "
    "stopped responding. Cancel and start again."
)


def is_verify_job_kind(kind: str) -> bool:
    return kind in VERIFY_JOB_KINDS


def resumable_verify_result(
    *,
    session_id: str | None,
    judged: int,
    total: int,
    session_snapshot: dict[str, Any] | None = None,
    interrupted: bool = True,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a terminal ``result`` that the curator UI can Continue from."""
    judged_n = max(0, int(judged or 0))
    total_n = max(0, int(total or 0))
    remaining = max(0, total_n - judged_n) if total_n else 0
    resumable = judged_n > 0 and (total_n == 0 or judged_n < total_n)
    out: dict[str, Any] = {
        "session_id": session_id or None,
        "judged": judged_n,
        "total": total_n or judged_n,
        "outcome": "partial",
        "resumable": resumable,
        "interrupted": interrupted,
        "remaining": remaining if total_n else None,
    }
    if isinstance(session_snapshot, dict) and session_snapshot.get("verdicts"):
        out["session_snapshot"] = session_snapshot
    if extra:
        for key, value in extra.items():
            if value is not None:
                out[key] = value
    return out


def stale_verify_error_message(*, judged: int, total: int) -> str:
    judged_n = max(0, int(judged or 0))
    total_n = max(0, int(total or 0))
    if judged_n > 0 and (total_n == 0 or judged_n < total_n):
        scope = f"{judged_n} of {total_n}" if total_n else str(judged_n)
        return (
            f"Verification interrupted after {scope}. "
            "Cached verdicts were kept — resuming automatically."
        )
    return STALE_VERIFY_RETRY_ERROR


def progress_counts(progress: dict[str, Any] | None) -> tuple[int, int]:
    prog = progress if isinstance(progress, dict) else {}
    judged = int(prog.get("processed") or 0)
    total = int(prog.get("total") or 0)
    return judged, total


def verify_job_can_auto_resume(job: RunJob) -> bool:
    if job.cancel_requested_at is not None:
        return False
    progress = job.progress if isinstance(job.progress, dict) else {}
    judged, total = progress_counts(progress)
    if judged <= 0:
        return False
    if total > 0 and judged >= total:
        return False
    return True


def apply_verify_job_auto_resume(job: RunJob) -> bool:
    """Re-queue a verify job so the worker continues from inference-cache hits.

    Mutates ``job`` in place. Returns True when the row was re-queued.
    """
    if not verify_job_can_auto_resume(job):
        return False
    progress = job.progress if isinstance(job.progress, dict) else {}
    judged, total = progress_counts(progress)
    session_id = new_session_id()
    params = dict(job.params or {})
    params["override_cache"] = False
    params["session_id"] = session_id
    job.params = params
    job.status = JOB_STATUS_QUEUED
    job.claimed_by = None
    job.error = None
    job.result = None
    job.finished_at = None
    scope = f"{judged} of {total}" if total else str(judged)
    job.progress = {
        **progress,
        "phase": "queued",
        "processed": judged,
        "total": total or judged,
        "message": f"Auto-resuming verification ({scope} already cached)…",
        "session_id": session_id,
    }
    job.updated_at = datetime.now(timezone.utc)
    return True
