"""Shared authority re-enrich orchestration for POST and SSE endpoints."""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.run import AuthorityMatch, Run, RunRecord
from app.pipeline.entity_normalize import (
    normalize_entity_key,
    normalize_entity_text,
    normalize_role,
)
from app.pipeline.marc_ingest import extract_named_entities, prepare_record_for_pipeline

logger = logging.getLogger(__name__)


def match_key(
    control_number: str,
    text: str,
    kind: str,
    role: str,
) -> tuple[str, str, str, str]:
    return (
        control_number,
        normalize_entity_key(normalize_entity_text(text)),
        kind,
        normalize_role(role),
    )


async def re_enrich_run(
    db: AsyncSession,
    run: Run,
    matcher: Any,
    *,
    skip_cache: bool,
    records: list[RunRecord],
    existing_rows: list[AuthorityMatch],
) -> dict[str, int]:
    """Re-match every entity; upsert by normalised key; purge orphan rows."""
    run_id = run.id
    user_id = run.created_by

    existing_idx: dict[tuple[str, str, str, str], list[AuthorityMatch]] = defaultdict(list)
    orphan_pairs: list[tuple[AuthorityMatch, tuple[str, str, str, str]]] = []
    for m in existing_rows:
        key = match_key(
            str(m.control_number or ""),
            str(m.entity_text or ""),
            str(m.entity_kind or ""),
            str(m.role or ""),
        )
        existing_idx[key].append(m)
        orphan_pairs.append((m, key))

    # Materialise records before any await — async ORM cannot lazy-load after
    # matcher threads / flush expire attributes on the shared session.
    record_rows: list[tuple[str, dict[str, Any]]] = []
    for rec in records:
        record_rows.append((
            str(rec.control_number or ""),
            prepare_record_for_pipeline(dict(rec.marc or {})),
        ))

    produced_keys: set[tuple[str, str, str, str]] = set()
    checked = 0
    updated = 0
    newly_matched = 0
    orphans_removed = 0

    for control_number, marc in record_rows:
        for entity in extract_named_entities(marc):
            checked += 1
            clean_text = normalize_entity_text(entity.get("text", ""))
            clean_role = normalize_role(entity.get("role", ""))
            kind = entity.get("kind", "person")
            key = match_key(control_number, clean_text, kind, clean_role)
            produced_keys.add(key)

            try:
                candidates = await matcher.match(
                    entity, marc,
                    db_session=db,
                    user_id=user_id,
                    skip_cache=skip_cache,
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "re-enrich: authority match failed for %r", entity.get("text"),
                )
                candidates = []

            if not candidates:
                continue

            c = candidates[0]

            if key in existing_idx:
                matches = existing_idx[key]
                primary = matches[0]
                for dup in matches[1:]:
                    await db.delete(dup)
                    orphans_removed += 1
                existing_idx[key] = [primary]
                primary.entity_text = clean_text
                primary.role = clean_role
                primary.entity_kind = kind
                primary.matched_name = c.matched_name
                primary.mazal_id = c.mazal_id
                primary.viaf_id = c.viaf_id
                primary.wikidata_qid = c.wikidata_qid
                primary.confidence = c.confidence
                primary.source = c.source
                primary.payload = c.payload
                updated += 1
            else:
                row = AuthorityMatch(
                    run_id=run_id,
                    control_number=control_number,
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

    for m, key in orphan_pairs:
        if key not in produced_keys:
            await db.delete(m)
            orphans_removed += 1

    remaining_rows = (
        await db.execute(
            select(AuthorityMatch).where(AuthorityMatch.run_id == run_id)
        )
    ).scalars().all()
    from app.pipeline.authority_post_enrich import finalize_authority_matches  # noqa: PLC0415

    stats_extra = finalize_authority_matches(list(remaining_rows))
    cross_linked = stats_extra["cross_linked"]
    wd_crosschecked = stats_extra["wikidata_crosschecked"]

    await db.flush()
    remaining_count = await db.scalar(
        select(func.count())
        .select_from(AuthorityMatch)
        .where(AuthorityMatch.run_id == run_id)
    )
    run.match_count = int(remaining_count or 0)
    await db.commit()
    return {
        "checked": checked,
        "updated": updated,
        "newly_matched": newly_matched,
        "orphans_removed": orphans_removed,
        "cross_linked": cross_linked,
        "wikidata_crosschecked": wd_crosschecked,
    }
