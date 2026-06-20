"""Verify session Postgres fallback (Heroku multi-dyno safe)."""

from __future__ import annotations

import pytest

from app.models.run_job import JOB_KIND_WIKIDATA_VERIFY, JOB_STATUS_SUCCEEDED, RunJob
from app.pipeline.verify_session_store import (
    fetch_verify_session_from_job,
    load_verify_session,
    snapshot_from_collected_events,
)


@pytest.mark.asyncio
async def test_fetch_verify_session_from_job_returns_snapshot(db_session, sample_run) -> None:
    run_id = sample_run["run_id"]
    user_id = sample_run["user_id"]
    project_id = sample_run["project_id"]
    session_id = "20260620T120000000000Z"
    snap = snapshot_from_collected_events(
        run_id=str(run_id),
        session_id=session_id,
        events=[
            {
                "type": "agent.verdict",
                "record_id": "990001",
                "candidate": {"_local_id": "manuscript::x", "labels": {"en": "Test MS"}},
                "verdict": {"overall": "pass"},
            },
        ],
    )
    job = RunJob(
        project_id=project_id,
        run_id=run_id,
        kind=JOB_KIND_WIKIDATA_VERIFY,
        status=JOB_STATUS_SUCCEEDED,
        params={"session_id": session_id, "action_id": "audit_wikidata_item"},
        result={"session_id": session_id, "session_snapshot": snap},
        created_by=user_id,
    )
    db_session.add(job)
    await db_session.commit()

    loaded = await fetch_verify_session_from_job(
        db_session,
        run_id=run_id,
        session_id=session_id,
        job_kind=JOB_KIND_WIKIDATA_VERIFY,
    )
    assert loaded is not None
    assert len(loaded["verdicts"]) == 1
    assert loaded["verdicts"][0]["verdict"]["overall"] == "pass"


@pytest.mark.asyncio
async def test_load_verify_session_falls_back_to_job_when_disk_missing(
    db_session, sample_run, monkeypatch,
) -> None:
    run_id = sample_run["run_id"]
    session_id = "20260620T130000000000Z"

    def _no_disk(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        "app.pipeline.verify_session_store.read_verify_session",
        _no_disk,
    )

    snap = snapshot_from_collected_events(
        run_id=str(run_id),
        session_id=session_id,
        events=[{"type": "session.end", "outcome": "complete"}],
    )
    job = RunJob(
        project_id=sample_run["project_id"],
        run_id=run_id,
        kind=JOB_KIND_WIKIDATA_VERIFY,
        status=JOB_STATUS_SUCCEEDED,
        params={"session_id": session_id},
        result={"session_snapshot": snap},
        created_by=sample_run["user_id"],
    )
    db_session.add(job)
    await db_session.commit()

    loaded = await load_verify_session(
        db_session,
        run_id=run_id,
        session_id=session_id,
        channel="wikidata-verify-sessions",
        job_kind=JOB_KIND_WIKIDATA_VERIFY,
    )
    assert loaded is not None
    assert loaded["session_id"] == session_id
