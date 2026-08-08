"""Verify-scope cache partitioning — one implementation, two callers (Rule W-167).

The stream route (`start_wikidata_verify`) and the background job worker
(`verify_job`) both decide which items are already judged and which go to the
judge. That loop was copy-pasted, so a rule added to one path was silently absent
from the other — which is exactly how a re-judge trigger would end up existing
only in the interactive path and never in the job the curator actually runs.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.pipeline.inference_cache import read_from_inference_cache
from app.pipeline.wikidata_verdict_cache import (
    cached_verdict_is_sticky_full,
    cached_verdict_needs_duplicate_rejudge,
    wikidata_verdict_query_summary,
)

logger = logging.getLogger(__name__)


def _sticky_source_from_item(item: dict[str, Any]) -> dict[str, Any] | None:
    """Prior full verdict carried on the item (override merge / previous verify)."""
    for key in ("ai_verdict", "_sticky_ai_verdict"):
        raw = item.get(key)
        if isinstance(raw, dict) and raw:
            return raw
    return None


async def partition_wikidata_verify_cache(
    db: AsyncSession,
    items: list[dict[str, Any]],
    *,
    judge_model: str,
    evaluator_id: str,
    override_cache: bool,
) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]], list[dict[str, Any]], dict[str, int]]:
    """Split *items* into cache hits and items that must be judged.

    Returns ``(pre_cached, uncached, stats)``. A hit whose stored verdict was
    judged while the duplicate probe was inconclusive, and whose probe has since
    answered, is returned as *uncached* so it is judged again (Rule W-157).

    Sticky-full (Rule W-171): when the inference cache misses but the item still
    carries a prior ``full``/``pass`` verdict whose claim fingerprint matches,
    reuse it instead of re-judging — unless ``override_cache`` is set.
    """
    stats = {
        "cached": 0,
        "uncached": 0,
        "rejudge_duplicate_resolved": 0,
        "sticky_full": 0,
    }
    if override_cache:
        stats["uncached"] = len(items)
        return [], list(items), stats

    pre_cached: list[tuple[dict[str, Any], dict[str, Any]]] = []
    uncached: list[dict[str, Any]] = []
    for item in items:
        hit = await read_from_inference_cache(
            db,
            kind="ai_verdict",
            query_summary=wikidata_verdict_query_summary(
                item, judge_model, evaluator=evaluator_id,
            ),
        )
        if hit is not None:
            if cached_verdict_needs_duplicate_rejudge(hit, item):
                stats["rejudge_duplicate_resolved"] += 1
                uncached.append(item)
                continue
            pre_cached.append((item, hit))
            continue

        sticky = _sticky_source_from_item(item)
        if sticky is not None and cached_verdict_is_sticky_full(
            sticky,
            item,
            judge_model=judge_model,
            evaluator_id=evaluator_id,
        ):
            from app.pipeline.wikidata_verdict_cache import (  # noqa: PLC0415
                _verdict_envelope,
            )

            envelope = _verdict_envelope(sticky)
            pre_cached.append((item, envelope))
            stats["sticky_full"] += 1
            continue

        uncached.append(item)

    stats["cached"] = len(pre_cached)
    stats["uncached"] = len(uncached)
    if stats["rejudge_duplicate_resolved"]:
        logger.info(
            "wikidata verify scope: re-judging %s item(s) whose duplicate check "
            "resolved since the stored verdict",
            stats["rejudge_duplicate_resolved"],
        )
    if stats["sticky_full"]:
        logger.info(
            "wikidata verify scope: sticky-full reused %s prior full verdict(s)",
            stats["sticky_full"],
        )
    return pre_cached, uncached, stats
