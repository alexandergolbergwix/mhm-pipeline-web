"""Run the reviewed reference queue on the backend with durable checkpoints."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import tempfile
import uuid
from contextlib import suppress
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import select

from app.db import session_scope
from app.models.run_job import RunJob
from app.models.user import ROLE_ADMIN, User
from app.pipeline.run_job_service import finish_job, is_cancel_requested, update_job_progress
from app.settings import get_settings

logger = logging.getLogger(__name__)

KIND = "wikidata_reference_repair"
SEED_PATH = Path(__file__).resolve().parents[1] / "data/gila_reference_seed.json"


class ReferenceJobCancelledError(Exception):
    """Stop before another public write."""


def load_seed():
    raw = SEED_PATH.read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def checkpoint_for(qid, results, directory):
    completed = []
    for row in results:
        if row.get("status") not in {"verified", "already_clean"}:
            break
        completed.append(row)
    path = directory / f"plan-{qid}.json"
    pending = len(completed) == len(results) and qid not in {r["qid"] for r in completed}
    halted = results[-1].get("status") if len(completed) < len(results) else None
    return {
        "results": completed,
        "current_plan": json.loads(path.read_text())
        if path.exists() and (pending or halted)
        else None,
        "halted_status": halted,
    }


async def assert_admin_job(db, job_id, actor):
    job = await db.get(RunJob, job_id, populate_existing=True)
    user = await db.get(User, actor, populate_existing=True)
    if not user or user.role != ROLE_ADMIN or not job or job.created_by != actor:
        raise ValueError("Current administrator authority is required.")
    if job.status != "running" or job.cancel_requested_at:
        raise ReferenceJobCancelledError()


async def run_reference_job(job_id: uuid.UUID) -> None:
    from scripts.run_gila_reference_queue import run

    seed, digest = load_seed()
    manifest = seed["manifest"]
    actor = uuid.UUID(manifest["actor_user_id"])
    runner = None
    try:
        if not get_settings().gila_reference_cloud_enabled:
            raise ValueError("The backend reference worker is disabled.")
        async with session_scope() as db:
            job = await db.get(RunJob, job_id)
            if (
                not job
                or job.kind != KIND
                or job.params.get("scope_digest") != digest
                or str(job.run_id) != manifest["run_id"]
                or job.created_by != actor
            ):
                raise ValueError("The reference job does not match the reviewed scope.")
            await assert_admin_job(db, job_id, actor)
            checkpoint = job.params.get("_reference_checkpoint", seed["checkpoint"])
            await db.rollback()

        async def authorize(db, current_actor):
            if not get_settings().gila_reference_cloud_enabled:
                raise ValueError("The backend reference worker is disabled.")
            if current_actor != actor:
                raise ValueError("The build actor differs from the reviewed administrator.")
            await assert_admin_job(db, job_id, actor)

        async def before_item(_qid):
            if await is_cancel_requested(job_id):
                raise ReferenceJobCancelledError()

        latest = {}
        counts = {
            "processed": len(checkpoint.get("results", [])),
            "verified": sum(row.get("corrected") is True for row in checkpoint.get("results", [])),
            "already_clean": sum(
                row.get("status") == "already_clean" for row in checkpoint.get("results", [])
            ),
        }

        def event(value):
            latest.clear()
            latest.update(value)

        async def persist(qid, results, directory):
            snapshot = checkpoint_for(qid, results, directory)
            async with session_scope() as db:
                row = (
                    await db.execute(select(RunJob).where(RunJob.id == job_id).with_for_update())
                ).scalar_one()
                await assert_admin_job(db, job_id, actor)
                row.params = {**row.params, "_reference_checkpoint": snapshot}
                await db.commit()
            counts.update(
                processed=len(snapshot["results"]),
                verified=sum(row.get("corrected") is True for row in snapshot["results"]),
                already_clean=sum(
                    row.get("status") == "already_clean" for row in snapshot["results"]
                ),
            )

        await update_job_progress(
            job_id,
            {
                **counts,
                "phase": "start",
                "total": len(manifest["qids"]),
                "unit": "items",
                "message": "Check the next reviewed item.",
            },
        )

        with tempfile.TemporaryDirectory(prefix=f"reference-job-{job_id}-") as temporary:
            directory = Path(temporary)
            plan = checkpoint.get("current_plan")
            if plan:
                if (
                    plan.get("actor_user_id") != str(actor)
                    or plan.get("run_id") != manifest["run_id"]
                    or len(plan.get("authorized_qids", [])) != 1
                ):
                    raise ValueError("The saved reference plan has another scope.")
                qid = plan["authorized_qids"][0]
                if qid not in manifest["qids"]:
                    raise ValueError("The saved item is outside the reviewed scope.")
                (directory / f"plan-{qid}.json").write_text(json.dumps(plan))
            args = SimpleNamespace(
                run_id=uuid.UUID(manifest["run_id"]),
                build_job_id=uuid.UUID(seed["build_job_id"]),
                state_dir=directory,
                apply=True,
                wait_seconds=86400,
            )
            runner = asyncio.create_task(
                run(
                    args,
                    manifest["qids"],
                    before_item_hook=before_item,
                    on_checkpoint=persist,
                    resume_results=checkpoint.get("results", []),
                    on_event=event,
                    dispose_engine=False,
                    authorize_session=authorize,
                )
            )
            while not runner.done():
                await asyncio.wait({runner}, timeout=1)
                if await is_cancel_requested(job_id):
                    runner.cancel()
                    raise ReferenceJobCancelledError()
                if latest:
                    saved = dict(latest)
                    latest.clear()
                    await update_job_progress(
                        job_id,
                        {
                            "phase": saved.get("status", "active"),
                            **counts,
                            "total": len(manifest["qids"]),
                            "unit": "items",
                            "current_qid": saved.get("qid"),
                            "next_attempt_at": saved.get("next_attempt_at"),
                            "message": (
                                "Wait for the saved Wikidata retry time."
                                if saved.get("status") in {"service_delay", "retry_wait"}
                                else "Check the current reference repair."
                            ),
                        },
                    )
            result = await runner
            complete = result["status"] == "complete"
            await finish_job(
                job_id,
                status="succeeded" if complete else "failed",
                result={
                    "checkpoint_status": result["status"],
                    "verified": sum(r.get("corrected") is True for r in result["results"]),
                    "already_clean": sum(
                        r.get("status") == "already_clean" for r in result["results"]
                    ),
                    "total": len(manifest["qids"]),
                },
                error=None if complete else "The reference queue paused or requires review.",
                progress={
                    **counts,
                    "total": len(manifest["qids"]),
                    "unit": "items",
                    "phase": "succeeded" if complete else result["status"],
                    "current_qid": (result.get("current") or {}).get("qid"),
                    "next_attempt_at": (result.get("current") or {}).get("next_attempt_at"),
                    "message": "The reviewed queue is complete."
                    if complete
                    else "The queue paused or requires review.",
                },
            )
    except ReferenceJobCancelledError:
        await finish_job(
            job_id, status="cancelled", error="The administrator cancelled this queue."
        )
    except Exception as error:
        logger.exception("The backend reference job stopped.")
        await finish_job(
            job_id, status="failed", error=f"Reference queue stopped: {type(error).__name__}"
        )
    finally:
        if runner is not None and not runner.done():
            runner.cancel()
            with suppress(asyncio.CancelledError):
                await runner
