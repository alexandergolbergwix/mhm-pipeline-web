"""Execute one ingest run: parse upload → persist records → resolve
authority candidates → persist matches.

Synchronous for the MVP. Phase 7 (real-time collab) and Phase 6
(history) wrap this with WebSocket fan-out + event-log appends; the
loop here is unchanged.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.run import (
    RUN_STATUS_FAILED,
    RUN_STATUS_RUNNING,
    RUN_STATUS_SUCCEEDED,
    AuthorityMatch,
    Run,
    RunRecord,
)
from app.pipeline import authority, marc_ingest

logger = logging.getLogger(__name__)


async def execute_run(
    db: AsyncSession,
    *,
    run: Run,
    upload: bytes,
) -> Run:
    run.status = RUN_STATUS_RUNNING
    await db.flush()

    try:
        records = marc_ingest.parse_marc_upload(upload)
    except ValueError as exc:
        run.status = RUN_STATUS_FAILED
        run.error = f"MARC parse failed: {exc}"
        run.completed_at = datetime.now(timezone.utc)
        await db.commit()
        return run

    # Persist records.
    seen_cn: set[str] = set()
    for rec in records:
        cn = rec["_control_number"]
        if cn in seen_cn:
            continue
        seen_cn.add(cn)
        db.add(RunRecord(run_id=run.id, control_number=cn, marc=rec))
    run.record_count = len(seen_cn)
    await db.flush()

    # Resolve authority candidates per record.
    matcher = authority.get_default_matcher()
    match_count = 0
    for rec in records:
        entities = marc_ingest.extract_named_entities(rec)
        for entity in entities:
            try:
                candidates = await matcher.match(entity, rec)
            except Exception as exc:  # noqa: BLE001 — never let one bad entity kill the run
                logger.exception("authority match failed for %s", entity.get("text"))
                candidates = []
            for c in candidates:
                db.add(
                    AuthorityMatch(
                        run_id=run.id,
                        control_number=rec["_control_number"],
                        entity_text=entity["text"],
                        entity_kind=entity.get("kind", "person"),
                        role=entity.get("role", ""),
                        matched_name=c.matched_name,
                        mazal_id=c.mazal_id,
                        viaf_id=c.viaf_id,
                        wikidata_qid=c.wikidata_qid,
                        confidence=c.confidence,
                        source=c.source,
                        payload=c.payload,
                    )
                )
                match_count += 1
    run.match_count = match_count

    run.status = RUN_STATUS_SUCCEEDED
    run.completed_at = datetime.now(timezone.utc)
    await db.commit()
    return run


async def get_run_or_404(db: AsyncSession, run_id: uuid.UUID) -> Run:
    from fastapi import HTTPException, status as http_status
    from sqlalchemy import select

    r = (await db.execute(select(Run).where(Run.id == run_id))).scalar_one_or_none()
    if r is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Run not found")
    return r


def serialise_match(m: AuthorityMatch) -> dict[str, Any]:
    return {
        "id": str(m.id),
        "control_number": m.control_number,
        "entity_text": m.entity_text,
        "entity_kind": m.entity_kind,
        "role": m.role,
        "matched_name": m.matched_name,
        "mazal_id": m.mazal_id,
        "viaf_id": m.viaf_id,
        "wikidata_qid": m.wikidata_qid,
        "confidence": m.confidence,
        "source": m.source,
        "payload": m.payload or {},
        "approved": m.approved,
        "approved_by": str(m.approved_by) if m.approved_by else None,
        "approved_at": m.approved_at.isoformat() if m.approved_at else None,
    }
