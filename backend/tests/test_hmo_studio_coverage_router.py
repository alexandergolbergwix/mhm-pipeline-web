"""Router tests for GET /runs/{run_id}/hmo-studio/coverage.

Pins the background-job fix: on a cache miss the endpoint must enqueue
a ``hmo_coverage`` run job and return 409 immediately instead of
building the report inline (which used to hold the request — and its
DB connection — open until Heroku's 30s router timeout on large runs).
"""

from __future__ import annotations

import json
import uuid

import pytest

from app.models.run_job import JOB_KIND_HMO_COVERAGE, JOB_STATUS_SUCCEEDED, RunJob
from app.pipeline import hmo_studio as hmo_pipeline
from app.pipeline.rdf_build import rdf_output_path_for_run

_TTL = """
@prefix hm: <https://w3id.org/mhm/ontology#> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

hm:MS1 rdf:type hm:Codicological_Unit ;
    rdfs:label "Test MS"@en .
"""


@pytest.mark.asyncio
async def test_coverage_requires_rdf_first(sample_run) -> None:
    run_id = sample_run["run_id"]

    response = await sample_run["client"].get(
        f"/api/runs/{run_id}/hmo-studio/coverage",
    )
    assert response.status_code == 409
    assert "RDF graph" in response.json()["detail"]


@pytest.mark.asyncio
async def test_coverage_cache_hit_returns_immediately(sample_run) -> None:
    run_id = sample_run["run_id"]
    cache_path = hmo_pipeline.coverage_path_for_run(str(run_id))
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps({"rdf_class_count": 1, "wikidata_item_count": 1, "classes": []}),
        encoding="utf-8",
    )

    response = await sample_run["client"].get(
        f"/api/runs/{run_id}/hmo-studio/coverage",
    )
    assert response.status_code == 200
    assert response.json()["rdf_class_count"] == 1

    cache_path.unlink()


@pytest.mark.asyncio
async def test_coverage_cache_miss_enqueues_job_instead_of_blocking(
    sample_run, db_session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = sample_run["run_id"]
    ttl_path = rdf_output_path_for_run(str(run_id))
    ttl_path.parent.mkdir(parents=True, exist_ok=True)
    ttl_path.write_text(_TTL, encoding="utf-8")

    called = {"spawned": False}
    monkeypatch.setattr(
        "app.pipeline.run_job_service.spawn_job",
        lambda *_a, **_k: called.__setitem__("spawned", True),
    )

    response = await sample_run["client"].get(
        f"/api/runs/{run_id}/hmo-studio/coverage",
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "hmo_coverage_in_progress"
    assert detail["job_id"]
    # The heavy rdflib work never ran inline — a job was scheduled instead.
    assert called["spawned"] is True

    job = await db_session.get(RunJob, uuid.UUID(detail["job_id"]))
    assert job is not None
    assert job.kind == JOB_KIND_HMO_COVERAGE

    ttl_path.unlink()


@pytest.mark.asyncio
async def test_coverage_second_request_reuses_active_job(
    sample_run, monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = sample_run["run_id"]
    ttl_path = rdf_output_path_for_run(str(run_id))
    ttl_path.parent.mkdir(parents=True, exist_ok=True)
    ttl_path.write_text(_TTL, encoding="utf-8")
    monkeypatch.setattr("app.pipeline.run_job_service.spawn_job", lambda *_a, **_k: None)

    r1 = await sample_run["client"].get(f"/api/runs/{run_id}/hmo-studio/coverage")
    r2 = await sample_run["client"].get(f"/api/runs/{run_id}/hmo-studio/coverage")

    assert r1.json()["detail"]["job_id"] == r2.json()["detail"]["job_id"]

    ttl_path.unlink()


@pytest.mark.asyncio
async def test_coverage_restores_from_durable_db_cache_on_disk_miss(
    sample_run, db_session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dyno restart wipes the on-disk cache but the durable Postgres
    cache (keyed by TTL fingerprint) must serve the report instantly —
    no background job — when the RDF graph hasn't changed since."""
    from app.pipeline import hmo_studio as hmo_pipeline

    run_id = sample_run["run_id"]
    ttl_path = rdf_output_path_for_run(str(run_id))
    ttl_path.parent.mkdir(parents=True, exist_ok=True)
    ttl_path.write_text(_TTL, encoding="utf-8")

    fingerprint = await hmo_pipeline.compute_coverage_fingerprint(ttl_path)
    fake_report = {"rdf_class_count": 5, "wikidata_item_count": 4, "classes": []}
    await hmo_pipeline.save_coverage_to_db(db_session, run_id, fingerprint, fake_report)

    spawned = {"called": False}
    monkeypatch.setattr(
        "app.pipeline.run_job_service.spawn_job",
        lambda *_a, **_k: spawned.__setitem__("called", True),
    )

    response = await sample_run["client"].get(
        f"/api/runs/{run_id}/hmo-studio/coverage",
    )

    assert response.status_code == 200, response.text
    assert response.json() == fake_report
    assert spawned["called"] is False

    # The on-disk cache should now be re-seeded so the next request is
    # even faster and doesn't need the DB round trip.
    cache_path = hmo_pipeline.coverage_path_for_run(str(run_id))
    assert cache_path.exists()
    cache_path.unlink()
    ttl_path.unlink()


@pytest.mark.asyncio
async def test_coverage_stale_db_cache_still_enqueues_rebuild(
    sample_run, db_session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fingerprint mismatch (RDF changed since the cached build) must
    not be served stale — it should fall through to the background job."""
    from app.pipeline import hmo_studio as hmo_pipeline

    run_id = sample_run["run_id"]
    ttl_path = rdf_output_path_for_run(str(run_id))
    ttl_path.parent.mkdir(parents=True, exist_ok=True)
    ttl_path.write_text(_TTL, encoding="utf-8")

    stale_report = {"rdf_class_count": 1, "wikidata_item_count": 1, "classes": []}
    await hmo_pipeline.save_coverage_to_db(
        db_session, run_id, "0" * 64, stale_report,
    )
    monkeypatch.setattr("app.pipeline.run_job_service.spawn_job", lambda *_a, **_k: None)

    response = await sample_run["client"].get(
        f"/api/runs/{run_id}/hmo-studio/coverage",
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "hmo_coverage_in_progress"

    ttl_path.unlink()


@pytest.mark.asyncio
async def test_hmo_coverage_job_writes_cache_on_success(
    sample_run, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The background worker itself: builds the report and caches it so
    the next GET is served from disk without another job."""
    from app.models.run_job import JOB_STATUS_QUEUED
    from app.pipeline.hmo_coverage_job import run_hmo_coverage_job

    run_id = sample_run["run_id"]
    ttl_path = rdf_output_path_for_run(str(run_id))
    ttl_path.parent.mkdir(parents=True, exist_ok=True)
    ttl_path.write_text(_TTL, encoding="utf-8")

    fake_report = {"rdf_class_count": 3, "wikidata_item_count": 2, "classes": []}
    monkeypatch.setattr(
        hmo_pipeline, "coverage_report_for_run",
        lambda *, ttl_path: _async_return(fake_report),
    )

    from app.db import session_scope
    from app.models.run_job import RunJob

    async with session_scope() as db:
        job = RunJob(
            project_id=sample_run["project_id"], run_id=run_id,
            kind=JOB_KIND_HMO_COVERAGE, status=JOB_STATUS_QUEUED,
            params={}, progress={}, created_by=sample_run["user_id"],
        )
        db.add(job)
        await db.commit()
        await db.refresh(job)
        job_id = job.id

    await run_hmo_coverage_job(job_id)

    async with session_scope() as db:
        finished = await db.get(RunJob, job_id)
        assert finished.status == JOB_STATUS_SUCCEEDED
        assert finished.result == fake_report

    cache_path = hmo_pipeline.coverage_path_for_run(str(run_id))
    assert cache_path.exists()
    assert json.loads(cache_path.read_text(encoding="utf-8")) == fake_report
    cache_path.unlink()

    fingerprint = await hmo_pipeline.compute_coverage_fingerprint(ttl_path)
    async with session_scope() as db:
        db_report = await hmo_pipeline.load_cached_coverage_from_db(db, run_id, fingerprint)
    assert db_report == fake_report

    ttl_path.unlink()


async def _async_return(value):
    return value
