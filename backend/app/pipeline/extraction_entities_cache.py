"""Run-scoped cache helpers for GET /extraction/entities."""

from __future__ import annotations

import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.scoped_cache import invalidate_scope
from app.models.extraction_approval import ExtractionApproval
from app.pipeline.inference_cache import canonical_hash

ENTITIES_CACHE_KIND = "extraction.entities"


async def compute_entities_fingerprint(
    db: AsyncSession,
    run_id: uuid.UUID,
    ner_path: Path,
) -> str:
    """Fingerprint everything that affects the unfiltered entity list."""
    rows = (
        await db.execute(
            select(
                ExtractionApproval.id,
                ExtractionApproval.override_text,
                ExtractionApproval.override_type,
                ExtractionApproval.override_role,
                ExtractionApproval.approved,
                ExtractionApproval.ai_verdict,
                ExtractionApproval.updated_at,
            ).where(ExtractionApproval.run_id == run_id)
        )
    ).all()
    snapshot = [
        {
            "id":            str(r.id),
            "override_text": r.override_text,
            "override_type": r.override_type,
            "override_role": r.override_role,
            "approved":      bool(r.approved),
            "ai_verdict":    r.ai_verdict,
            "updated_at":    r.updated_at.isoformat() if r.updated_at else None,
        }
        for r in sorted(rows, key=lambda row: str(row.id))
    ]
    mtime_ns = int(ner_path.stat().st_mtime_ns) if ner_path.exists() else 0
    return canonical_hash({
        "run_id":     str(run_id),
        "approvals":  snapshot,
        "ner_mtime_ns": mtime_ns,
    })


def entities_etag(fingerprint: str) -> str:
    return f'"{fingerprint}"'


async def invalidate_entities_cache(run_id: uuid.UUID) -> None:
    await invalidate_scope("run", str(run_id), kind=ENTITIES_CACHE_KIND)


def entities_cacheable(
    *,
    source: str | None,
    type_filter: str | None,
    role_filter: str | None,
    approved: bool | None,
    search: str | None,
    sort_by: str | None,
    page: int | None,
    page_size: int | None,
) -> bool:
    """Only cache the full unfiltered list (the entity table poll)."""
    return not any([
        source, type_filter, role_filter, approved is not None,
        search, sort_by, page is not None, page_size is not None,
    ])
