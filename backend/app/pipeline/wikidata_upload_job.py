"""Background Wikidata upload / dry-run job (two-pass deferred links, W-192)."""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import replace
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

STEP_WRITE_ITEMS = 1
STEP_ADD_LINKS = 2
UPLOAD_STEP_TOTAL = 2


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


def estimate_remaining_seconds(
    done: int,
    total: int,
    elapsed: float,
    *,
    min_samples: int = 3,
    cap_seconds: int = 24 * 3600,
) -> int | None:
    """ETA for the current step. Hidden until ``min_samples`` completions."""
    if done < min_samples or total <= 0 or elapsed <= 0 or done <= 0:
        return None
    remaining_items = max(0, total - done)
    if remaining_items == 0:
        return 0
    est = remaining_items * (elapsed / done)
    return int(min(max(est, 0), cap_seconds))


def _item_display_label(item: Any) -> str:
    labels = getattr(item, "labels", None) or {}
    if isinstance(labels, dict) and labels:
        return str(
            labels.get("en") or labels.get("he") or next(iter(labels.values()), "") or "",
        )
    return ""


def _step_blob(
    *,
    step_id: str,
    label: str,
    status: str,
    processed: int,
    total: int,
    unit: str,
    eta_seconds: int | None,
    current_label: str,
) -> dict[str, Any]:
    return {
        "id": step_id,
        "label": label,
        "status": status,
        "processed": processed,
        "total": total,
        "unit": unit,
        "eta_seconds": eta_seconds,
        "current_label": current_label,
    }


def build_upload_progress(
    *,
    phase: str,
    message: str,
    upload_target: str,
    step: int,
    item_done: int,
    item_total: int,
    link_done: int,
    link_total: int,
    current_label: str,
    eta_seconds: int | None,
    elapsed_seconds: float,
    item_outcome: dict[str, Any] | None = None,
    recent_item_outcomes: list[dict[str, Any]] | None = None,
    outcome_counts: dict[str, int] | None = None,
    processing_local_id: str | None = None,
) -> dict[str, Any]:
    """W-112/W-113 two-step progress plus modal ``steps`` + ETA (W-192)."""
    step1_status = "running" if step == STEP_WRITE_ITEMS else "done"
    if step < STEP_WRITE_ITEMS:
        step1_status = "pending"
    step2_status = "pending"
    if step == STEP_ADD_LINKS:
        step2_status = "running"
    elif step > STEP_ADD_LINKS:
        step2_status = "done"

    item_eta = eta_seconds if step == STEP_WRITE_ITEMS else (0 if step > STEP_WRITE_ITEMS else None)
    link_eta = eta_seconds if step == STEP_ADD_LINKS else None
    if step > STEP_ADD_LINKS:
        link_eta = 0

    sub_processed = item_done if step == STEP_WRITE_ITEMS else link_done
    sub_total = item_total if step == STEP_WRITE_ITEMS else link_total
    sub_unit = "items" if step == STEP_WRITE_ITEMS else "links"
    if phase in {"cancelled", "failed"}:
        step1_status = "skipped" if step == STEP_WRITE_ITEMS and item_done < item_total else step1_status
        if step < STEP_ADD_LINKS:
            step2_status = "skipped"

    outer = STEP_WRITE_ITEMS if step <= STEP_WRITE_ITEMS else STEP_ADD_LINKS
    if phase == "done" or step > STEP_ADD_LINKS:
        outer = UPLOAD_STEP_TOTAL
    progress: dict[str, Any] = {
        "phase": phase,
        "processed": outer,
        "total": UPLOAD_STEP_TOTAL,
        "unit": "steps",
        "message": message,
        "upload_target": upload_target,
        "sub_processed": sub_processed,
        "sub_total": sub_total,
        "sub_unit": sub_unit,
        "sub_message": current_label or message,
        "eta_seconds": eta_seconds,
        "elapsed_seconds": int(elapsed_seconds),
        "current_label": current_label,
        "processing_local_id": processing_local_id,
        "steps": [
            _step_blob(
                step_id="write_items",
                label="Step 1 — Write items",
                status=step1_status,
                processed=item_done,
                total=item_total,
                unit="items",
                eta_seconds=item_eta,
                current_label=current_label if step == STEP_WRITE_ITEMS else (
                    "Complete" if step1_status == "done" else ""
                ),
            ),
            _step_blob(
                step_id="add_connections",
                label="Step 2 — Add connections",
                status=step2_status,
                processed=link_done,
                total=link_total,
                unit="links",
                eta_seconds=link_eta,
                current_label=current_label if step == STEP_ADD_LINKS else (
                    "Waiting for step 1" if step2_status == "pending" else (
                        "Complete" if step2_status == "done" else ""
                    )
                ),
            ),
        ],
    }
    if item_outcome is not None:
        progress["item_outcome"] = item_outcome
    if recent_item_outcomes is not None:
        progress["recent_item_outcomes"] = recent_item_outcomes
    if outcome_counts is not None:
        progress["outcome_counts"] = outcome_counts
    return progress


def _link_outcome_dict(row: wikidata_upload.DeferredLinkOutcome) -> dict[str, Any]:
    return {
        "source_local_id": row.source_local_id,
        "property_id": row.property_id,
        "target_local_id": row.target_local_id,
        "status": row.status,
        "message": row.message,
        "qid": row.qid,
    }


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
        scoped_ids = params.get("local_ids")
        scope: set[str] | None = None
        if isinstance(scoped_ids, list) and scoped_ids:
            scope = {str(x) for x in scoped_ids if str(x).strip()}
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

    from converter.wikidata.uploader import (  # noqa: PLC0415
        first_local_target,
        partition_unresolved_local,
        resolve_statement_locals,
        sort_items_for_upload,
    )

    native = sort_items_for_upload(list(native))
    native_by_id = {wikidata_studio.local_id_for_item(it): it for it in native}
    pass1_native = native
    if scope is not None:
        pass1_native = [
            it for it in native
            if wikidata_studio.local_id_for_item(it) in scope
        ]
    created_qids: dict[str, str] = {}
    deferred_by_local_id: dict[str, list[Any]] = {}

    item_total = len(pass1_native)
    link_total = 0
    label = {
        wikidata_upload.UPLOAD_TARGET_DRY_RUN: "Dry-run",
        wikidata_upload.UPLOAD_TARGET_TEST: "Test upload",
        wikidata_upload.UPLOAD_TARGET_LIVE: "Live upload",
    }.get(mode.target, "Upload")

    await update_job_progress(job_id, build_upload_progress(
        phase="uploading",
        message="Step 1 of 2: writing items",
        upload_target=mode.target,
        step=STEP_WRITE_ITEMS,
        item_done=0,
        item_total=item_total,
        link_done=0,
        link_total=0,
        current_label=f"{label}: {item_total} items…",
        eta_seconds=None,
        elapsed_seconds=0,
    ))

    if await is_cancel_requested(job_id):
        await finish_job(
            job_id,
            status=JOB_STATUS_CANCELLED,
            result={
                "outcomes": [],
                "link_outcomes": [],
                "processed": 0,
                "dry_run": mode.dry_run,
                "upload_target": mode.target,
                "moratorium_lifted": mode.moratorium_lifted,
                "test_mode": mode.test_mode,
            },
            progress=build_upload_progress(
                phase="cancelled",
                message="Cancelled by user",
                upload_target=mode.target,
                step=STEP_WRITE_ITEMS,
                item_done=0,
                item_total=item_total,
                link_done=0,
                link_total=0,
                current_label="Cancelled",
                eta_seconds=None,
                elapsed_seconds=0,
            ),
        )
        return

    if not mode.dry_run and not token:
        await finish_job(job_id, status=JOB_STATUS_FAILED, error="missing Wikidata token")
        return

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
        for it in pass1_native
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
    link_outcomes: list[wikidata_upload.DeferredLinkOutcome] = []
    audit_ctx = None if mode.dry_run else WikibaseAuditContext(
        actor_user_id=job.created_by,
        channel=CHANNEL_WIKIDATA_UPLOAD,
        project_id=project_id,
        run_id=run_id,
        job_id=job_id,
    )
    step1_started = time.monotonic()
    try:
        for idx, item in enumerate(pass1_native):
            if await is_cancel_requested(job_id):
                await finish_job(
                    job_id,
                    status=JOB_STATUS_CANCELLED,
                    result={
                        "outcomes": [o.__dict__ for o in outcomes],
                        "link_outcomes": [_link_outcome_dict(r) for r in link_outcomes],
                        "processed": idx,
                        "dry_run": mode.dry_run,
                        "upload_target": mode.target,
                        "moratorium_lifted": mode.moratorium_lifted,
                        "test_mode": mode.test_mode,
                    },
                    progress=build_upload_progress(
                        phase="cancelled",
                        message="Cancelled by user",
                        upload_target=mode.target,
                        step=STEP_WRITE_ITEMS,
                        item_done=idx,
                        item_total=item_total,
                        link_done=0,
                        link_total=link_total,
                        current_label="Cancelled",
                        eta_seconds=None,
                        elapsed_seconds=time.monotonic() - step1_started,
                        recent_item_outcomes=[
                            slim_upload_progress_outcome(o) for o in outcomes[-200:]
                        ],
                        outcome_counts=upload_outcome_counts(outcomes),
                    ),
                )
                return
            local_id = wikidata_studio.local_id_for_item(item)
            label_txt = _item_display_label(item)
            entity_type = str(getattr(item, "entity_type", "") or "")
            current = f"{entity_type} · {label_txt or local_id}".strip(" ·")
            elapsed = time.monotonic() - step1_started
            eta = estimate_remaining_seconds(idx, item_total, elapsed)
            processing_row = {
                "local_id": local_id,
                "label": label_txt or None,
                "entity_type": entity_type or None,
                "status": "processing",
                "qid": None,
                "wikibase_id": None,
                "message": "Processing…",
            }
            await update_job_progress(job_id, build_upload_progress(
                phase="uploading",
                message="Step 1 of 2: writing items",
                upload_target=mode.target,
                step=STEP_WRITE_ITEMS,
                item_done=idx,
                item_total=item_total,
                link_done=0,
                link_total=link_total,
                current_label=current,
                eta_seconds=eta,
                elapsed_seconds=elapsed,
                processing_local_id=local_id,
                item_outcome=processing_row,
                recent_item_outcomes=[
                    slim_upload_progress_outcome(o) for o in outcomes[-199:]
                ] + [processing_row],
                outcome_counts={**upload_outcome_counts(outcomes), "pending": 1},
            ))

            write_item, deferred = partition_unresolved_local(item, created_qids)
            if deferred:
                deferred_by_local_id[local_id] = deferred
                link_total = sum(len(v) for v in deferred_by_local_id.values())

            async with session_scope() as db:
                batch_outcomes = await wikidata_upload.upload_items(
                    [write_item], token=token or "", mode=mode,
                    audit_ctx=audit_ctx, db=db, ledger=ledger,
                    run_id=run_id,
                    uploader=shared_uploader,
                    existence_cache=existence_cache,
                    created_qids=created_qids,
                )
            outcomes.extend(batch_outcomes)
            if batch_outcomes:
                last = batch_outcomes[-1]
                wikidata_upload.remember_created_qid(
                    created_qids, local_id, last.qid, last.status,
                    dry_run=mode.dry_run,
                )
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
                            "link_outcomes": [],
                            "aborted_auth": True,
                        },
                        progress=build_upload_progress(
                            phase="failed",
                            message="Aborted: Wikidata login failure",
                            upload_target=mode.target,
                            step=STEP_WRITE_ITEMS,
                            item_done=idx + 1,
                            item_total=item_total,
                            link_done=0,
                            link_total=link_total,
                            current_label="Aborted",
                            eta_seconds=None,
                            elapsed_seconds=time.monotonic() - step1_started,
                            item_outcome=slim_upload_progress_outcome(last),
                            recent_item_outcomes=[
                                slim_upload_progress_outcome(o) for o in outcomes[-200:]
                            ],
                            outcome_counts=upload_outcome_counts(outcomes),
                        ),
                    )
                    return
            elapsed = time.monotonic() - step1_started
            await update_job_progress(job_id, build_upload_progress(
                phase="uploading",
                message="Step 1 of 2: writing items",
                upload_target=mode.target,
                step=STEP_WRITE_ITEMS,
                item_done=idx + 1,
                item_total=item_total,
                link_done=0,
                link_total=link_total,
                current_label=current,
                eta_seconds=estimate_remaining_seconds(idx + 1, item_total, elapsed),
                elapsed_seconds=elapsed,
                processing_local_id=None,
                item_outcome=(
                    slim_upload_progress_outcome(batch_outcomes[-1])
                    if batch_outcomes else None
                ),
                recent_item_outcomes=[
                    slim_upload_progress_outcome(o) for o in outcomes[-200:]
                ],
                outcome_counts=upload_outcome_counts(outcomes),
            ))
    except Exception as exc:  # noqa: BLE001
        logger.exception("wikidata upload job failed for %s", run_id)
        await finish_job(job_id, status=JOB_STATUS_FAILED, error=str(exc))
        return

    link_total = sum(len(v) for v in deferred_by_local_id.values())
    link_done = 0
    step2_started = time.monotonic()

    async def _finish_success() -> None:
        added = sum(1 for r in link_outcomes if r.status in {"linked", "would_link", "updated"})
        unresolved = sum(1 for r in link_outcomes if r.status == "unresolved")
        failed = sum(1 for r in link_outcomes if r.status in {"failed", "blocked"})
        await finish_job(
            job_id,
            status=JOB_STATUS_SUCCEEDED,
            result={
                "dry_run": mode.dry_run,
                "upload_target": mode.target,
                "moratorium_lifted": mode.moratorium_lifted,
                "test_mode": mode.test_mode,
                "outcomes": [o.__dict__ for o in outcomes],
                "link_outcomes": [_link_outcome_dict(r) for r in link_outcomes],
                "links_added": added,
                "links_unresolved": unresolved,
                "links_failed": failed,
            },
            progress=build_upload_progress(
                phase="done",
                message=f"{label} complete",
                upload_target=mode.target,
                step=STEP_ADD_LINKS + 1,
                item_done=item_total,
                item_total=item_total,
                link_done=link_total,
                link_total=link_total,
                current_label="Complete",
                eta_seconds=0,
                elapsed_seconds=time.monotonic() - step2_started,
                recent_item_outcomes=[
                    slim_upload_progress_outcome(o) for o in outcomes[-200:]
                ],
                outcome_counts=upload_outcome_counts(outcomes),
            ),
        )

    if not deferred_by_local_id:
        await _finish_success()
        return

    def _link_in_retry_scope(source_id: str, stmts: list[Any]) -> bool:
        if scope is None:
            return True
        if source_id in scope:
            return True
        return any(first_local_target(s) in scope for s in stmts)

    outcome_by_id = {o.local_id: o for o in outcomes}
    skipped_person_names = {
        o.local_id: (o.label or "").strip()
        for o in outcomes
        if o.status == "skipped"
        and str(o.entity_type or "").lower() == "person"
        and (o.label or "").strip()
    }

    try:
        for source_id, stmts in deferred_by_local_id.items():
            if await is_cancel_requested(job_id):
                await finish_job(
                    job_id,
                    status=JOB_STATUS_CANCELLED,
                    result={
                        "outcomes": [o.__dict__ for o in outcomes],
                        "link_outcomes": [_link_outcome_dict(r) for r in link_outcomes],
                        "processed": item_total,
                        "dry_run": mode.dry_run,
                        "upload_target": mode.target,
                        "moratorium_lifted": mode.moratorium_lifted,
                        "test_mode": mode.test_mode,
                    },
                    progress=build_upload_progress(
                        phase="cancelled",
                        message="Cancelled by user",
                        upload_target=mode.target,
                        step=STEP_ADD_LINKS,
                        item_done=item_total,
                        item_total=item_total,
                        link_done=link_done,
                        link_total=link_total,
                        current_label="Cancelled",
                        eta_seconds=None,
                        elapsed_seconds=time.monotonic() - step2_started,
                        recent_item_outcomes=[
                            slim_upload_progress_outcome(o) for o in outcomes[-200:]
                        ],
                        outcome_counts=upload_outcome_counts(outcomes),
                    ),
                )
                return
            if not _link_in_retry_scope(source_id, stmts):
                link_done += len(stmts)
                continue

            source_item = native_by_id.get(source_id)
            source_qid = created_qids.get(source_id)
            source_label = _item_display_label(source_item) if source_item is not None else source_id
            current = f"links · {source_label or source_id}"
            elapsed = time.monotonic() - step2_started
            await update_job_progress(job_id, build_upload_progress(
                phase="uploading",
                message="Step 2 of 2: adding connections",
                upload_target=mode.target,
                step=STEP_ADD_LINKS,
                item_done=item_total,
                item_total=item_total,
                link_done=link_done,
                link_total=link_total,
                current_label=current,
                eta_seconds=estimate_remaining_seconds(link_done, link_total, elapsed)
                if link_done else None,
                elapsed_seconds=elapsed,
                processing_local_id=source_id,
                recent_item_outcomes=[
                    slim_upload_progress_outcome(o) for o in outcomes[-200:]
                ],
                outcome_counts=upload_outcome_counts(outcomes),
            ))

            if not source_qid or source_item is None:
                for stmt in stmts:
                    target = first_local_target(stmt)
                    link_outcomes.append(wikidata_upload.DeferredLinkOutcome(
                        source_local_id=source_id,
                        property_id=stmt.property_id,
                        target_local_id=target,
                        status="unresolved",
                        message=(
                            f"Source {source_id} has no QID from step 1; "
                            f"cannot add {stmt.property_id} → {target or '?'}"
                        ),
                    ))
                    link_done += 1
                continue

            if not wikidata_upload.pass2_may_update_source(outcome_by_id.get(source_id)):
                for stmt in stmts:
                    target = first_local_target(stmt)
                    link_outcomes.append(wikidata_upload.DeferredLinkOutcome(
                        source_local_id=source_id,
                        property_id=stmt.property_id,
                        target_local_id=target,
                        status="skipped_foreign",
                        message=(
                            f"Pass 2 UPDATE refused for {source_id}: not an "
                            "item we created or own (Rule W-195)"
                        ),
                        qid=source_qid if str(source_qid).startswith("Q") else None,
                    ))
                    link_done += 1
                continue

            resolved: list[Any] = []
            resolved_targets: list[str] = []
            for stmt in stmts:
                target = first_local_target(stmt)
                rewritten, leftover = resolve_statement_locals(stmt, created_qids)
                if leftover:
                    name = skipped_person_names.get(target or "")
                    pid = str(getattr(stmt, "property_id", "") or "")
                    if name and pid == "P50":
                        resolved.append(
                            wikidata_upload.rewrite_author_link_to_name_string(
                                stmt, name,
                            )
                        )
                        resolved_targets.append(target)
                        continue
                    link_outcomes.append(wikidata_upload.DeferredLinkOutcome(
                        source_local_id=source_id,
                        property_id=stmt.property_id,
                        target_local_id=target,
                        status="unresolved",
                        message=(
                            f"Target {target or leftover[0]} was not created; "
                            f"left {stmt.property_id} unresolved"
                        ),
                        qid=source_qid if str(source_qid).startswith("Q") else None,
                    ))
                    link_done += 1
                    continue
                resolved.append(rewritten)
                resolved_targets.append(target)

            if not resolved:
                continue

            if mode.dry_run:
                pids = ", ".join(s.property_id for s in resolved)
                patch = {
                    "local_id": source_id,
                    "label": source_label or None,
                    "entity_type": getattr(source_item, "entity_type", None),
                    "status": "would_update",
                    "qid": source_qid if str(source_qid).startswith("Q") else None,
                    "wikibase_id": source_qid if str(source_qid).startswith("Q") else None,
                    "message": f"Would add connections {pids}",
                }
                for stmt, target in zip(resolved, resolved_targets, strict=True):
                    link_outcomes.append(wikidata_upload.DeferredLinkOutcome(
                        source_local_id=source_id,
                        property_id=stmt.property_id,
                        target_local_id=target,
                        status="would_link",
                        message=f"Would add {stmt.property_id} on {source_id}",
                        qid=patch["qid"],
                    ))
                    link_done += 1
                await update_job_progress(job_id, build_upload_progress(
                    phase="uploading",
                    message="Step 2 of 2: adding connections",
                    upload_target=mode.target,
                    step=STEP_ADD_LINKS,
                    item_done=item_total,
                    item_total=item_total,
                    link_done=link_done,
                    link_total=link_total,
                    current_label=current,
                    eta_seconds=estimate_remaining_seconds(
                        link_done, link_total, time.monotonic() - step2_started,
                    ),
                    elapsed_seconds=time.monotonic() - step2_started,
                    processing_local_id=None,
                    item_outcome=patch,
                    recent_item_outcomes=[
                        slim_upload_progress_outcome(o) for o in outcomes[-199:]
                    ] + [patch],
                    outcome_counts=upload_outcome_counts(outcomes),
                ))
                continue

            clone = replace(
                source_item,
                existing_qid=source_qid,
                statements=resolved,
            )
            async with session_scope() as db:
                batch_outcomes = await wikidata_upload.upload_items(
                    [clone], token=token or "", mode=mode,
                    audit_ctx=audit_ctx, db=db, ledger=ledger,
                    run_id=run_id,
                    uploader=shared_uploader,
                    existence_cache=existence_cache,
                    created_qids=created_qids,
                )
            last = batch_outcomes[-1] if batch_outcomes else None
            status = last.status if last else "failed"
            message = last.message if last else "no outcome"
            qid = last.qid if last else source_qid
            if last and (
                last.status in {"failed", "skipped"}
                and wikidata_upload._is_auth_failure_message(last.message)
            ):
                await finish_job(
                    job_id,
                    status=JOB_STATUS_FAILED,
                    error=(
                        "Wikidata authentication failed during connection pass; "
                        f"aborted. {last.message}"
                    ),
                    result={
                        "dry_run": mode.dry_run,
                        "upload_target": mode.target,
                        "moratorium_lifted": mode.moratorium_lifted,
                        "test_mode": mode.test_mode,
                        "outcomes": [o.__dict__ for o in outcomes],
                        "link_outcomes": [_link_outcome_dict(r) for r in link_outcomes],
                        "aborted_auth": True,
                    },
                    progress=build_upload_progress(
                        phase="failed",
                        message="Aborted: Wikidata login failure",
                        upload_target=mode.target,
                        step=STEP_ADD_LINKS,
                        item_done=item_total,
                        item_total=item_total,
                        link_done=link_done,
                        link_total=link_total,
                        current_label="Aborted",
                        eta_seconds=None,
                        elapsed_seconds=time.monotonic() - step2_started,
                    ),
                )
                return
            link_status = "linked"
            if status in {"blocked", "failed", "skipped"}:
                link_status = "failed" if status == "failed" else status
            pids = ", ".join(s.property_id for s in resolved)
            patch = {
                "local_id": source_id,
                "label": source_label or None,
                "entity_type": getattr(source_item, "entity_type", None),
                "status": "updated" if link_status == "linked" else status,
                "qid": qid,
                "wikibase_id": qid,
                "message": f"+{pids} {message}",
            }
            for stmt, target in zip(resolved, resolved_targets, strict=True):
                link_outcomes.append(wikidata_upload.DeferredLinkOutcome(
                    source_local_id=source_id,
                    property_id=stmt.property_id,
                    target_local_id=target,
                    status=link_status if link_status != "updated" else "linked",
                    message=message,
                    qid=qid,
                ))
                link_done += 1
            await update_job_progress(job_id, build_upload_progress(
                phase="uploading",
                message="Step 2 of 2: adding connections",
                upload_target=mode.target,
                step=STEP_ADD_LINKS,
                item_done=item_total,
                item_total=item_total,
                link_done=link_done,
                link_total=link_total,
                current_label=current,
                eta_seconds=estimate_remaining_seconds(
                    link_done, link_total, time.monotonic() - step2_started,
                ),
                elapsed_seconds=time.monotonic() - step2_started,
                processing_local_id=None,
                item_outcome=patch,
                recent_item_outcomes=[
                    slim_upload_progress_outcome(o) for o in outcomes[-199:]
                ] + [patch],
                outcome_counts=upload_outcome_counts(outcomes),
            ))
    except Exception as exc:  # noqa: BLE001
        logger.exception("wikidata upload link pass failed for %s", run_id)
        await finish_job(job_id, status=JOB_STATUS_FAILED, error=str(exc))
        return

    await _finish_success()
