"""Scope loading for rule-verify runs: merged items + MARC index."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.pipeline.hmo_item_views import (
    ItemBuildMissingError,
    fetch_merged_hmo_items_cached,
    item_label,
)
from app.pipeline.marc_verify_context import (
    attach_marc_context,
    load_run_marc_records,
)

logger = logging.getLogger(__name__)


async def load_rule_verify_scope(
    db: AsyncSession,
    run_id: Any,
    *,
    item_ids: list[str] | None = None,
    with_marc: bool = True,
) -> list[dict[str, Any]]:
    """Override-merged HMO items, ready for the rule engine.

    Raises ``ItemBuildMissingError`` when the run has no item build — the
    caller maps that to HTTP 409 like every other Studio surface.
    """
    items = await fetch_merged_hmo_items_cached(db, run_id)
    wanted = {str(x) for x in (item_ids or [])}
    out: list[dict[str, Any]] = []
    for raw in items:
        local_id = str(raw.get("local_id") or "")
        if wanted and local_id not in wanted:
            continue
        item = dict(raw)
        item["_local_id"] = local_id
        item["label"] = item_label(item)
        out.append(item)
    if with_marc:
        marc_records = await load_run_marc_records(db, run_id)
        attach_marc_context(out, marc_records)
    return out


def build_context(  # noqa: ANN201 — dataclass return, see rule_verify.context
    *,
    run_id: str,
    items: list[dict[str, Any]],
    api_enabled: bool,
    fetcher: Any = None,
    wikibase_endpoint: str = "",
    on_progress: Any = None,
):
    """RuleContext with the within-run duplicate index precomputed."""
    from app.pipeline.rule_verify.context import RuleContext
    from app.pipeline.rule_verify.rules.hmo import in_run_dup_index

    return RuleContext(
        run_id=run_id,
        api_enabled=api_enabled,
        fetcher=fetcher,
        on_progress=on_progress,
        in_run_dup_index=in_run_dup_index(items),
    )


__all__ = [
    "ItemBuildMissingError",
    "build_context",
    "load_rule_verify_scope",
]
