"""Background Wikidata upload / dry-run job."""

from __future__ import annotations

import logging
import uuid
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from app.db import session_scope
from app.models.run_job import (
    JOB_STATUS_CANCELLED,
    JOB_STATUS_FAILED,
    JOB_STATUS_SUCCEEDED,
    RunJob,
)
from app.models.item_override import WikidataItemOverride
from app.models.wikibase_cloud_write import CHANNEL_WIKIDATA_UPLOAD
from app.pipeline import wikidata_studio, wikidata_upload
from app.pipeline.run_job_service import (
    finish_job,
    is_cancel_requested,
    update_job_progress,
)
from app.services.wikibase_audit import WikibaseAuditContext

logger = logging.getLogger(__name__)


async def run_wikidata_upload_job(job_id: uuid.UUID) -> None:
    async with session_scope() as db:
        job = await db.get(RunJob, job_id)
        if job is None:
            return
        run_id = job.run_id
        project_id = job.project_id
        params = job.params or {}
        mode = wikidata_upload.resolve_upload_mode(
            params.get("upload_target"),
            dry_run=params.get("dry_run"),
        )
        approved_only = bool(params.get("approved_only", True))
        source = str(params.get("source") or "canonical")
        item_approved_only = bool(params.get("item_approved_only", False))
        token = str(params.get("_wikidata_token") or "")
        auth = SimpleNamespace(user=SimpleNamespace(id=job.created_by))

        from app.routers.wikidata_studio import _build_native_items  # noqa: PLC0415

        native = await _build_native_items(
            db, run_id, auth, approved_only=approved_only, source=source,
        )
        if item_approved_only:
            override_rows = (
                await db.execute(
                    select(WikidataItemOverride).where(WikidataItemOverride.run_id == run_id)
                )
            ).scalars().all()
            approved_ids = {r.local_id for r in override_rows if r.approved}
            native = [
                it for it in native
                if wikidata_studio.local_id_for_item(it) in approved_ids
            ]
        ledger = await wikidata_upload.load_ledger_for_prepare(
            db, is_test=mode.is_test,
        )

    total = len(native)
    label = {
        wikidata_upload.UPLOAD_TARGET_DRY_RUN: "Dry-run",
        wikidata_upload.UPLOAD_TARGET_TEST: "Test upload",
        wikidata_upload.UPLOAD_TARGET_LIVE: "Live upload",
    }.get(mode.target, "Upload")
    await update_job_progress(job_id, {
        "phase": "uploading",
        "processed": 0,
        "total": total,
        "message": f"{label}: {total} items…",
        "upload_target": mode.target,
    })

    if await is_cancel_requested(job_id):
        await finish_job(job_id, status=JOB_STATUS_CANCELLED)
        return

    if not mode.dry_run and not token:
        await finish_job(job_id, status=JOB_STATUS_FAILED, error="missing Wikidata token")
        return

    # One MediaWiki login for the whole job (Rule W-179). Creating a new
    # WikidataUploader per item burned login rate limits and turned one bad
    # password into hundreds of "too many recent login attempts" failures.
    shared_uploader: Any | None = None
    if token:
        from converter.wikidata.uploader import WikidataUploader  # noqa: PLC0415

        shared_uploader = WikidataUploader(
            token=token,
            is_test=mode.is_test,
            batch_mode=True,
            allow_live=mode.allow_live,
        )
        if not mode.dry_run:
            try:
                await run_in_threadpool(shared_uploader.ensure_authenticated)
            except Exception as exc:  # noqa: BLE001
                logger.exception("wikidata upload login failed for %s", run_id)
                await finish_job(
                    job_id,
                    status=JOB_STATUS_FAILED,
                    error=f"Wikidata login failed before any writes: {exc}",
                )
                return

    outcomes: list[Any] = []
    audit_ctx = None if mode.dry_run else WikibaseAuditContext(
        actor_user_id=job.created_by,
        channel=CHANNEL_WIKIDATA_UPLOAD,
        project_id=project_id,
        run_id=run_id,
        job_id=job_id,
    )
    try:
        for idx, item in enumerate(native):
            if await is_cancel_requested(job_id):
                await finish_job(
                    job_id,
                    status=JOB_STATUS_CANCELLED,
                    result={"outcomes": [o.__dict__ for o in outcomes], "processed": idx},
                    progress={
                        "phase": "cancelled",
                        "processed": idx,
                        "total": total,
                        "message": "Cancelled by user",
                    },
                )
                return
            async with session_scope() as db:
                batch_outcomes = await wikidata_upload.upload_items(
                    [item], token=token or "", mode=mode,
                    audit_ctx=audit_ctx, db=db, ledger=ledger,
                    run_id=run_id,
                    uploader=shared_uploader,
                )
            outcomes.extend(batch_outcomes)
            item_outcome = None
            if batch_outcomes:
                last = batch_outcomes[-1]
                item_outcome = {
                    "local_id": last.local_id,
                    "status": last.status,
                    "qid": last.qid,
                    "wikibase_id": last.qid,
                    "message": last.message,
                }
                if (
                    last.status == "failed"
                    and wikidata_upload._is_auth_failure_message(last.message)
                ):
                    await finish_job(
                        job_id,
                        status=JOB_STATUS_FAILED,
                        error=(
                            "Wikidata authentication failed; aborted remaining "
                            f"items to avoid login rate-limits. {last.message}"
                        ),
                        result={
                            "dry_run": mode.dry_run,
                            "upload_target": mode.target,
                            "moratorium_lifted": mode.moratorium_lifted,
                            "test_mode": mode.test_mode,
                            "outcomes": [o.__dict__ for o in outcomes],
                            "aborted_auth": True,
                        },
                        progress={
                            "phase": "failed",
                            "processed": idx + 1,
                            "total": total,
                            "message": "Aborted: Wikidata login failure",
                            "upload_target": mode.target,
                        },
                    )
                    return
            progress: dict = {
                "phase": "uploading",
                "processed": idx + 1,
                "total": total,
                "message": f"Item {idx + 1} / {total}",
                "upload_target": mode.target,
            }
            if item_outcome is not None:
                recent = [
                    {
                        "local_id": o.local_id,
                        "status": o.status,
                        "qid": o.qid,
                        "wikibase_id": o.qid,
                        "message": o.message,
                    }
                    for o in outcomes[-200:]
                ]
                progress["item_outcome"] = item_outcome
                progress["recent_item_outcomes"] = recent
            await update_job_progress(job_id, progress)
    except Exception as exc:  # noqa: BLE001
        logger.exception("wikidata upload job failed for %s", run_id)
        await finish_job(job_id, status=JOB_STATUS_FAILED, error=str(exc))
        return

    await finish_job(
        job_id,
        status=JOB_STATUS_SUCCEEDED,
        result={
            "dry_run": mode.dry_run,
            "upload_target": mode.target,
            "moratorium_lifted": mode.moratorium_lifted,
            "test_mode": mode.test_mode,
            "outcomes": [o.__dict__ for o in outcomes],
        },
        progress={
            "phase": "done",
            "processed": total,
            "total": total,
            "message": f"{label} complete",
            "upload_target": mode.target,
        },
    )
