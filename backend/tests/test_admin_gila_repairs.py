import json
import uuid
import zlib
from base64 import b64encode
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from app.models.run_job import RunJob
from app.models.user import User
from app.pipeline import wikidata_gila_audit_job as worker
from app.pipeline import wikidata_publication_prepare_job as prepare_worker
from app.pipeline.marc_verify_context import load_run_marc_records
from app.pipeline.wikidata_qid_ledger import record_ledger_mapping
from app.publication.runtime import StudioCacheProjectionSource
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
@pytest.mark.parametrize("state", ["current", "source_changed", "foreign", "mapping_changed"])
async def test_repair_preparation_checks_freshness_in_the_worker(
    sample_run, db_session, monkeypatch, state,
):
    run_id = sample_run["run_id"]
    user = await db_session.get(User, sample_run["user_id"])
    user.role = "admin"
    item = _canonical_item("work:1", "A work")
    item["existing_qid"] = "Q42"
    await _add_canonical_cache(db_session, run_id, items=[item])
    snapshot = await StudioCacheProjectionSource(db_session).current_snapshot(
        run_id=run_id, source="canonical", approved_only=True,
    )
    records = await load_run_marc_records(db_session, run_id)
    cases = [{"case_id": "repair:1", "qid": "Q42", "local_id": "work:1", "can_prepare": True}]
    report = RunJob(
        project_id=sample_run["project_id"], run_id=run_id, created_by=user.id,
        kind=worker.KIND, status="succeeded", result={},
        params={
            "_gila_cases": b64encode(zlib.compress(json.dumps(cases).encode())).decode(),
            "_gila_build_revision": snapshot.revision,
            "_gila_build_digest": snapshot.digest,
            "_gila_source_fingerprint": "stale" if state == "source_changed"
            else worker.gila_record_fingerprint(records),
        },
    )
    db_session.add(report)
    await db_session.commit()
    monkeypatch.setattr(router, "_run_id", lambda: run_id)
    creator = AsyncMock(return_value=12 if state == "foreign" else 6935067)
    monkeypatch.setattr(router.ReferenceItemReader, "creator_uid", creator)
    fetch_item = AsyncMock(return_value=None if state == "mapping_changed" else item)
    monkeypatch.setattr(prepare_worker, "fetch_merged_wikidata_item", fetch_item)
    spawn = Mock()
    monkeypatch.setattr("app.pipeline.run_job_service.spawn_job", spawn)
    check_current = wikidata_studio._studio_cache_is_current
    timeouts = []

    async def checked(*args, timeout_seconds=None, **kwargs):
        timeouts.append(timeout_seconds)
        return await check_current(*args, timeout_seconds=timeout_seconds, **kwargs)

    monkeypatch.setattr(wikidata_studio, "_studio_cache_is_current", checked)
    response = await sample_run["client"].post(
        "/api/admin/wikidata-gila-repairs/prepare",
        json={"audit_id": str(report.id), "case_id": "repair:1"},
    )
    if state == "foreign":
        assert response.status_code == 403
        spawn.assert_not_called()
        return
    assert response.status_code == 200, response.text
    assert timeouts == []
    fetch_item.assert_not_awaited()
    job_id = uuid.UUID(response.json()["id"])
    await prepare_worker.run_wikidata_publication_prepare_job(job_id)
    job = await db_session.get(RunJob, job_id)
    if state != "current":
        assert job.status == "failed"
        assert job.result is None
        assert timeouts == []
        assert fetch_item.await_count == (1 if state == "mapping_changed" else 0)
        return
    assert job.status == "succeeded", job.error
    assert job.result["publication_id"]
    fetch_item.assert_awaited_once()
    assert timeouts and set(timeouts) == {wikidata_studio.STUDIO_JOB_FRESHNESS_TIMEOUT_SECONDS}


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
    item["validation_issues"] = [{"code": "STALE_CATALOGUE_EDIT", "severity": "warning"}]
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
    assert job.params["_gila_checked_manuscripts"] == [{
        "control_number": "990000000000000001",
        "qid": "Q42",
        "revision": 22,
        "disposition": "verified",
    }]
    assert any(case["case_id"] == "model:manuscript-and-work" for case in worker.gila_cases(job))
    stale_edit = next(
        case for case in worker.gila_cases(job)
        if case["case_id"] == "stale-catalogue-edit:manuscript:one"
    )
    assert stale_edit["points"] == [6]
    assert stale_edit["disposition"] == "manual_review"
    assert stale_edit["can_prepare"] is False
    assert job.result["email_points"][5]["counts"]["manual_review"] >= 1
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
    job.result = {
        "case_count": 4,
        "email_points": [{"point": 1, "status": "manual_review"}],
        "all_requirements_verified": False,
        "requirement_coverage": {"unchecked_passages": 3},
    }
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
        {"case_id": "d", "disposition": "repair_required", "points": [8], "can_prepare": True},
    ]
    job.params = {"_gila_cases": b64encode(zlib.compress(json.dumps(cases).encode())).decode()}
    db_session.add(RunJob(
        project_id=sample_run["project_id"],
        run_id=run_id,
        created_by=user.id,
        kind="wikidata_reference_repair",
        status="succeeded",
        params={
            "reference_scope": "remaining",
            "_reference_checkpoint": {"results": [
                {"qid": "Q6", "status": "verified", "corrected": True, "after_revision": 24},
                {"qid": "Q7", "status": "outcome_unknown", "corrected": False},
            ]},
        },
    ))
    await db_session.commit()
    status = await client.get(base)
    assert status.json()["report"]["case_count"] == 4
    assert "_gila_cases" not in status.text
    response = await client.get(
        base + "/cases",
        params={"audit_id": str(job.id), "disposition": "manual_review", "query": "Q2", "limit": 1},
    )
    assert response.json() == {"items": [cases[1]], "total": 2}
    ready = await client.get(
        base + "/cases", params={"audit_id": str(job.id), "disposition": "ready_to_prepare"},
    )
    assert ready.json() == {"items": [cases[3]], "total": 1}
    result = await client.get(base + "/evidence", params={"audit_id": str(job.id)})
    assert result.status_code == 200
    assert result.json()["checked_manuscripts"] is None
    assert result.json()["unchecked_passages"] == 3
    receipts = [row for row in result.json()["reference_repairs"] if row["scope"] == "remaining"]
    assert [row["qid"] for row in receipts] == ["Q6"]
    assert receipts[0]["after_revision"] == 24
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
    forbidden = await client.get(base + "/evidence", params={"audit_id": str(job.id)})
    assert forbidden.status_code == 403
