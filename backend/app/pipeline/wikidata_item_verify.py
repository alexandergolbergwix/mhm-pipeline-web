"""AI verification persistence for Wikidata Studio items."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select

from app.pipeline.inference_cache import write_to_inference_cache
from app.pipeline.wikidata_verdict_cache import (
    wikidata_verdict_input_fingerprint,
    wikidata_verdict_query_summary,
)

logger = logging.getLogger(__name__)


async def _persist_wikidata_verdicts_to_overrides(
    *,
    run_id: UUID,
    items_by_id: dict[str, dict[str, Any]],
    verdicts: list[dict[str, Any]],
    judge_model: str,
    marc_records: list[dict[str, Any]] | None = None,
) -> None:
    from app.db import session_scope  # noqa: PLC0415
    from app.models.item_override import WikidataItemOverride  # noqa: PLC0415
    from app.pipeline.wikidata_verdict_cache import marc_context_for_wikidata_item  # noqa: PLC0415

    now = datetime.now(timezone.utc)
    async with session_scope() as db:
        for v in verdicts:
            cand = v.get("candidate") if isinstance(v, dict) else None
            local_id = ""
            if isinstance(cand, dict):
                local_id = str(
                    cand.get("_local_id") or cand.get("_item_id")
                    or cand.get("local_id") or "",
                )
            item = items_by_id.get(local_id)
            if item is None:
                continue
            model = str(v.get("judge_id") or v.get("model") or judge_model)
            evaluator_id = str(
                v.get("evaluator_id") or v.get("evaluator") or "wikidata_item",
            )
            verdict_body = v.get("verdict") or {}
            marc_ctx = item.get("_marc_context")
            if not isinstance(marc_ctx, dict):
                marc_ctx = marc_context_for_wikidata_item(item, marc_records or [])
            fingerprint = wikidata_verdict_input_fingerprint(
                item,
                model,
                evaluator=evaluator_id,
                marc_context=marc_ctx if isinstance(marc_ctx, dict) else None,
            )
            summary: dict[str, Any] = {
                "overall": verdict_body.get("overall") or "unknown",
                "name_ok": verdict_body.get("name_ok"),
                "type_ok": verdict_body.get("type_ok"),
                "role_ok": verdict_body.get("role_ok"),
                "reasoning": verdict_body.get("reasoning"),
                "model": model,
                "judged_at": v.get("judged_at"),
                "cache_key": fingerprint,
                "session_id": None,
                "evaluator": evaluator_id,
            }
            if evaluator_id == "wikidata_autofix":
                fixes = verdict_body.get("suggested_fixes") or cand.get("suggested_fixes")
                if fixes:
                    summary["suggested_fixes"] = fixes

            row = (
                await db.execute(
                    select(WikidataItemOverride).where(
                        WikidataItemOverride.run_id == run_id,
                        WikidataItemOverride.local_id == local_id,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                row = WikidataItemOverride(run_id=run_id, local_id=local_id)
                db.add(row)
            row.ai_verdict = summary
            row.ai_verdict_at = now

            cached_result = {
                "verdict": verdict_body,
                "judge_id": model,
                "judged_at": v.get("judged_at"),
                "cache_key": fingerprint,
                "evaluator": evaluator_id,
            }
            await write_to_inference_cache(
                db,
                kind="ai_verdict",
                query_summary=wikidata_verdict_query_summary(
                    item, model, evaluator=evaluator_id,
                    marc_context=marc_ctx if isinstance(marc_ctx, dict) else None,
                ),
                result=cached_result,
            )
        await db.commit()
