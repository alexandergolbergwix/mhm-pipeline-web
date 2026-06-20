"""Background run job lifecycle tests."""

from __future__ import annotations

import pytest

from app.models.run_job import (
    JOB_KIND_AUTHORITY_RE_ENRICH,
    JOB_KIND_RDF_BUILD,
    JOB_STATUS_RUNNING,
    RunJob,
    SUPPORTED_JOB_KINDS,
)
from app.pipeline.run_job_service import create_job, serialise_job


@pytest.mark.asyncio
async def test_start_job_via_api(db_session, sample_run) -> None:
    client = sample_run["client"]
    run_id = sample_run["run_id"]

    r = await client.post(
        f"/api/runs/{run_id}/jobs",
        json={"kind": JOB_KIND_AUTHORITY_RE_ENRICH, "params": {"skip_cache": True}},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["kind"] == JOB_KIND_AUTHORITY_RE_ENRICH
    assert body["status"] in ("queued", "running", "succeeded", "failed")


@pytest.mark.asyncio
async def test_duplicate_active_job_returns_409(db_session, sample_run) -> None:
    run_id = sample_run["run_id"]
    project_id = sample_run["project_id"]
    user_id = sample_run["user_id"]
    client = sample_run["client"]

    job = await create_job(
        db_session,
        project_id=project_id,
        run_id=run_id,
        kind=JOB_KIND_AUTHORITY_RE_ENRICH,
        params={"skip_cache": False},
        created_by=user_id,
    )
    job.status = JOB_STATUS_RUNNING
    await db_session.commit()

    r = await client.post(
        f"/api/runs/{run_id}/jobs",
        json={"kind": JOB_KIND_AUTHORITY_RE_ENRICH, "params": {}},
    )
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_cancel_requested_on_active_job(db_session, sample_run) -> None:
    run_id = sample_run["run_id"]
    client = sample_run["client"]

    job = RunJob(
        project_id=sample_run["project_id"],
        run_id=run_id,
        kind=JOB_KIND_AUTHORITY_RE_ENRICH,
        status=JOB_STATUS_RUNNING,
        params={},
        progress={"processed": 1, "total": 10},
        created_by=sample_run["user_id"],
    )
    db_session.add(job)
    await db_session.commit()

    r = await client.post(f"/api/runs/{run_id}/jobs/{job.id}/cancel")
    assert r.status_code == 200
    body = r.json()
    assert body["cancel_requested_at"] is not None


@pytest.mark.asyncio
async def test_list_mine_active_jobs(db_session, sample_run) -> None:
    client = sample_run["client"]
    job = RunJob(
        project_id=sample_run["project_id"],
        run_id=sample_run["run_id"],
        kind=JOB_KIND_AUTHORITY_RE_ENRICH,
        status=JOB_STATUS_RUNNING,
        params={},
        progress={},
        created_by=sample_run["user_id"],
    )
    db_session.add(job)
    await db_session.commit()

    r = await client.get("/api/jobs/mine?active=true")
    assert r.status_code == 200
    ids = [j["id"] for j in r.json()["jobs"]]
    assert str(job.id) in ids


def test_supported_job_kinds_include_all_phases() -> None:
    expected = {
        "authority_re_enrich",
        "extraction",
        "ner_verify",
        "authority_verify",
        "wikidata_verify",
        "rdf_build",
        "wikidata_studio_build",
        "wikidata_upload",
    }
    assert expected <= SUPPORTED_JOB_KINDS


def test_serialise_job_strips_secret_params() -> None:
    job = RunJob(
        project_id=__import__("uuid").uuid4(),
        run_id=__import__("uuid").uuid4(),
        kind=JOB_KIND_RDF_BUILD,
        status="queued",
        params={"add_epistemological_status": True, "_api_key": "secret"},
        progress={},
    )
    out = serialise_job(job)
    assert out["params"].get("_api_key") is None
    assert out["params"]["add_epistemological_status"] is True
