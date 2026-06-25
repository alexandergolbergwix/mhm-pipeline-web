"""Background authority re-enrich job with progress + cancel."""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from typing import Any

from sqlalchemy import func, select

from app.db import session_scope
from app.models.run import AuthorityMatch, Run, RunRecord
from app.models.run_job import (
    JOB_STATUS_CANCELLED,
    JOB_STATUS_FAILED,
    JOB_STATUS_SUCCEEDED,
    RunJob,
)
from app.pipeline import authority as auth_pipeline
from app.pipeline.authority_re_enrich import match_key
from app.pipeline.entity_normalize import normalize_entity_text, normalize_role
from app.pipeline.marc_ingest import extract_named_entities, prepare_record_for_pipeline
from app.pipeline.run_job_service import (
    finish_job,
    is_cancel_requested,
    update_job_progress,
)

logger = logging.getLogger(__name__)


async def run_authority_re_enrich_job(job_id: uuid.UUID) -> None:
    async with session_scope() as db:
        job = await db.get(RunJob, job_id)
        if job is None:
            return
        run_id = job.run_id
        skip_cache = bool((job.params or {}).get("skip_cache"))
        run = await db.get(Run, run_id)
        if run is None:
            await finish_job(job_id, status=JOB_STATUS_FAILED, error="run not found")
            return

        matcher = auth_pipeline.get_default_matcher()
        records = (
            await db.execute(select(RunRecord).where(RunRecord.run_id == run_id))
        ).scalars().all()
        existing_rows = (
            await db.execute(
                select(AuthorityMatch).where(AuthorityMatch.run_id == run_id)
            )
        ).scalars().all()

    existing_idx: dict[tuple[str, str, str, str], list[AuthorityMatch]] = defaultdict(list)
    for m in existing_rows:
        existing_idx[match_key(
            m.control_number, m.entity_text, m.entity_kind, m.role or "",
        )].append(m)

    all_entities: list[tuple[RunRecord, dict[str, Any]]] = []
    for rec in records:
        marc = prepare_record_for_pipeline(dict(rec.marc or {}))
        for entity in extract_named_entities(marc):
            all_entities.append((rec, entity))

    total_entities = len(all_entities)
    await update_job_progress(job_id, {
        "phase": "running",
        "processed": 0,
        "total": total_entities,
        "message": "Starting authority re-enrich…",
    })

    checked = 0
    updated = 0
    newly_matched = 0
    matched_count = 0
    orphans_removed = 0
    source_counts: dict[str, int] = {}
    produced_keys: set[tuple[str, str, str, str]] = set()

    async with session_scope() as db:
        run = await db.get(Run, run_id)
        if run is None:
            await finish_job(job_id, status=JOB_STATUS_FAILED, error="run not found")
            return
        matcher = auth_pipeline.get_default_matcher()

        for idx, (rec, entity) in enumerate(all_entities):
            if await is_cancel_requested(job_id):
                await db.commit()
                await finish_job(
                    job_id,
                    status=JOB_STATUS_CANCELLED,
                    progress={
                        "phase": "cancelled",
                        "processed": idx,
                        "total": total_entities,
                        "message": "Cancelled by user",
                    },
                )
                return

            checked += 1
            clean_text = normalize_entity_text(entity.get("text", ""))
            clean_role = normalize_role(entity.get("role", ""))
            kind = entity.get("kind", "person")
            produced_keys.add(match_key(rec.control_number, clean_text, kind, clean_role))

            await update_job_progress(job_id, {
                "phase": "running",
                "processed": idx,
                "total": total_entities,
                "message": str(entity.get("text") or ""),
                "current_entity": str(entity.get("text") or ""),
                "entity_kind": kind,
            })

            prepared_marc = prepare_record_for_pipeline(dict(rec.marc or {}))
            candidates: list[Any] = []
            try:
                candidates = await matcher.match(
                    entity, prepared_marc,
                    db_session=db,
                    user_id=run.created_by,
                    skip_cache=skip_cache,
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "re-enrich job: authority match failed for %r",
                    entity.get("text"),
                )

            matched = bool(candidates)
            c = candidates[0] if candidates else None
            is_new = False
            if matched:
                matched_count += 1
            if c:
                payload_sources = c.payload.get("sources") if isinstance(c.payload, dict) else None
                sources = payload_sources if isinstance(payload_sources, list) else [c.source]
                for source in sources:
                    source_key = str(source or "").strip()
                    if source_key:
                        source_counts[source_key] = source_counts.get(source_key, 0) + 1

            if c:
                key = match_key(rec.control_number, clean_text, kind, clean_role)
                if key in existing_idx:
                    matches = existing_idx[key]
                    m = matches[0]
                    for dup in matches[1:]:
                        await db.delete(dup)
                        orphans_removed += 1
                    existing_idx[key] = [m]
                    m.entity_text = clean_text
                    m.role = clean_role
                    m.entity_kind = kind
                    m.matched_name = c.matched_name
                    m.mazal_id = c.mazal_id
                    m.viaf_id = c.viaf_id
                    m.wikidata_qid = c.wikidata_qid
                    m.confidence = c.confidence
                    m.source = c.source
                    m.payload = c.payload
                    updated += 1
                else:
                    row = AuthorityMatch(
                        run_id=run_id,
                        control_number=rec.control_number,
                        entity_text=clean_text,
                        entity_kind=kind,
                        role=clean_role,
                        matched_name=c.matched_name,
                        mazal_id=c.mazal_id,
                        viaf_id=c.viaf_id,
                        wikidata_qid=c.wikidata_qid,
                        confidence=c.confidence,
                        source=c.source,
                        payload=c.payload,
                    )
                    db.add(row)
                    await db.flush()
                    existing_idx[key] = [row]
                    newly_matched += 1
                    is_new = True

            await update_job_progress(job_id, {
                "phase": "running",
                "processed": idx + 1,
                "total": total_entities,
                "message": str(entity.get("text") or ""),
                "current_entity": str(entity.get("text") or ""),
                "current_source": c.source if c else None,
                "matched": matched,
                "is_new": is_new,
                "entity_kind": kind,
            })

        for m in existing_rows:
            k = match_key(m.control_number, m.entity_text, m.entity_kind, m.role or "")
            if k not in produced_keys:
                await db.delete(m)
                orphans_removed += 1

        remaining_rows = (
            await db.execute(
                select(AuthorityMatch).where(AuthorityMatch.run_id == run_id)
            )
        ).scalars().all()
        from app.pipeline.authority_post_enrich import finalize_authority_matches  # noqa: PLC0415

        finalize_authority_matches(list(remaining_rows))
        await db.flush()

        remaining_count = await db.scalar(
            select(func.count())
            .select_from(AuthorityMatch)
            .where(AuthorityMatch.run_id == run_id)
        )
        run.match_count = int(remaining_count or 0)
        await db.commit()

    result = {
        "checked": checked,
        "matched": matched_count,
        "updated": updated,
        "newly_matched": newly_matched,
        "orphans_removed": orphans_removed,
        "source_counts": source_counts,
        "skip_cache": skip_cache,
    }
    await finish_job(
        job_id,
        status=JOB_STATUS_SUCCEEDED,
        result=result,
        progress={
            "phase": "done",
            "processed": total_entities,
            "total": total_entities,
            "message": "Complete",
        },
    )
