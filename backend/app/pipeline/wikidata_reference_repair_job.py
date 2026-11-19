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

import httpx
from sqlalchemy import select

from app.db import session_scope
from app.models.publication import (
    Publication,
    PublicationExecution,
    PublicationWriteIntent,
    PublicationWriteReceipt,
)
from app.models.run_job import RunJob
from app.models.user import ROLE_ADMIN, User
from app.pipeline.run_job_service import finish_job, is_cancel_requested, update_job_progress
from app.pipeline.wikidata_foreground_repair import PROFILE
from app.pipeline.wikidata_gila_execution import expected_entity, semantic_entity
from app.pipeline.wikidata_gila_repairs import entity_digest, validate_gila_repair_proposal
from app.pipeline.wikidata_reference_reader import ReferenceItemReader
from app.publication.reference_recovery import probe_reference_lag, scan_reference_mirror
from app.publication.reference_repair_queue import reference_source_controls
from app.settings import get_settings

logger = logging.getLogger(__name__)

KIND = "wikidata_reference_repair"
REFERENCE_JOB_MAXLAG = 20
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


class ReferenceCheckpointUnverifiedError(ValueError):
    """The queue has no proof that resolves its blocked item."""


async def recover_blocked_checkpoint(checkpoint, manifest, project_id):
    """Use a verified interactive repair to advance a blocked item without an edit."""
    results = checkpoint.get("results", [])
    qids = manifest["qids"]
    plan = checkpoint.get("current_plan") or {}
    if (
        len(results) >= len(qids)
        or [row.get("qid") for row in results] != qids[: len(results)]
        or plan.get("execution_mode") is not None
        or plan.get("run_id") != manifest["run_id"]
        or plan.get("project_id") != str(project_id)
        or plan.get("actor_user_id") != manifest["actor_user_id"]
    ):
        raise ValueError("The blocked checkpoint has another scope.")
    qid = qids[len(results)]
    proposals = plan.get("proposals", [])
    if (
        plan.get("authorized_qids") != [qid]
        or len(proposals) != 1
        or proposals[0].get("qid") != qid
    ):
        raise ValueError("The blocked plan does not match the next item.")
    proposal = proposals[0]
    before = plan.get("before_entities", {}).get(qid)
    if not isinstance(before, dict):
        raise ValueError("The blocked plan has no public snapshot.")
    validate_gila_repair_proposal(proposal, before)
    reference_source_controls(proposal)
    current = await ReferenceItemReader().fetch(qid)
    if (
        current["lastrevid"] <= before["lastrevid"]
        or semantic_entity(current) != semantic_entity(expected_entity(proposal, before))
    ):
        raise ReferenceCheckpointUnverifiedError()

    intent = PublicationWriteIntent
    receipt = PublicationWriteReceipt
    async with session_scope() as db:
        unresolved = await db.scalar(
            select(intent.id).where(
                intent.target_qid == qid,
                intent.state.in_(["pending", "in_flight", "outcome_unknown"]),
            ).limit(1)
        )
        fingerprint = entity_digest(current)
        verified = await db.scalar(
            select(receipt.id)
            .join(intent, receipt.intent_id == intent.id)
            .join(PublicationExecution, intent.execution_id == PublicationExecution.id)
            .join(Publication, PublicationExecution.publication_id == Publication.id)
            .where(
                Publication.run_id == uuid.UUID(manifest["run_id"]),
                Publication.profile_name == PROFILE,
                Publication.target_site == "www.wikidata.org",
                Publication.target_environment == "production",
                PublicationExecution.actor_id == manifest["actor_user_id"],
                intent.entity_key == f"repair:{qid}",
                intent.action == "update",
                intent.target_qid == qid,
                intent.state == "succeeded",
                receipt.status == "succeeded",
                receipt.qid == qid,
                receipt.fingerprint == fingerprint,
            ).limit(1)
        )
        await db.rollback()
    if unresolved is not None or verified is None:
        raise ReferenceCheckpointUnverifiedError()

    return {
        "results": [
            *results,
            {
                "qid": qid,
                "status": "verified",
                "corrected": True,
                "before_revision": before["lastrevid"],
                "after_revision": current["lastrevid"],
            },
        ],
        "current_plan": None,
        "halted_status": None,
    }


async def run_reference_job(job_id: uuid.UUID) -> None:
    from scripts.run_gila_reference_queue import run

    seed, digest = load_seed()
    manifest = seed["manifest"]
    actor = uuid.UUID(manifest["actor_user_id"])
    runner = None
    scan = None
    client = None
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
            project_id = job.project_id
            await db.rollback()

        if checkpoint.get("halted_status") == "blocked":
            checkpoint = await recover_blocked_checkpoint(checkpoint, manifest, project_id)
            async with session_scope() as db:
                job = await db.get(RunJob, job_id, with_for_update=True)
                await assert_admin_job(db, job_id, actor)
                job.params = {**job.params, "_reference_checkpoint": checkpoint}
                await db.commit()
        elif checkpoint.get("halted_status") not in {None, "pre_send_retryable"}:
            raise ReferenceCheckpointUnverifiedError()

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
        scan_progress = {}
        scan_changed = False

        async def inspect_mirror():
            nonlocal scan_changed
            try:
                remaining = manifest["qids"][len(checkpoint.get("results", [])) :]
                async for progress in scan_reference_mirror(remaining):
                    scan_progress.update(status="scanning", **progress)
                    scan_changed = True
                scan_progress["status"] = "complete"
            except Exception:
                logger.exception("The reference mirror scan did not finish.")
                scan_progress["status"] = "unavailable"
            scan_changed = True

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
            client = httpx.AsyncClient(
                headers={
                    "User-Agent": "MHMPipeline/1.0 (https://mhm-pipeline.org; reference lag check)"
                },
                timeout=8.0,
            )

            async def check_lag():
                if await is_cancel_requested(job_id):
                    raise ReferenceJobCancelledError()
                return await probe_reference_lag(client, maxlag=REFERENCE_JOB_MAXLAG)

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
                    lag_probe=check_lag,
                    maxlag=REFERENCE_JOB_MAXLAG,
                )
            )
            scan = asyncio.create_task(inspect_mirror())
            saved = {"status": "start"}
            while not runner.done():
                await asyncio.wait({runner}, timeout=1)
                if await is_cancel_requested(job_id):
                    runner.cancel()
                    raise ReferenceJobCancelledError()
                if latest or scan_changed:
                    if latest:
                        saved = dict(latest)
                        latest.clear()
                    scan_changed = False
                    await update_job_progress(
                        job_id,
                        {
                            "phase": saved.get("status", "active"),
                            **counts,
                            "total": len(manifest["qids"]),
                            "unit": "items",
                            "current_qid": saved.get("qid"),
                            "next_attempt_at": saved.get("next_attempt_at"),
                            "lag_probe_status": saved.get("lag_probe_status"),
                            "lag_probe_seconds": saved.get("lag_probe_seconds"),
                            "next_lag_probe_at": saved.get("next_lag_probe_at"),
                            "mirror_scan": dict(scan_progress),
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
                    "mirror_scan": dict(scan_progress),
                    "message": "The reviewed queue is complete."
                    if complete
                    else "The queue paused or requires review.",
                },
            )
    except ReferenceJobCancelledError:
        await finish_job(
            job_id, status="cancelled", error="The administrator cancelled this queue."
        )
    except ReferenceCheckpointUnverifiedError:
        await finish_job(
            job_id, status="failed", error="The blocked item has no matching verified repair."
        )
    except Exception as error:
        logger.exception("The backend reference job stopped.")
        await finish_job(
            job_id, status="failed", error=f"Reference queue stopped: {type(error).__name__}"
        )
    finally:
        for task in (runner, scan):
            if task is not None and not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
        if client is not None:
            await client.aclose()
