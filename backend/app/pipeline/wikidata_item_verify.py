"""AI verification persistence for Wikidata Studio items."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select

from app.pipeline.inference_cache import write_to_inference_cache
from app.pipeline.wikidata_duplicate_probe import (
    duplicate_class_for_item,
    duplicate_status_for_item,
)
from app.pipeline.wikidata_verdict_cache import (
    WIKIDATA_VERDICT_KEY_VERSION,
    wikidata_verdict_input_fingerprint,
    wikidata_verdict_query_summary,
    wikidata_verdict_stable_input_fingerprint,
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
                "stable_cache_key": wikidata_verdict_stable_input_fingerprint(
                    item, model, evaluator=evaluator_id,
                ),
                "cache_key_version": WIKIDATA_VERDICT_KEY_VERSION,
                "session_id": None,
                "evaluator": evaluator_id,
                # Recorded, never keyed (Rule W-157): a verdict judged while the
                # probe was inconclusive must be re-judged once it answers, and
                # the fingerprint cannot carry that without going stale everywhere.
                "duplicate_status": duplicate_status_for_item(item),
                "duplicate_class": duplicate_class_for_item(item),
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
                "verdict": {
                    **verdict_body,
                    "duplicate_status": summary["duplicate_status"],
                    "duplicate_class": summary["duplicate_class"],
                },
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


class WikidataVerdictPersistBatch:
    """Batch override/cache writes during verify streams (Rule W-131).

    Flushes run in the background so Postgres I/O never blocks reading the
    eval-agent subprocess stdout (pipe backpressure stalls the judge).
    """

    def __init__(
        self,
        *,
        run_id: UUID,
        items_by_id: dict[str, dict[str, Any]],
        judge_model: str,
        marc_records: list[dict[str, Any]] | None = None,
        flush_size: int = 5,
        flush_interval_s: float = 1.0,
    ) -> None:
        import time  # noqa: PLC0415

        self._run_id = run_id
        self._items_by_id = items_by_id
        self._judge_model = judge_model
        self._marc_records = marc_records
        self._flush_size = flush_size
        self._flush_interval_s = flush_interval_s
        self._buffer: list[dict[str, Any]] = []
        self._last_flush = time.monotonic()
        self._flush_lock = asyncio.Lock()
        self._pending_flush: asyncio.Task[None] | None = None

    def enqueue(self, payload: dict[str, Any]) -> None:
        import time  # noqa: PLC0415

        self._buffer.append(payload)
        now = time.monotonic()
        if (
            len(self._buffer) >= self._flush_size
            or (now - self._last_flush) >= self._flush_interval_s
        ):
            self._schedule_flush()

    def _schedule_flush(self) -> None:
        if self._pending_flush is not None and not self._pending_flush.done():
            return
        self._pending_flush = asyncio.create_task(self._flush_guarded())

    async def _flush_guarded(self) -> None:
        async with self._flush_lock:
            await self.flush()

    async def add(self, payload: dict[str, Any]) -> None:
        self.enqueue(payload)

    async def flush(self) -> None:
        import time  # noqa: PLC0415

        if not self._buffer:
            return
        batch = self._buffer
        self._buffer = []
        self._last_flush = time.monotonic()
        await _persist_wikidata_verdicts_to_overrides(
            run_id=self._run_id,
            items_by_id=self._items_by_id,
            verdicts=batch,
            judge_model=self._judge_model,
            marc_records=self._marc_records,
        )

    async def finish(self) -> None:
        if self._pending_flush is not None:
            try:
                await self._pending_flush
            except Exception:  # noqa: BLE001
                logger.exception("Wikidata verdict background flush failed")
        await self.flush()
