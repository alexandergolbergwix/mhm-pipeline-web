"""Persist per-item rule verdicts to the owning override rows.

Mirrors the AI verdict two-tier contract (Rule W-51) in a deliberately
smaller form: the owning row keeps the full verdict (it is the review
surface), and the job result carries only the aggregated summary. No
inference_cache write for CPU rules — recomputation is cheap and always
fresh, so caching would add staleness risk without saving real time.
API-rule evidence (probe answers) is cached at the API layer instead.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.hmo_studio_item_override import HmoStudioItemOverride
from app.pipeline.rule_verify.base import (
    RULE_STATES,
    RULE_VERDICT_SCHEMA,
    STATE_ERROR,
    STATE_FAIL,
    STATE_NOT_RELEVANT,
    RuleResult,
    worst_state,
)
from app.pipeline.rule_verify.engine import summarise

logger = logging.getLogger(__name__)

_BATCH_COMMIT = 200


def _result_entry(r: RuleResult) -> dict[str, Any]:
    """The compact per-rule JSONB entry: passes carry ``rule_id``+``state``."""
    return {
        "rule_id": r.rule_id,
        "state": r.state,
        **({"field": r.field} if r.field else {}),
        **({"message": r.message} if r.message else {}),
        **({"evidence": r.evidence} if r.evidence else {}),
    }


def api_rule_ids() -> frozenset[str]:
    """The rule ids whose entries an API pass owns (Rule W-256)."""
    from app.pipeline.rule_verify.rules.hmo import build_hmo_rules

    return frozenset(r.id for r in build_hmo_rules() if r.uses_api)


def build_rule_verdict(
    results: list[RuleResult],
    *,
    job_id: str | None = None,
    checked_at: str | None = None,
    label: str = "",
    class_qid: str = "",
) -> dict[str, Any]:
    """The JSONB stored on the override row — compact, per-rule, per-item.

    Pass results store only ``rule_id`` + ``state``; failures and errors
    carry field, message, and evidence. ``label``/``class_qid`` snapshots
    let the results endpoints filter, search, and paginate purely in SQL
    — no merged-view deserialise on the request path.
    """
    states = [r.state for r in results if r.state in RULE_STATES]
    return {
        "schema": RULE_VERDICT_SCHEMA,
        "overall": worst_state(states),
        "error_count": sum(1 for s in states if s == "error"),
        "fail_count": sum(1 for s in states if s == "fail"),
        "checked_at": checked_at or datetime.now(UTC).isoformat(),
        "job_id": job_id,
        "label": label,
        "class_qid": class_qid,
        "results": [_result_entry(r) for r in results],
    }


async def persist_rule_verdicts(
    db: AsyncSession,
    *,
    run_id: Any,
    results_by_local_id: dict[str, list[RuleResult]],
    job_id: str | None = None,
    items_by_id: dict[str, dict[str, Any]] | None = None,
    on_batch: Any = None,
) -> int:
    """Write ``rule_verdict`` for every local_id with results. Returns count.

    ``items_by_id`` supplies the label/class_qid snapshots stored beside
    the results so the results endpoints filter and paginate in SQL.
    """
    local_ids = [lid for lid in results_by_local_id if lid]
    if not local_ids:
        return 0
    items_by_id = items_by_id or {}
    written = 0
    for start in range(0, len(local_ids), _BATCH_COMMIT):
        chunk = local_ids[start : start + _BATCH_COMMIT]
        rows = (
            await db.execute(
                select(HmoStudioItemOverride).where(
                    HmoStudioItemOverride.run_id == run_id,
                    HmoStudioItemOverride.local_id.in_(chunk),
                )
            )
        ).scalars().all()
        by_id = {r.local_id: r for r in rows}
        now = datetime.now(UTC)
        for local_id in chunk:
            item = items_by_id.get(local_id) or {}
            row = by_id.get(local_id)
            if row is None:
                row = HmoStudioItemOverride(run_id=run_id, local_id=local_id)
                db.add(row)
            row.rule_verdict = build_rule_verdict(
                results_by_local_id[local_id],
                job_id=job_id,
                label=str(item.get("label") or item.get("_label") or "")[:300],
                class_qid=str(item.get("class_qid") or ""),
            )
            row.rule_verdict_at = now
            written += 1
        await db.commit()
        if on_batch is not None:
            await on_batch(written)
    return written


async def merge_api_rule_verdicts(
    db: AsyncSession,
    *,
    run_id: Any,
    results_by_local_id: dict[str, list[RuleResult]],
    job_id: str | None = None,
    items_by_id: dict[str, Any] | None = None,
) -> int:
    """Merge API-pass results into the verdict rows a CPU pass wrote.

    The shard path persists CPU-only verdicts (Rule W-256); this pass
    replaces the API-rule entries and recomputes the rollups so the row
    never mixes a stale API answer with fresh CPU answers. A row the
    CPU pass never wrote gets a fresh verdict from the API results alone
    (fail-closed: its rollup then reflects only what actually ran).
    """
    api_ids = api_rule_ids()
    local_ids = [lid for lid in results_by_local_id if lid]
    if not local_ids:
        return 0
    items_by_id = items_by_id or {}
    merged = 0
    for start in range(0, len(local_ids), _BATCH_COMMIT):
        chunk = local_ids[start : start + _BATCH_COMMIT]
        rows = (
            await db.execute(
                select(HmoStudioItemOverride).where(
                    HmoStudioItemOverride.run_id == run_id,
                    HmoStudioItemOverride.local_id.in_(chunk),
                )
            )
        ).scalars().all()
        by_id = {r.local_id: r for r in rows}
        now = datetime.now(UTC)
        for local_id in chunk:
            item = items_by_id.get(local_id) or {}
            row = by_id.get(local_id)
            if row is None:
                row = HmoStudioItemOverride(run_id=run_id, local_id=local_id)
                db.add(row)
            verdict = dict(row.rule_verdict) if isinstance(row.rule_verdict, dict) else {}
            kept = [
                entry for entry in (verdict.get("results") or [])
                if isinstance(entry, dict)
                and str(entry.get("rule_id") or "") not in api_ids
            ]
            entries = kept + [_result_entry(r) for r in results_by_local_id[local_id]]
            states = [
                str(entry.get("state") or "")
                for entry in entries
                if entry.get("state") in RULE_STATES
            ]
            verdict.update({
                "schema": RULE_VERDICT_SCHEMA,
                "overall": worst_state(states),
                "error_count": sum(1 for s in states if s == STATE_ERROR),
                "fail_count": sum(1 for s in states if s == STATE_FAIL),
                "checked_at": now.isoformat(),
                "job_id": job_id,
                "label": (
                    str(item.get("label") or item.get("_label") or "")[:300]
                    or str(verdict.get("label") or "")[:300]
                ),
                "class_qid": (
                    str(item.get("class_qid") or "")
                    or str(verdict.get("class_qid") or "")
                ),
                "results": entries,
            })
            row.rule_verdict = verdict
            row.rule_verdict_at = now
            merged += 1
        await db.commit()
    return merged


def per_rule_tally(
    results_by_local_id: dict[str, list[RuleResult]],
) -> dict[str, dict[str, int]]:
    """Per-rule tallies only — no ``scope``/``overall_counts``.

    The API pass re-reports entities the CPU shards already counted
    (Rule W-256); ``merge_summaries`` adds tallies but must not add a
    second copy of the scope or the per-entity overalls.
    """
    per_rule: dict[str, dict[str, int]] = {}
    for results in results_by_local_id.values():
        for r in results:
            tally = per_rule.setdefault(r.rule_id, {state: 0 for state in RULE_STATES})
            tally[r.state] = tally.get(r.state, 0) + 1
    return per_rule


def compact_rule_verdict(verdict: dict[str, Any] | None) -> dict[str, Any] | None:
    """The list-response shape: rollup + failing rule ids, no per-rule bodies.

    The full verdict (all 30 rules per item) lives on the override row and
    the rule-verify results endpoint. Shipping it in the merged items view
    added ~2 KB x 18k items and R14'd the 512 MB web dyno (2026-09-19).
    """
    if not isinstance(verdict, dict) or not verdict.get("results"):
        return None
    results = [r for r in verdict["results"] if isinstance(r, dict)]
    return {
        "overall": verdict.get("overall"),
        "fail_count": verdict.get("fail_count") or sum(
            1 for r in results if r.get("state") == "fail"
        ),
        "error_count": verdict.get("error_count") or sum(
            1 for r in results if r.get("state") == "error"
        ),
        "failing_rules": [
            str(r.get("rule_id")) for r in results if r.get("state") == "fail"
        ],
        "pass_count": sum(1 for r in results if r.get("state") == "pass"),
        "checked_at": verdict.get("checked_at"),
    }


def summary_from_results(
    results_by_local_id: dict[str, list[RuleResult]],
) -> dict[str, Any]:
    summary = summarise(results_by_local_id)
    entities = summary["per_entity"]
    overall_counts: dict[str, int] = {state: 0 for state in RULE_STATES}
    for entity in entities.values():
        state = str(entity.get("overall") or STATE_NOT_RELEVANT)
        overall_counts[state] = overall_counts.get(state, 0) + 1
    return {
        "scope": len(entities),
        "overall_counts": overall_counts,
        "per_rule": summary["per_rule"],
        "per_entity": entities,
    }
