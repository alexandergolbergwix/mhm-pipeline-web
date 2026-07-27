"""Interrupted AI verify resume helpers (Rule W-130)."""

from __future__ import annotations

from app.pipeline.verify_resume import (
    STALE_GENERIC_ERROR,
    is_verify_job_kind,
    progress_counts,
    resumable_verify_result,
    stale_verify_error_message,
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
    assert "Continue" in stale_verify_error_message(judged=61, total=313)


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
