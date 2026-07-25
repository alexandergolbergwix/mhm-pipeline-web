"""Router tests for POST /hmo-studio/build-items (background job)."""

from __future__ import annotations

import pytest

from app.models.run_job import JOB_KIND_HMO_ITEM_BUILD, JOB_STATUS_SUCCEEDED, RunJob
from app.pipeline.rdf_build import rdf_output_path_for_run

_TTL = """
@prefix hm: <https://w3id.org/mhm/ontology#> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

hm:MS1 rdf:type hm:Codicological_Unit ;
    rdfs:label "Test MS"@en ;
    hm:has_date_of_creation "1500" .
"""


@pytest.mark.asyncio
async def test_build_items_requires_rdf_first(sample_run) -> None:
    run_id = sample_run["run_id"]

    response = await sample_run["client"].post(
        f"/api/runs/{run_id}/hmo-studio/build-items?refresh_authority=false"
    )
    assert response.status_code == 409
    assert "RDF graph" in response.json()["detail"]


@pytest.mark.asyncio
async def test_build_items_enqueues_job(sample_run, db_session) -> None:
    run_id = sample_run["run_id"]
    ttl_path = rdf_output_path_for_run(str(run_id))
    ttl_path.parent.mkdir(parents=True, exist_ok=True)
    ttl_path.write_text(_TTL, encoding="utf-8")

    response = await sample_run["client"].post(
        f"/api/runs/{run_id}/hmo-studio/build-items?refresh_authority=false"
    )
    assert response.status_code == 201
    body = response.json()
    assert body["kind"] == JOB_KIND_HMO_ITEM_BUILD
    assert body["status"] in ("queued", "running", "succeeded", "failed")
    assert body["params"]["refresh_authority"] is False

    ttl_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_build_items_409_when_active_job_exists(sample_run, db_session) -> None:
    run_id = sample_run["run_id"]
    project_id = sample_run["project_id"]
    user_id = sample_run["user_id"]
    ttl_path = rdf_output_path_for_run(str(run_id))
    ttl_path.parent.mkdir(parents=True, exist_ok=True)
    ttl_path.write_text(_TTL, encoding="utf-8")

    db_session.add(RunJob(
        project_id=project_id,
        run_id=run_id,
        kind=JOB_KIND_HMO_ITEM_BUILD,
        status="running",
        params={"force_rebuild": False, "refresh_authority": False},
        created_by=user_id,
        progress={},
    ))
    await db_session.commit()

    response = await sample_run["client"].post(
        f"/api/runs/{run_id}/hmo-studio/build-items?refresh_authority=false"
    )
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert "already running" in str(detail).lower() or (
        isinstance(detail, dict) and "job_id" in detail
    )

    ttl_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_hmo_item_build_job_worker(sample_run, db_session, monkeypatch) -> None:
    from app.pipeline import hmo_item_build_job as job_module
    from app.pipeline.hmo_item_build_exec import HmoItemBuildJobResult

    run_id = sample_run["run_id"]
    project_id = sample_run["project_id"]
    user_id = sample_run["user_id"]

    job = RunJob(
        project_id=project_id,
        run_id=run_id,
        kind=JOB_KIND_HMO_ITEM_BUILD,
        status="running",
        params={"force_rebuild": True, "refresh_authority": False},
        created_by=user_id,
        progress={},
    )
    db_session.add(job)
    await db_session.commit()
    job_id = job.id

    async def _fake_exec(*_a, **_k):
        return HmoItemBuildJobResult(
            from_cache=False,
            entity_count=3,
            deferred_link_count=1,
            skipped_statement_count=0,
            refreshed_authority=False,
            rebuilt_rdf=False,
        )

    async def _noop(*_a, **_k):
        return None

    async def _never(_jid):
        return False

    monkeypatch.setattr(job_module, "execute_hmo_item_build", _fake_exec)
    monkeypatch.setattr(job_module, "update_job_progress", _noop)
    monkeypatch.setattr(job_module, "is_cancel_requested", _never)

    await job_module.run_hmo_item_build_job(job_id)

    db_session.expire_all()
    refreshed = await db_session.get(RunJob, job_id)
    assert refreshed is not None
    assert refreshed.status == JOB_STATUS_SUCCEEDED
    assert refreshed.result["entity_count"] == 3
