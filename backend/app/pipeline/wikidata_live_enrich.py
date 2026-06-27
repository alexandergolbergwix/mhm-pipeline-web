"""Attach live Wikidata compare snapshots to Studio items for AI autofix."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.pipeline.wikidata_entity_compare import build_compare, fetch_wikidata_entity

logger = logging.getLogger(__name__)

_MAX_CONCURRENT = 5


async def enrich_item_with_wikidata_live(item: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of *item* with ``wikidata_live`` when a QID exists."""
    qid = str(item.get("existing_qid") or "").strip()
    if not qid:
        return item
    out = dict(item)
    try:
        live = await fetch_wikidata_entity(qid)
        compare = build_compare(out, live, qid)
        out["wikidata_live"] = {
            "qid": compare.qid,
            "labels": compare.wikidata.labels,
            "descriptions": compare.wikidata.descriptions,
            "statement_count": compare.wikidata.statement_count,
            "rows": [row.model_dump() for row in compare.rows],
            "has_conflicts": compare.has_conflicts,
            "conflict_count": compare.conflict_count,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("wikidata_live enrich failed for %s (%s): %s", qid, item.get("local_id"), exc)
        out["wikidata_live"] = {"qid": qid, "error": str(exc)}
    return out


async def enrich_items_with_wikidata_live(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fetch live Wikidata + diff rows for every item that carries a QID."""
    sem = asyncio.Semaphore(_MAX_CONCURRENT)

    async def _one(raw: dict[str, Any]) -> dict[str, Any]:
        async with sem:
            return await enrich_item_with_wikidata_live(raw)

    return list(await asyncio.gather(*[_one(it) for it in items]))
