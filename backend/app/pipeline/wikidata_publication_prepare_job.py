"""Build a durable Publication Release outside the HTTP request."""

from __future__ import annotations

import asyncio
import uuid

from converter.wikidata import troki_author_repair as troki
from sqlalchemy import select

from app.db import session_scope
from app.models.run_job import (
    JOB_KIND_WIKIDATA_GILA_AUDIT,
    JOB_STATUS_CANCELLED,
    JOB_STATUS_FAILED,
    JOB_STATUS_SUCCEEDED,
    RunJob,
)
from app.pipeline.run_job_service import finish_job, is_cancel_requested, update_job_progress
from app.pipeline.wikidata_item_views import StudioBuildMissingError, fetch_merged_wikidata_item
from app.pipeline.wikidata_reference_reader import ReferenceItemReader
from app.publication.credentials import configured_publication_gateway_factory
from app.publication.reviewed_reference_scope import GILA_CREATOR_UID
from app.publication.runtime import PublicationRuntime, _has_blocking_item_issue
from app.schemas.publication import PreparePublicationRequest


async def _author_evidence_is_current(reader: ReferenceItemReader) -> bool:
    work, manuscript, author = await asyncio.gather(
        reader.fetch(troki.WORK_QID), reader.fetch(troki.MANUSCRIPT_QID),
        reader.fetch(troki.AUTHOR_QID),
    )
    return (
        troki.work_matches(work, require_missing=True)
        and troki.manuscript_links_work(manuscript)
        and troki.author_matches(author)
    )


async def _mapped_target(db, run_id, local_id, qid, exact_author):
    try:
        item = await fetch_merged_wikidata_item(db, run_id, local_id, source="canonical")
    except StudioBuildMissingError:
        return False
    if not item or item.get("rebuild_required") or _has_blocking_item_issue(item):
        return False
    if exact_author:
        return qid == troki.WORK_QID and troki.author_statement(item) is not None
    return item.get("existing_qid") == qid


async def _creator_result(reader: ReferenceItemReader, qid: str) -> str | None:
    try:
        creator = await reader.creator_uid(qid)
    except ValueError:
        return "creator_unavailable"
    if creator is None:
        return "creator_unavailable"
    return None if creator == GILA_CREATOR_UID else "other_creator"


async def _select_batch(job_id, db, run_id, entries):
    by_local = {}
    by_qid = {}
    for local_id, qid, exact_author in entries:
        by_local.setdefault(local_id, set()).add((qid, exact_author))
        by_qid.setdefault(qid, set()).add((local_id, exact_author))
    exclusions = []
    candidates = []
    for qid, mappings in sorted(by_qid.items()):
        if len(mappings) != 1:
            exclusions.append({"qid": qid, "reason": "target_conflict"})
            continue
        local_id, exact_author = next(iter(mappings))
        if len(by_local[local_id]) != 1:
            exclusions.append({"qid": qid, "reason": "target_conflict"})
        elif not await _mapped_target(db, run_id, local_id, qid, exact_author):
            exclusions.append({"qid": qid, "reason": "source_changed"})
        else:
            candidates.append((local_id, qid, exact_author))
    await db.commit()

    reader = ReferenceItemReader()
    selection = {}
    includes_exact_author = False
    for offset in range(0, len(candidates), 5):
        if await is_cancel_requested(job_id):
            return None
        group = candidates[offset : offset + 5]
        outcomes = await asyncio.gather(*(_creator_result(reader, qid) for _, qid, _ in group))
        for (local_id, qid, exact_author), reason in zip(group, outcomes, strict=True):
            if not reason and exact_author:
                try:
                    if not await _author_evidence_is_current(reader):
                        reason = "source_changed"
                except ValueError:
                    reason = "evidence_unavailable"
            if reason:
                exclusions.append({"qid": qid, "reason": reason})
            else:
                selection[local_id] = qid
                includes_exact_author |= exact_author
        await update_job_progress(job_id, {
            "phase": "check_creators",
            "processed": len(selection) + len(exclusions),
            "total": len(by_qid),
            "unit": "items",
            "message": "Check audited targets and first-revision creators.",
        })
    return selection, includes_exact_author, {
        "prepared_qids": sorted(selection.values()),
        "exclusions": exclusions,
        "batch_counts": {"eligible": len(by_qid), "prepared": len(selection)},
    }


async def run_wikidata_publication_prepare_job(job_id: uuid.UUID) -> None:
    async with session_scope() as db:
        job = await db.get(RunJob, job_id)
        if job is None:
            return
        params = job.params or {}
        actor_id = str(params.get("actor_id") or "").strip()
        payload = params.get("request")
        if not actor_id or not isinstance(payload, dict):
            await finish_job(
                job_id,
                status=JOB_STATUS_FAILED,
                error="Invalid Publication prepare job parameters",
            )
            return
        scoped = "_selected_existing" in params or "_gila_batch" in params
        await update_job_progress(job_id, {
            "phase": "preparing",
            "message": "Verify the audited source and prepare the Publication release."
            if scoped else "Prepare the immutable Publication Release.",
        })
        freshness_timeout = None
        troki_author_only = params.get("_gila_troki_author") is True
        selected = params.get("_selected_existing")
        batch = params.get("_gila_batch")
        batch_result = {}
        if selected is not None or batch:
            from app.pipeline.marc_verify_context import load_run_marc_records
            from app.pipeline.wikidata_gila_audit_job import gila_record_fingerprint
            from app.routers.wikidata_studio import STUDIO_JOB_FRESHNESS_TIMEOUT_SECONDS

            expected_fingerprint = params.get("_gila_source_fingerprint")
            if expected_fingerprint is not None:
                records = await load_run_marc_records(db, job.run_id)
                if gila_record_fingerprint(records) != expected_fingerprint:
                    await finish_job(
                        job_id, status=JOB_STATUS_FAILED,
                        error="The source changed. Run a new audit before preparation.",
                    )
                    return
            if batch:
                selection = await _select_batch(job_id, db, job.run_id, batch)
                if selection is None or await is_cancel_requested(job_id):
                    await finish_job(job_id, status=JOB_STATUS_CANCELLED)
                    return
                selected, troki_author_only, batch_result = selection
                latest = await db.scalar(select(RunJob.id).where(
                    RunJob.run_id == job.run_id,
                    RunJob.kind == JOB_KIND_WIKIDATA_GILA_AUDIT,
                    RunJob.status == JOB_STATUS_SUCCEEDED,
                ).order_by(RunJob.created_at.desc(), RunJob.id.desc()).limit(1))
                await db.commit()
                if str(latest) != params["_gila_audit_id"]:
                    await finish_job(
                        job_id, status=JOB_STATUS_FAILED,
                        error="The audit changed. Start the batch from the latest audit.",
                    )
                    return
                if not selected:
                    await finish_job(
                        job_id, status=JOB_STATUS_SUCCEEDED, result=batch_result,
                        progress={
                            "phase": "succeeded", "message": "No items passed the repair checks.",
                        },
                    )
                    return
            else:
                for local_id, qid in selected.items():
                    if not await _mapped_target(db, job.run_id, local_id, qid, troki_author_only):
                        await finish_job(
                            job_id, status=JOB_STATUS_FAILED,
                            error="The selected item changed. Rebuild Studio and run a new audit.",
                        )
                        return
                if troki_author_only and not await _author_evidence_is_current(
                    ReferenceItemReader(),
                ):
                    await finish_job(
                        job_id, status=JOB_STATUS_FAILED,
                        error="The repair evidence changed. Run a new audit before preparation.",
                    )
                    return
            freshness_timeout = STUDIO_JOB_FRESHNESS_TIMEOUT_SECONDS
        runtime = PublicationRuntime(
            session=db, gateway_factory=configured_publication_gateway_factory,
            freshness_timeout_seconds=freshness_timeout,
        )
        response = await runtime.prepare(
            run_id=job.run_id,
            request=PreparePublicationRequest.model_validate(payload),
            actor_id=actor_id,
            **({
                "selected_existing": selected,
                "expected_snapshot": tuple(params["_gila_build_snapshot"]),
                "troki_author_only": troki_author_only,
            } if selected is not None else {}),
        )
    publication = response.publication
    if publication is None:
        await finish_job(
            job_id, status=JOB_STATUS_FAILED, error="Publication preparation returned no Release",
        )
        return
    await finish_job(
        job_id,
        status=JOB_STATUS_SUCCEEDED,
        result={**batch_result, "publication_id": publication.publication_id},
        progress={
            "phase": "succeeded",
            "processed": publication.current_release.entity_count,
            "total": publication.current_release.entity_count,
            "unit": "entities",
            "message": "The immutable Publication Release is ready for review.",
        },
    )
