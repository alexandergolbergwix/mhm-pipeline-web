"""Background Wikidata upload / dry-run job."""

from __future__ import annotations

import logging
import os
import uuid
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select

from app.db import session_scope
from app.models.run_job import (
    JOB_STATUS_CANCELLED,
    JOB_STATUS_FAILED,
    JOB_STATUS_SUCCEEDED,
    RunJob,
)
from app.models.item_override import WikidataItemOverride
from app.pipeline import wikidata_studio, wikidata_upload
from app.pipeline.run_job_service import (
    finish_job,
    is_cancel_requested,
    update_job_progress,
)

logger = logging.getLogger(__name__)


async def run_wikidata_upload_job(job_id: uuid.UUID) -> None:
    async with session_scope() as db:
        job = await db.get(RunJob, job_id)
        if job is None:
            return
        run_id = job.run_id
        params = job.params or {}
        dry_run = bool(params.get("dry_run", True))
        approved_only = bool(params.get("approved_only", True))
        item_approved_only = bool(params.get("item_approved_only", False))
        token = str(params.get("_wikidata_token") or "")
        auth = SimpleNamespace(user=SimpleNamespace(id=job.created_by))

        from app.routers.wikidata_studio import _build_native_items  # noqa: PLC0415

        native = await _build_native_items(
            db, run_id, auth, approved_only=approved_only,
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

    total = len(native)
    await update_job_progress(job_id, {
        "phase": "uploading",
        "processed": 0,
        "total": total,
        "message": f"{'Dry-run' if dry_run else 'Uploading'} {total} items…",
    })

    if await is_cancel_requested(job_id):
        await finish_job(job_id, status=JOB_STATUS_CANCELLED)
        return

    if not dry_run and not token:
        await finish_job(job_id, status=JOB_STATUS_FAILED, error="missing Wikidata token")
        return

    outcomes: list[Any] = []
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
            batch_outcomes = await wikidata_upload.upload_items(
                [item], token=token or "", dry_run=dry_run,
            )
            outcomes.extend(batch_outcomes)
            await update_job_progress(job_id, {
                "phase": "uploading",
                "processed": idx + 1,
                "total": total,
                "message": f"Item {idx + 1} / {total}",
            })
    except Exception as exc:  # noqa: BLE001
        logger.exception("wikidata upload job failed for %s", run_id)
        await finish_job(job_id, status=JOB_STATUS_FAILED, error=str(exc))
        return

    await finish_job(
        job_id,
        status=JOB_STATUS_SUCCEEDED,
        result={
            "dry_run": dry_run,
            "moratorium_lifted": os.environ.get("MORATORIUM_LIFTED", "").lower() == "true",
            "test_mode": os.environ.get("WIKIDATA_TEST_MODE", "").lower() == "true",
            "outcomes": [o.__dict__ for o in outcomes],
        },
        progress={
            "phase": "done",
            "processed": total,
            "total": total,
            "message": "Upload complete" if not dry_run else "Dry-run complete",
        },
    )
