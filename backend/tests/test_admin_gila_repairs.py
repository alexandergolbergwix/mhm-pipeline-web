import json
import zlib
from base64 import b64encode
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from app.models.run_job import RunJob
from app.models.user import User
from app.pipeline import wikidata_gila_audit_job as worker
from app.pipeline.wikidata_qid_ledger import record_ledger_mapping
from app.routers import admin_gila_repairs as router
from app.routers import wikidata_studio
from tests.test_publication_router import _add_canonical_cache, _canonical_item


def test_public_audit_snapshot_retains_exact_entities_across_batches(tmp_path):
    snapshot = worker.PublicAuditSnapshot(str(tmp_path))
    first = {"id": "Q1", "lastrevid": 12, "claims": {}}
    snapshot.add_batch({"Q1": first})
    assert snapshot.get("Q1") == first
    snapshot.add_batch({"Q2": {"id": "Q2", "lastrevid": 13, "claims": {}}})
    assert list(snapshot) == ["Q1", "Q2"]
    assert snapshot.get("Q3") is None
    with pytest.raises(ValueError, match="one response per exact QID"):
        snapshot.add_batch({"Q1": first})


@pytest.mark.asyncio
@pytest.mark.parametrize("fresh", [True, False])
async def test_worker_streams_the_canonical_source_and_saves_read_only_report(
    sample_run,
    db_session,
    monkeypatch,
    fresh,
):
    run_id = sample_run["run_id"]
    user = await db_session.get(User, sample_run["user_id"])
    user.role = "admin"
    item = _canonical_item("manuscript:one", "One manuscript")
    item.update(entity_type="manuscript", existing_qid="Q42", records=["990000000000000001"])
    await _add_canonical_cache(db_session, run_id, items=[item])
    await record_ledger_mapping(db_session, "wikidata:marc:990000000000000001", "Q42")
    job = RunJob(
        project_id=sample_run["project_id"],
        run_id=run_id,
        created_by=user.id,
        kind=worker.KIND,
        status="running",
        params={},
    )
    db_session.add(job)
    await db_session.commit()
    monkeypatch.setattr(worker, "load_seed", lambda: ({"manifest": {"qids": []}}, ""))
    monkeypatch.setattr(worker, "remaining_reference_inventory", lambda: {"items": []})
    entity = {
        "id": "Q42",
        "lastrevid": 22,
        "labels": {},
        "descriptions": {},
        "aliases": {},
        "sitelinks": {},
        "claims": {
            "P3959": [
                {
                    "rank": "normal",
                    "mainsnak": {
                        "snaktype": "value",
                        "property": "P3959",
                        "datavalue": {"type": "string", "value": "990000000000000001"},
                    },
                }
            ]
        },
    }
    batch = AsyncMock(return_value={"Q42": entity})
    monkeypatch.setattr(worker.ReferenceItemReader, "fetch_batch", batch)
    check_current = wikidata_studio._studio_cache_is_current
    timeout_values = []

    async def check_with_production_delay(*args, timeout_seconds=None, **kwargs):
        timeout_values.append(timeout_seconds)
        if timeout_seconds != wikidata_studio.STUDIO_JOB_FRESHNESS_TIMEOUT_SECONDS:
            raise HTTPException(409, "The source check exceeded the web request time limit.")
        return fresh and await check_current(*args, timeout_seconds=timeout_seconds, **kwargs)

    monkeypatch.setattr(wikidata_studio, "_studio_cache_is_current", check_with_production_delay)
    await worker.run_gila_audit_job(job.id)
    await db_session.refresh(job)
    if not fresh:
        assert job.status == "failed"
        assert job.progress["phase"] == "source_rebuild_required"
        assert job.result is None
        batch.assert_not_awaited()
        assert timeout_values == [wikidata_studio.STUDIO_JOB_FRESHNESS_TIMEOUT_SECONDS]
        return
    assert job.status == "succeeded", job.error
    assert job.result["record_count"] == job.result["public_read"] == 1
    assert len(job.result["email_points"]) == 9
    assert job.result["all_requirements_verified"] is False
    assert worker.gila_cases(job)
    batch.assert_awaited_once()
    assert timeout_values == [wikidata_studio.STUDIO_JOB_FRESHNESS_TIMEOUT_SECONDS] * 2


@pytest.mark.asyncio
async def test_admin_audit_start_progress_and_case_pagination(sample_run, db_session, monkeypatch):
    client = sample_run["client"]
    run_id = sample_run["run_id"]
    user = await db_session.get(User, sample_run["user_id"])
    monkeypatch.setattr(router, "_run_id", lambda: run_id)
    spawn = Mock()
    monkeypatch.setattr("app.pipeline.run_job_service.spawn_job", spawn)
    base = "/api/admin/wikidata-gila-repairs"
    assert (await client.get(base)).status_code == 403
    user.role = "admin"
    await db_session.commit()
    direct = await client.post(f"/api/runs/{run_id}/jobs", json={"kind": router.KIND, "params": {}})
    assert direct.status_code == 403
    launched = await client.post(base + "/start")
    assert launched.status_code == 200
    again = await client.post(base + "/start")
    assert again.json()["id"] == launched.json()["id"]
    spawn.assert_called_once()
    job = await db_session.get(RunJob, spawn.call_args.args[0])
    job.status = "succeeded"
    job.result = {"case_count": 3}
    cases = [
        {
            "case_id": "a",
            "disposition": "manual_review",
            "points": [1],
            "qid": "Q1",
            "classification": "absent_in_source",
        },
        {"case_id": "b", "disposition": "manual_review", "points": [1], "qid": "Q2"},
        {"case_id": "c", "disposition": "manual_review", "points": [8], "qid": "Q2"},
    ]
    job.params = {"_gila_cases": b64encode(zlib.compress(json.dumps(cases).encode())).decode()}
    await db_session.commit()
    status = await client.get(base)
    assert status.json()["report"]["case_count"] == 3
    assert "_gila_cases" not in status.text
    response = await client.get(
        base + "/cases",
        params={"audit_id": str(job.id), "disposition": "manual_review", "query": "Q2", "limit": 1},
    )
    assert response.json() == {"items": [cases[1]], "total": 2}
    rejected = await client.post(base + "/prepare", json={"audit_id": str(job.id), "case_id": "b"})
    assert rejected.status_code == 409
    injected = await client.post(
        base + "/prepare",
        json={"audit_id": str(job.id), "case_id": "b", "qid": "Q999"},
    )
    assert injected.status_code == 422
    user.role = "editor"
    await db_session.commit()
    assert (await client.get(f"/api/runs/{run_id}/jobs/{job.id}")).status_code == 403
