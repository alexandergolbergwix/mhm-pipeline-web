"""SQL-paginated views over ``wikidata_studio_item_rows``.

The review read path used to slice the whole
``wikidata_studio_cache.result_items`` JSONB blob in Python — first
page request deserialised tens of MB, filtering and sorting ran over
the full corpus, and bulk loads walked integer pages of it. These
views move the read to per-item rows: SQL keyset pagination (cursor,
not OFFSET), streaming row iterators for exports, and a lazy backfill
for runs built before the table existed.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.item_override import WikidataItemOverride
from app.models.wikidata_studio_item_row import WikidataStudioItemRow
from app.pipeline.wikidata_item_merge import apply_wikidata_item_override, override_row_to_dict
from app.pipeline.wikidata_item_views import (
    _merge_one_wikidata_item,
    trim_studio_list_item,
)

logger = logging.getLogger(__name__)

_PAGE_MERGE_BATCH = 200


def _row_values(ord_index: int, item: dict[str, Any]) -> dict[str, Any]:
    """Indexed columns for one built item dict (payload stays verbatim)."""
    labels = item.get("labels") if isinstance(item.get("labels"), dict) else {}
    label_en = str(labels.get("en") or "")[:600] or None
    label_he = str(labels.get("he") or "")[:600] or None
    local_id = str(item.get("local_id") or "")
    label_sort = (label_en or label_he or local_id).lower()[:650]
    record_ids = item.get("record_ids") or item.get("records") or []
    return {
        "local_id": local_id[:256] or f"item::{ord_index}",
        "ord": ord_index,
        "payload": item,
        "label_en": label_en,
        "label_he": label_he,
        "label_sort": label_sort,
        "entity_type": str(item.get("entity_type") or "")[:64],
        "existing_qid": str(item.get("existing_qid") or "")[:32],
        "source_uri": str(item.get("source_uri") or "")[:600],
        "record_ids": [str(r) for r in record_ids if r],
        "statement_count": len(item.get("statements") or []),
    }


async def replace_wikidata_item_rows(
    db: AsyncSession,
    run_id: uuid.UUID,
    *,
    approved_only: bool,
    source: str,
    items: list[dict[str, Any]],
) -> int:
    """Replace the rows for one build key with the freshly built items.

    Called right after the cache-row upsert so the table always matches
    the served cache.     Failure is logged, never raised — the blob cache
    remains the fallback read path.
    """
    from app.pipeline.hmo_canonical_wikidata import filter_public_wikidata_items  # noqa: PLC0415

    # Non-public rows (canonical HMO ontology internals) must never reach
    # the review table, exports, or bulk scopes — same gate as the merged
    # view. Filtering at write time keeps counts and cursors consistent.
    try:
        items = filter_public_wikidata_items(items or [], source=source)
    except Exception:
        logger.exception("public-item filter failed; writing rows unfiltered")
    try:
        await db.execute(delete(WikidataStudioItemRow).where(
            WikidataStudioItemRow.run_id == run_id,
            WikidataStudioItemRow.approved_only.is_(approved_only),
            WikidataStudioItemRow.source == source,
        ))
        for idx, item in enumerate(items or []):
            db.add(WikidataStudioItemRow(
                run_id=run_id,
                approved_only=approved_only,
                source=source,
                **_row_values(idx, item),
            ))
        await db.commit()
        return len(items or [])
    except Exception:
        logger.exception(
            "wikidata-studio item rows write failed for run %s (%s/%s)",
            run_id, approved_only, source,
        )
        await db.rollback()
        return 0


async def ensure_rows_backfilled(
    db: AsyncSession,
    run_id: uuid.UUID,
    *,
    approved_only: bool = True,
    source: str = "legacy",
) -> int:
    """Backfill rows from the cache blob once, for pre-table builds.

    Returns the row count for the build key. A missing cache row is a
    :class:`LookupError` — callers answer 409 (no build exists).
    """
    from app.models.wikidata_studio_cache import WikidataStudioCache  # noqa: PLC0415
    from app.pipeline.hmo_canonical_wikidata import filter_public_wikidata_items  # noqa: PLC0415

    cache_row = (
        await db.execute(
            select(WikidataStudioCache).where(
                WikidataStudioCache.run_id == run_id,
                WikidataStudioCache.approved_only == approved_only,
                WikidataStudioCache.source == source,
            )
        )
    ).scalar_one_or_none()
    if cache_row is None:
        raise LookupError(f"No Wikidata Studio build exists for run {run_id}.")

    count = int(await db.scalar(
        select(func.count()).select_from(WikidataStudioItemRow).where(
            WikidataStudioItemRow.run_id == run_id,
            WikidataStudioItemRow.approved_only.is_(approved_only),
            WikidataStudioItemRow.source == source,
        )
    ) or 0)
    if count:
        return count

    items = filter_public_wikidata_items(
        cache_row.result_items or [], source=source,
    )
    return await replace_wikidata_item_rows(
        db, run_id, approved_only=approved_only, source=source, items=items,
    )


# ── Cursor-paginated page ───────────────────────────────────────────────


def _encode_cursor(sort_val: str, local_id: str) -> str:
    import base64  # noqa: PLC0415

    return base64.urlsafe_b64encode(f"{sort_val}\x1f{local_id}".encode()).decode("ascii")


def _decode_cursor(cursor: str) -> tuple[str, str]:
    if not cursor:
        return "", ""
    import base64  # noqa: PLC0415

    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode("utf-8")
        sort_val, _, local_id = raw.partition("\x1f")
        return sort_val, local_id
    except Exception:  # noqa: BLE001 — bad cursor = start from the top
        return "", ""


async def page_wikidata_items(
    db: AsyncSession,
    run_id: uuid.UUID,
    *,
    cursor: str = "",
    limit: int = 25,
    q: str = "",
    entity_type: str = "",
    sort: str = "label",
    dir: str = "asc",
    approved_only: bool = True,
    source: str = "legacy",
    include_total: bool = True,
) -> dict[str, Any]:
    """One cursor page of review-table items, entirely SQL-side.

    Keyset pagination on ``(sort_value, local_id)`` — stable under
    concurrent edits, and the payload never scales with the corpus.
    Per-page items merge with curator overrides + verdicts + upload
    state exactly like the legacy merged view, in Python, for
    ``limit`` items only.
    """
    from app.models.wikibase_cloud_write import (  # noqa: PLC0415
        CHANNEL_WIKIDATA_UPLOAD,
        TARGET_ITEM,
    )
    from app.pipeline.wikidata_qid_ledger import load_global_ledger  # noqa: PLC0415
    from app.services.wikibase_audit import fetch_latest_wikibase_writes  # noqa: PLC0415

    sort_col = (
        WikidataStudioItemRow.local_id
        if sort == "local_id"
        else WikidataStudioItemRow.label_sort
    )
    descending = dir == "desc"
    is_postgres = db.get_bind().dialect.name == "postgresql"

    conditions = [
        WikidataStudioItemRow.run_id == run_id,
        WikidataStudioItemRow.approved_only.is_(approved_only),
        WikidataStudioItemRow.source == source,
    ]
    if entity_type and entity_type != "all":
        conditions.append(WikidataStudioItemRow.entity_type == entity_type)
    needle = q.strip().lower()
    if needle:
        like = f"%{needle}%"
        conditions.append(or_(
            WikidataStudioItemRow.label_sort.ilike(like),
            WikidataStudioItemRow.local_id.ilike(like),
            WikidataStudioItemRow.existing_qid.ilike(like),
            # JSONB text search is Postgres-only; SQLite tests match on
            # the indexed columns.
            WikidataStudioItemRow.payload["descriptions"].astext.ilike(like),
        ) if is_postgres else or_(
            WikidataStudioItemRow.label_sort.ilike(like),
            WikidataStudioItemRow.local_id.ilike(like),
            WikidataStudioItemRow.existing_qid.ilike(like),
        ))

    cursor_sort, cursor_local = _decode_cursor(cursor)
    if cursor_sort or cursor_local:
        if descending:
            conditions.append(or_(
                sort_col < cursor_sort,
                (sort_col == cursor_sort) & (WikidataStudioItemRow.local_id < cursor_local),
            ))
        else:
            conditions.append(or_(
                sort_col > cursor_sort,
                (sort_col == cursor_sort) & (WikidataStudioItemRow.local_id > cursor_local),
            ))

    base = (
        select(WikidataStudioItemRow, WikidataItemOverride)
        .join(
            WikidataItemOverride,
            (WikidataItemOverride.run_id == WikidataStudioItemRow.run_id)
            & (WikidataItemOverride.local_id == WikidataStudioItemRow.local_id),
            isouter=True,
        )
        .where(*conditions)
    )
    order = (
        (sort_col.desc(), WikidataStudioItemRow.local_id.desc())
        if descending
        else (sort_col.asc(), WikidataStudioItemRow.local_id.asc())
    )
    page_rows = (await db.execute(base.order_by(*order).limit(limit + 1))).all()

    has_more = len(page_rows) > limit
    page_rows = page_rows[:limit]

    ledger = await load_global_ledger(db)
    latest_writes = await fetch_latest_wikibase_writes(
        db, run_id, channel=CHANNEL_WIKIDATA_UPLOAD, target_kind=TARGET_ITEM,
    )

    items: list[dict[str, Any]] = []
    last_sort = ""
    last_local = ""
    for row, ov_row in page_rows:
        raw = dict(row.payload or {})
        merged = _merge_one_wikidata_item(
            raw, ov_row=ov_row, ledger=ledger, latest_writes=latest_writes,
        )
        items.append(trim_studio_list_item(merged))
        last_sort = str(
            row.local_id if sort == "local_id" else row.label_sort,
        )
        last_local = str(row.local_id)

    total = None
    approved_count = None
    by_type: dict[str, int] | None = None
    if include_total:
        scope_conditions = [
            WikidataStudioItemRow.run_id == run_id,
            WikidataStudioItemRow.approved_only.is_(approved_only),
            WikidataStudioItemRow.source == source,
        ]
        total = int(await db.scalar(
            select(func.count()).select_from(WikidataStudioItemRow).where(*conditions)
        ) or 0)
        # Header aggregates, computed in SQL — the legacy in-memory slice
        # derived them from the whole blob (the H12 this table removes).
        approved_count = int(await db.scalar(
            select(func.count())
            .select_from(WikidataStudioItemRow)
            .join(
                WikidataItemOverride,
                (WikidataItemOverride.run_id == WikidataStudioItemRow.run_id)
                & (WikidataItemOverride.local_id == WikidataStudioItemRow.local_id),
            )
            .where(*scope_conditions, WikidataItemOverride.approved.is_(True))
        ) or 0)
        by_type = {
            str(row[0] or ""): int(row[1])
            for row in (
                await db.execute(
                    select(
                        WikidataStudioItemRow.entity_type, func.count(),
                    ).where(*scope_conditions).group_by(WikidataStudioItemRow.entity_type)
                )
            ).all()
        }
    return {
        "run_id": str(run_id),
        "limit": limit,
        "has_more": has_more,
        "next_cursor": _encode_cursor(last_sort, last_local) if has_more else None,
        "total": total,
        "approved_count": approved_count,
        "by_type": by_type,
        "items": items,
    }


# ── Streaming row iteration (exports — Rule W-247) ──────────────────────


def _apply_override_to_raw(
    raw: dict[str, Any], ov_row: Any,
) -> dict[str, Any]:
    return (
        apply_wikidata_item_override(raw, override_row_to_dict(ov_row))
        if ov_row is not None
        else raw
    )


async def iter_wikidata_row_payloads(
    run_id: uuid.UUID,
    *,
    approved_only: bool = True,
    source: str = "legacy",
) -> AsyncIterator[list[dict[str, Any]]]:
    """Yield partitions of raw row payloads in build order.

    Owns its own short-lived session windows — a streamed export must
    never hold a request-scoped session for the whole response
    (Rule W-247). Each partition is one merge batch.
    """
    from app.db import session_scope  # noqa: PLC0415

    async with session_scope() as db:
        result = await db.stream(
            select(WikidataStudioItemRow.payload)
            .where(
                WikidataStudioItemRow.run_id == run_id,
                WikidataStudioItemRow.approved_only.is_(approved_only),
                WikidataStudioItemRow.source == source,
            )
            .order_by(WikidataStudioItemRow.ord.asc()),
            execution_options={"yield_per": _PAGE_MERGE_BATCH},
        )
        async for partition in result.partitions():
            yield [dict(p) for (p,) in partition]
