import json
import zlib
from base64 import b64encode
from unittest.mock import Mock

import pytest

from app.models.run_job import RunJob
from app.models.user import User
from app.routers import admin_gila_repairs as router


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
