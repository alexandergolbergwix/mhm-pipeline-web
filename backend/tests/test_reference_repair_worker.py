import uuid
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

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
    lag_probe = AsyncMock()
    monkeypatch.setattr(worker, "probe_reference_lag", lag_probe)

    async def run(args, qids, **options):
        import json

        assert qids == ["Q123"]
        assert options["maxlag"] == 20
        await options["lag_probe"]()
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
                "lag_probe_seconds": 20.97,
                "next_lag_probe_at": "2026-11-17T18:01:00Z",
            }
        )
        await asyncio.sleep(1.05)
        result = {"qid": "Q123", "status": "verified", "corrected": True}
        await options["on_checkpoint"]("Q123", [result], args.state_dir)
        async with session_scope() as db:
            saved = await db.get(RunJob, job.id)
            timestamp = saved.params["_reference_checkpoint"]["results"][0]["completed_at"]
            timing_started = saved.params["_reference_timing_started_at"]
            assert datetime.fromisoformat(timing_started) <= datetime.fromisoformat(timestamp)
        await options["on_checkpoint"]("Q123", [result], args.state_dir)
        async with session_scope() as db:
            saved = await db.get(RunJob, job.id)
            assert saved.params["_reference_checkpoint"]["results"][0]["completed_at"] == timestamp
        return {"status": "complete", "results": [result]}

    monkeypatch.setattr("scripts.run_gila_reference_queue.run", run)
    await worker.run_reference_job(job.id)
    finish.assert_awaited_once()
    lag_probe.assert_awaited_once()
    assert lag_probe.await_args.kwargs == {"maxlag": 20}
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
    assert delayed[0]["lag_probe_seconds"] == 20.97
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


@pytest.mark.asyncio
@pytest.mark.parametrize("proof", ["verified", "missing", "changed", "unresolved"])
async def test_worker_recovers_only_with_the_exact_public_result_and_receipt(
    sample_run, db_session, monkeypatch, proof,
):
    from app.models.publication import PublicationWriteIntent
    from app.pipeline.wikidata_foreground_repair import MODE, ForegroundReferenceGateway
    from app.pipeline.wikidata_gila_repairs import build_gila_repair_proposals, entity_digest
    from app.publication.historical_repairs import advance_repair_publication
    from app.publication.sql_repository import SqlAlchemyPublicationRepository
    from tests.unit.test_reference_repair_queue import Gateway, source_check

    user = await db_session.get(User, sample_run["user_id"])
    user.role = "admin"
    target = Gateway()
    qid = "Q123"
    before = await target.fetch(qid)
    proposal, = build_gila_repair_proposals({qid: before})
    plan = {
        "authorized_qids": [qid], "actor_user_id": str(user.id),
        "run_id": str(sample_run["run_id"]), "project_id": str(sample_run["project_id"]),
        "proposals": [proposal], "before_entities": {qid: before},
        "preflight": [{"qid": qid, "source_evidence": await source_check(before, proposal)}],
    }
    manifest = {"run_id": plan["run_id"], "actor_user_id": str(user.id), "qids": [qid, "Q456"]}
    checkpoint = {"results": [], "current_plan": plan, "halted_status": "blocked"}
    job = RunJob(
        project_id=sample_run["project_id"], run_id=sample_run["run_id"], created_by=user.id,
        kind=worker.KIND, status="running",
        params={"scope_digest": "digest", "_reference_checkpoint": checkpoint},
    )
    db_session.add(job)
    await db_session.commit()

    if proof == "missing":
        await target.write(qid, 10, [])
    else:
        foreground_plan = {**plan, "execution_mode": MODE}
        foreground = ForegroundReferenceGateway(
            None, plan=foreground_plan, confirmed_qid=qid,
            confirmed_proposal_sha256=entity_digest(proposal),
        )
        monkeypatch.setattr(foreground, "assert_allowed", target.assert_allowed)
        monkeypatch.setattr(foreground, "fetch", target.fetch)
        monkeypatch.setattr(foreground, "write", target.write)
        async with session_scope() as db:
            repository = SqlAlchemyPublicationRepository(db)
            result = await advance_repair_publication(
                foreground_plan, foreground, source_check, repository,
                actor_id=manifest["actor_user_id"], run_id=manifest["run_id"],
            )
            await repository.checkpoint()
        assert result.execution_status == "succeeded"
        if proof == "changed":
            target.entities[qid]["lastrevid"] += 1
        if proof == "unresolved":
            original = await db_session.scalar(select(PublicationWriteIntent).where(
                PublicationWriteIntent.target_qid == qid,
            ))
            db_session.add(PublicationWriteIntent(
                id=uuid.uuid4(), execution_id=original.execution_id,
                entity_key=original.entity_key, action="update", target_qid=qid,
                mutation_digest=original.mutation_digest, request_key="unresolved-test-write",
                attempt=original.attempt + 1, state="outcome_unknown",
            ))
            await db_session.commit()

    seed = {"manifest": manifest, "build_job_id": plan["run_id"], "checkpoint": checkpoint}
    monkeypatch.setattr(
        worker, "get_settings", lambda: SimpleNamespace(gila_reference_cloud_enabled=True)
    )
    monkeypatch.setattr(worker, "load_seed", lambda: (seed, "digest"))
    monkeypatch.setattr(worker, "ReferenceItemReader", lambda: target)
    finish = AsyncMock()
    monkeypatch.setattr(worker, "finish_job", finish)
    monkeypatch.setattr(worker, "update_job_progress", AsyncMock())

    async def mirror_scan(remaining):
        assert remaining == ["Q456"]
        yield {"total": 1, "reviewed": 1}

    async def run(args, qids, **options):
        assert qids == [qid, "Q456"]
        assert not (args.state_dir / f"plan-{qid}.json").exists()
        result, = options["resume_results"]
        assert {key: value for key, value in result.items() if key != "completed_at"} == {
            "qid": qid, "status": "verified", "corrected": True,
            "before_revision": 10, "after_revision": 11,
        }
        assert datetime.fromisoformat(result["completed_at"]).tzinfo is not None
        async with session_scope() as db:
            saved = await db.get(RunJob, job.id)
            assert saved.params["_reference_checkpoint"]["current_plan"] is None
            assert saved.params["_reference_checkpoint"]["halted_status"] is None
        return {"status": "paused", "results": options["resume_results"]}

    continuation = AsyncMock(side_effect=run)
    monkeypatch.setattr(worker, "scan_reference_mirror", mirror_scan)
    monkeypatch.setattr("scripts.run_gila_reference_queue.run", continuation)
    await worker.run_reference_job(job.id)
    assert target.writes == [qid]
    await db_session.refresh(job)
    if proof == "verified":
        continuation.assert_awaited_once()
        assert job.params["_reference_checkpoint"]["results"][0]["after_revision"] == 11
    else:
        continuation.assert_not_awaited()
        assert job.params["_reference_checkpoint"] == checkpoint
        assert finish.call_args.kwargs["error"] == (
            "The blocked item has no matching verified repair."
        )
