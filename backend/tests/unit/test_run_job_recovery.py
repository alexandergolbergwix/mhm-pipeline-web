"""Startup recovery for interrupted background jobs."""

from __future__ import annotations

import uuid

import pytest

from app.models.run_job import JOB_KIND_RDF_BUILD, JOB_STATUS_RUNNING, RunJob
from app.pipeline.run_job_service import recover_interrupted_jobs


@pytest.mark.asyncio
async def test_recover_interrupted_jobs_respawns_active_rows(
    db_session, sample_run, monkeypatch,
) -> None:
    spawned: list[uuid.UUID] = []
    monkeypatch.setattr(
        "app.pipeline.run_job_service.spawn_job",
        lambda job_id: spawned.append(job_id),
    )

    job = RunJob(
        project_id=sample_run["project_id"],
        run_id=sample_run["run_id"],
        kind=JOB_KIND_RDF_BUILD,
        status=JOB_STATUS_RUNNING,
        params={},
        progress={"processed": 0, "total": 10},
        created_by=sample_run["user_id"],
    )
    db_session.add(job)
    await db_session.commit()

    count = await recover_interrupted_jobs()
    assert count == 1
    assert spawned == [job.id]
