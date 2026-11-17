import json
import uuid
from unittest.mock import Mock

import pytest

from app.models.run_job import RunJob
from app.models.user import User
from app.routers import admin_reference_repairs as router


async def reviewed_scope(sample_run, db_session, monkeypatch):
    user = await db_session.get(User, sample_run["user_id"])
    user.role = "admin"
    await db_session.commit()
    seed = {
        "manifest": {
            "run_id": str(sample_run["run_id"]),
            "actor_user_id": str(user.id),
            "qids": ["Q123"],
        },
        "checkpoint": {"results": [], "current_plan": None},
    }
    monkeypatch.setattr(router, "load_seed", lambda: (seed, "a" * 64))
    monkeypatch.setattr(
        router,
        "get_settings",
        lambda: type(
            "Settings",
            (),
            {
                "gila_reference_cloud_enabled": True,
            },
        )(),
    )
    spawn = Mock()
    monkeypatch.setattr("app.pipeline.run_job_service.spawn_job", spawn)
    return user, spawn


@pytest.mark.asyncio
async def test_live_status_counts_confirmed_results_and_limits_public_history(
    sample_run,
    db_session,
    monkeypatch,
):
    user, _ = await reviewed_scope(sample_run, db_session, monkeypatch)
    router.load_seed()[0]["manifest"]["qids"] = [f"Q{i}" for i in range(1, 8)]
    results = [
        {
            "qid": f"Q{i}",
            "status": "verified",
            "corrected": True,
            "after_revision": i,
            "private_plan": "excluded",
        }
        for i in range(1, 7)
    ]
    results.append({"qid": "Q7", "status": "already_clean", "corrected": False})
    job = RunJob(
        project_id=sample_run["project_id"],
        run_id=sample_run["run_id"],
        created_by=user.id,
        kind="wikidata_reference_repair",
        status="running",
        params={"_reference_checkpoint": {"results": results}},
    )
    db_session.add(job)
    await db_session.commit()
    response = await sample_run["client"].get("/api/admin/wikidata-reference-repairs")
    assert response.status_code == 200
    body = response.json()
    assert body["verified"] == 6
    assert body["already_clean"] == 1
    assert body["completed"] == 7
    assert len(body["recent_results"]) == 5
    assert body["recent_results"][0]["qid"] == "Q3"
    assert body["project_id"] == str(sample_run["project_id"])
    assert body["observed_at"].endswith("+00:00")
    assert "private_plan" not in response.text


@pytest.mark.asyncio
async def test_admin_start_uses_durable_job_and_does_not_launch_a_second_one(
    sample_run,
    db_session,
    monkeypatch,
):
    user, spawn = await reviewed_scope(sample_run, db_session, monkeypatch)
    client = sample_run["client"]
    path = "/api/admin/wikidata-reference-repairs"
    response = await client.post(
        path + "/start", json={"confirmed": True, "scope_digest": "a" * 64}
    )
    assert response.status_code == 200, response.text
    job = response.json()
    assert job["kind"] == "wikidata_reference_repair"
    assert job["created_by"] == str(user.id)
    assert "_reference_checkpoint" not in job["params"]
    again = await client.post(path + "/start", json={"confirmed": True, "scope_digest": "a" * 64})
    assert again.status_code == 200
    assert again.json()["id"] == job["id"]
    spawn.assert_called_once()
    status = await client.get(path)
    assert status.status_code == 200
    assert status.json()["job"]["id"] == job["id"]


@pytest.mark.asyncio
async def test_nonadmins_cannot_start_read_cancel_or_use_generic_job_path(
    sample_run,
    db_session,
    monkeypatch,
):
    user, _ = await reviewed_scope(sample_run, db_session, monkeypatch)
    job = RunJob(
        project_id=sample_run["project_id"],
        run_id=sample_run["run_id"],
        created_by=user.id,
        kind="wikidata_reference_repair",
        status="queued",
        params={},
    )
    db_session.add(job)
    await db_session.commit()
    user.role = "editor"
    await db_session.commit()
    client = sample_run["client"]
    prefix = f"/api/runs/{sample_run['run_id']}"
    assert (await client.get("/api/admin/wikidata-reference-repairs")).status_code == 403
    assert (
        await client.post(
            "/api/admin/wikidata-reference-repairs/start",
            json={
                "confirmed": True,
                "scope_digest": "a" * 64,
            },
        )
    ).status_code == 403
    assert (await client.get(prefix + "/wikidata-studio/repair-plan")).status_code == 403
    assert (await client.get(prefix + f"/jobs/{job.id}")).status_code == 403
    assert (await client.post(prefix + f"/jobs/{job.id}/cancel")).status_code == 403
    assert (await client.get(prefix + "/jobs")).json()["jobs"] == []
    assert (
        await client.post(
            prefix + "/jobs",
            json={
                "kind": "wikidata_reference_repair",
                "params": {},
            },
        )
    ).status_code == 403


@pytest.mark.asyncio
async def test_admin_requires_exact_scope_consent_and_original_actor(
    sample_run, db_session, monkeypatch
):
    _, spawn = await reviewed_scope(sample_run, db_session, monkeypatch)
    path = "/api/admin/wikidata-reference-repairs/start"
    client = sample_run["client"]
    for body in [
        {"confirmed": False, "scope_digest": "a" * 64},
        {"confirmed": True, "scope_digest": "b" * 64},
    ]:
        assert (await client.post(path, json=body)).status_code == 409
    assert (
        await client.post(
            path,
            json={
                "confirmed": True,
                "scope_digest": "a" * 64,
                "command": "anything",
            },
        )
    ).status_code == 422
    spawn.assert_not_called()


@pytest.mark.asyncio
async def test_private_checkpoint_survives_resume_and_unknown_outcome_refuses_it(
    sample_run,
    db_session,
    monkeypatch,
):
    user, _ = await reviewed_scope(sample_run, db_session, monkeypatch)
    checkpoint = {
        "results": [],
        "current_plan": {"preserved": True},
        "halted_status": "pre_send_retryable",
    }
    prior = RunJob(
        project_id=sample_run["project_id"],
        run_id=sample_run["run_id"],
        created_by=user.id,
        kind="wikidata_reference_repair",
        status="failed",
        params={"scope_digest": "a" * 64, "_reference_checkpoint": checkpoint},
    )
    db_session.add(prior)
    await db_session.commit()
    response = await sample_run["client"].post(
        "/api/admin/wikidata-reference-repairs/start",
        json={
            "confirmed": True,
            "scope_digest": "a" * 64,
        },
    )
    assert response.status_code == 200
    created = await db_session.get(RunJob, uuid.UUID(response.json()["id"]))
    assert json.dumps(created.params["_reference_checkpoint"], sort_keys=True) == json.dumps(
        checkpoint, sort_keys=True
    )
