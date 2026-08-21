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


def slim_upload_progress_outcome(outcome: Any) -> dict[str, Any]:
    """Slim per-item row for live upload progress (modal + table patches)."""
    return {
        "local_id": outcome.local_id,
        "label": outcome.label,
        "entity_type": outcome.entity_type,
        "status": outcome.status,
        "qid": outcome.qid,
        "wikibase_id": outcome.qid,
        "message": outcome.message,
    }


def upload_outcome_counts(outcomes: list[Any]) -> dict[str, int]:
    """Aggregate upload statuses for the live modal count strip."""
    counts = {
        "created": 0,
        "updated": 0,
        "adopted": 0,
        "blocked": 0,
        "skipped": 0,
        "failed": 0,
        "pending": 0,
    }
    for o in outcomes:
        status = str(getattr(o, "status", "") or "").lower()
        if status in {"success", "created", "would_create"}:
            counts["created"] += 1
        elif status in {"updated", "exists", "would_update"}:
            counts["updated"] += 1
        elif status in {"adopted", "would_adopt"}:
            counts["adopted"] += 1
        elif status in {"blocked", "would_block"}:
            counts["blocked"] += 1
        elif status == "skipped":
            counts["skipped"] += 1
        elif status == "failed":
            counts["failed"] += 1
        elif status == "pending":
            counts["pending"] += 1
    return counts


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

    from converter.wikidata.uploader import sort_items_for_upload  # noqa: PLC0415

    native = sort_items_for_upload(list(native))
    created_qids: dict[str, str] = {}

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
        await finish_job(
            job_id,
            status=JOB_STATUS_CANCELLED,
            result={
                "outcomes": [],
                "processed": 0,
                "dry_run": mode.dry_run,
                "upload_target": mode.target,
                "moratorium_lifted": mode.moratorium_lifted,
                "test_mode": mode.test_mode,
            },
            progress={
                "phase": "cancelled",
                "processed": 0,
                "total": total,
                "message": "Cancelled by user",
                "upload_target": mode.target,
            },
        )
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

    from app.pipeline.wikidata_existence import confirm_qids_alive  # noqa: PLC0415

    prefetch_qids = sorted({
        str(getattr(it, "existing_qid", "") or "").strip()
        for it in native
        if str(getattr(it, "existing_qid", "") or "").strip().startswith("Q")
    })
    existence_cache: dict[str, bool | None] = {}
    if prefetch_qids:
        existence_cache = await run_in_threadpool(
            confirm_qids_alive, prefetch_qids, is_test=mode.is_test,
        )
        logger.info(
            "Prefetched existence for %d QIDs (test=%s): %d alive, %d missing, %d unknown",
            len(prefetch_qids),
            mode.is_test,
            sum(1 for v in existence_cache.values() if v is True),
            sum(1 for v in existence_cache.values() if v is False),
            sum(1 for v in existence_cache.values() if v is None),
        )

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
                    result={
                        "outcomes": [o.__dict__ for o in outcomes],
                        "processed": idx,
                        "dry_run": mode.dry_run,
                        "upload_target": mode.target,
                        "moratorium_lifted": mode.moratorium_lifted,
                        "test_mode": mode.test_mode,
                    },
                    progress={
                        "phase": "cancelled",
                        "processed": idx,
                        "total": total,
                        "message": "Cancelled by user",
                        "upload_target": mode.target,
                        "processing_local_id": None,
                    },
                )
                return
            local_id = wikidata_studio.local_id_for_item(item)
            label_txt = ""
            labels = getattr(item, "labels", None) or {}
            if isinstance(labels, dict):
                label_txt = str(labels.get("en") or labels.get("he") or next(iter(labels.values()), "") or "")
            entity_type = str(getattr(item, "entity_type", "") or "")
            # Announce the row under work before the (slow) write so the review
            # table can show a loading pill instead of looking stuck.
            await update_job_progress(job_id, {
                "phase": "uploading",
                "processed": idx,
                "total": total,
                "message": f"Processing item {idx + 1} / {total}",
                "upload_target": mode.target,
                "processing_local_id": local_id,
                "item_outcome": {
                    "local_id": local_id,
                    "label": label_txt or None,
                    "entity_type": entity_type or None,
                    "status": "processing",
                    "qid": None,
                    "wikibase_id": None,
                    "message": "Processing…",
                },
                "recent_item_outcomes": [
                    slim_upload_progress_outcome(o) for o in outcomes[-199:]
                ] + [{
                    "local_id": local_id,
                    "label": label_txt or None,
                    "entity_type": entity_type or None,
                    "status": "processing",
                    "qid": None,
                    "wikibase_id": None,
                    "message": "Processing…",
                }],
                "outcome_counts": {
                    **upload_outcome_counts(outcomes),
                    "pending": 1,
                },
            })
            async with session_scope() as db:
                batch_outcomes = await wikidata_upload.upload_items(
                    [item], token=token or "", mode=mode,
                    audit_ctx=audit_ctx, db=db, ledger=ledger,
                    run_id=run_id,
                    uploader=shared_uploader,
                    existence_cache=existence_cache,
                    created_qids=created_qids,
                )
            outcomes.extend(batch_outcomes)
            item_outcome = None
            if batch_outcomes:
                last = batch_outcomes[-1]
                item_outcome = slim_upload_progress_outcome(last)
                if (
                    last.status in {"failed", "skipped"}
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
                            "processing_local_id": None,
                        },
                    )
                    return
            progress: dict = {
                "phase": "uploading",
                "processed": idx + 1,
                "total": total,
                "message": f"Item {idx + 1} / {total}",
                "upload_target": mode.target,
                "processing_local_id": None,
            }
            if item_outcome is not None:
                recent = [
                    slim_upload_progress_outcome(o)
                    for o in outcomes[-200:]
                ]
                progress["item_outcome"] = item_outcome
                progress["recent_item_outcomes"] = recent
                progress["outcome_counts"] = upload_outcome_counts(outcomes)
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
