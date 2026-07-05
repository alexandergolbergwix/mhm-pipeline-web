"""Attach live Wikibase Cloud compare snapshots for HMO item AI autofix."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.pipeline.wikidata_entity_compare import build_compare
from app.services.wikibase_credentials import build_server_wikibase_writer

logger = logging.getLogger(__name__)

_MAX_CONCURRENT = 5


def _claims_as_statements(item: dict[str, Any]) -> dict[str, Any]:
    out = dict(item)
    claims = out.get("claims") or []
    out["statements"] = [
        {
            "property_id": c.get("property_id"),
            "property": c.get("property_id"),
            "value": c.get("value"),
            "value_id": c.get("value") if c.get("datatype") == "wikibase-item" else None,
            "datatype": c.get("datatype"),
        }
        for c in claims
        if isinstance(c, dict)
    ]
    out["existing_qid"] = out.get("wikibase_id")
    return out


async def enrich_hmo_item_with_wikibase_live(item: dict[str, Any]) -> dict[str, Any]:
    qid = str(item.get("wikibase_id") or "").strip()
    if not qid:
        return item
    out = _claims_as_statements(item)
    try:
        writer = build_server_wikibase_writer()
        live = await asyncio.to_thread(writer.get_entity, qid)
        if not live:
            raise LookupError(f"Wikibase entity {qid} not found")
        compare = build_compare(out, live, qid)
        out["wikibase_live"] = {
            "qid": compare.qid,
            "labels": compare.wikidata.labels,
            "descriptions": compare.wikidata.descriptions,
            "statement_count": compare.wikidata.statement_count,
            "rows": [row.model_dump() for row in compare.rows],
            "has_conflicts": compare.has_conflicts,
            "conflict_count": compare.conflict_count,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "wikibase_live enrich failed for %s (%s): %s",
            qid, item.get("local_id"), exc,
        )
        out["wikibase_live"] = {"qid": qid, "error": str(exc)}
    return out


async def enrich_hmo_items_with_wikibase_live(
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    sem = asyncio.Semaphore(_MAX_CONCURRENT)

    async def _one(raw: dict[str, Any]) -> dict[str, Any]:
        async with sem:
            return await enrich_hmo_item_with_wikibase_live(raw)

    return list(await asyncio.gather(*[_one(it) for it in items]))
