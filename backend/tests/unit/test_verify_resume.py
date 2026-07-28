"""Interrupted AI verify resume helpers (Rule W-130 / W-134)."""

from __future__ import annotations

from types import SimpleNamespace

from app.models.run_job import JOB_STATUS_QUEUED, JOB_STATUS_RUNNING
from app.pipeline.verify_resume import (
    STALE_GENERIC_ERROR,
    apply_verify_job_auto_resume,
    is_verify_job_kind,
    progress_counts,
    resumable_verify_result,
    stale_verify_error_message,
    verify_job_can_auto_resume,
)


def test_resumable_when_partial_judged() -> None:
    result = resumable_verify_result(
        session_id="sess-1",
        judged=61,
        total=313,
    )
    assert result["resumable"] is True
    assert result["outcome"] == "partial"
    assert result["interrupted"] is True
    assert result["remaining"] == 252
    assert "resuming automatically" in stale_verify_error_message(judged=61, total=313)


def test_not_resumable_when_nothing_judged() -> None:
    result = resumable_verify_result(session_id="sess-1", judged=0, total=10)
    assert result["resumable"] is False
    assert "Start again" in stale_verify_error_message(judged=0, total=10)


def test_verify_kinds_and_progress_counts() -> None:
    assert is_verify_job_kind("wikidata_verify")
    assert is_verify_job_kind("hmo_item_verify")
    assert not is_verify_job_kind("rdf_build")
    assert progress_counts({"processed": 3, "total": 10}) == (3, 10)
    assert STALE_GENERIC_ERROR.startswith("Job interrupted")


def test_apply_verify_job_auto_resume_requeues_with_new_session() -> None:
    job = SimpleNamespace(
        kind="wikidata_verify",
        cancel_requested_at=None,
        params={"session_id": "old", "action_id": "audit_wikidata_item"},
        progress={
            "phase": "running",
            "processed": 40,
            "total": 313,
            "session_id": "old",
        },
        status=JOB_STATUS_RUNNING,
        claimed_by="dyno:abc",
        error="stale",
        result={"resumable": True},
        finished_at=None,
        updated_at=None,
    )
    assert verify_job_can_auto_resume(job)
    assert apply_verify_job_auto_resume(job) is True
    assert job.status == JOB_STATUS_QUEUED
    assert job.params["override_cache"] is False
    assert job.params["session_id"] != "old"
    assert job.progress["processed"] == 40
    assert job.error is None
