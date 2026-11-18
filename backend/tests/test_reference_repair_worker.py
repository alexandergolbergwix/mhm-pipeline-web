from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.db import session_scope
from app.models.run_job import RunJob
from app.models.user import User
from app.pipeline import wikidata_reference_repair_job as worker


@pytest.mark.asyncio
async def test_backend_worker_restores_the_plan_and_persists_before_and_after_the_send(
    sample_run,
    db_session,
    monkeypatch,
):
    user = await db_session.get(User, sample_run["user_id"])
    user.role = "admin"
    plan = {
        "authorized_qids": ["Q123"],
        "actor_user_id": str(user.id),
        "run_id": str(sample_run["run_id"]),
        "before_entities": {"Q123": {"lastrevid": 1}},
    }
    seed = {
        "manifest": {
            "run_id": str(sample_run["run_id"]),
            "actor_user_id": str(user.id),
            "qids": ["Q123"],
        },
        "build_job_id": str(sample_run["run_id"]),
        "checkpoint": {"results": [], "current_plan": plan},
    }
    job = RunJob(
        project_id=sample_run["project_id"],
        run_id=sample_run["run_id"],
        created_by=user.id,
        kind=worker.KIND,
        status="running",
        params={"scope_digest": "digest", "_reference_checkpoint": seed["checkpoint"]},
    )
    db_session.add(job)
    await db_session.commit()
    monkeypatch.setattr(
        worker, "get_settings", lambda: SimpleNamespace(gila_reference_cloud_enabled=True)
    )
    monkeypatch.setattr(worker, "load_seed", lambda: (seed, "digest"))
    finish = AsyncMock()
    monkeypatch.setattr(worker, "finish_job", finish)
    updates = AsyncMock()
    monkeypatch.setattr(worker, "update_job_progress", updates)

    async def mirror_scan(remaining):
        assert remaining == ["Q123"]
        yield {"total": 1, "reviewed": 1, "candidates": 1, "seen_clean": 0, "unchecked": 0}

    monkeypatch.setattr(worker, "scan_reference_mirror", mirror_scan)

    async def run(args, qids, **options):
        import json

        assert qids == ["Q123"]
        assert json.loads((args.state_dir / "plan-Q123.json").read_text()) == plan
        async with session_scope() as db:
            await options["authorize_session"](db, user.id)
        await options["on_checkpoint"]("Q123", [], args.state_dir)
        async with session_scope() as db:
            saved = await db.get(RunJob, job.id)
            assert saved.params["_reference_checkpoint"]["current_plan"] == plan
        import asyncio

        options["on_event"](
            {
                "qid": "Q123",
                "status": "service_delay",
                "ordinal": 999,
                "next_attempt_at": "2026-11-17T18:00:00Z",
                "lag_probe_status": "delayed",
                "next_lag_probe_at": "2026-11-17T18:01:00Z",
            }
        )
        await asyncio.sleep(1.05)
        result = {"qid": "Q123", "status": "verified", "corrected": True}
        await options["on_checkpoint"]("Q123", [result], args.state_dir)
        return {"status": "complete", "results": [result]}

    monkeypatch.setattr("scripts.run_gila_reference_queue.run", run)
    await worker.run_reference_job(job.id)
    finish.assert_awaited_once()
    assert finish.call_args.kwargs["status"] == "succeeded"
    assert finish.call_args.kwargs["result"]["verified"] == 1
    assert finish.call_args.kwargs["progress"]["processed"] == 1
    assert finish.call_args.kwargs["progress"]["verified"] == 1
    delayed = [
        call.args[1]
        for call in updates.call_args_list
        if call.args[1].get("phase") == "service_delay"
    ]
    assert delayed
    assert delayed[0]["processed"] == 0
    assert delayed[0]["verified"] == 0
    assert delayed[0]["lag_probe_status"] == "delayed"
    assert delayed[0]["mirror_scan"]["candidates"] == 1
    await db_session.refresh(job)
    assert job.params["_reference_checkpoint"]["current_plan"] is None
    assert job.params["_reference_checkpoint"]["results"][0]["corrected"] is True


@pytest.mark.asyncio
async def test_revoked_admin_cannot_start_a_backend_writer(sample_run, db_session, monkeypatch):
    user = await db_session.get(User, sample_run["user_id"])
    seed = {
        "manifest": {
            "run_id": str(sample_run["run_id"]),
            "actor_user_id": str(user.id),
            "qids": ["Q123"],
        },
        "checkpoint": {"results": []},
    }
    job = RunJob(
        project_id=sample_run["project_id"],
        run_id=sample_run["run_id"],
        created_by=user.id,
        kind=worker.KIND,
        status="running",
        params={"scope_digest": "digest"},
    )
    db_session.add(job)
    await db_session.commit()
    monkeypatch.setattr(
        worker, "get_settings", lambda: SimpleNamespace(gila_reference_cloud_enabled=True)
    )
    monkeypatch.setattr(worker, "load_seed", lambda: (seed, "digest"))
    run = AsyncMock()
    finish = AsyncMock()
    monkeypatch.setattr("scripts.run_gila_reference_queue.run", run)
    monkeypatch.setattr(worker, "finish_job", finish)
    await worker.run_reference_job(job.id)
    run.assert_not_awaited()
    assert finish.call_args.kwargs["status"] == "failed"
