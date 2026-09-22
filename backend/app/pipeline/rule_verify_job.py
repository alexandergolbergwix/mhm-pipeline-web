"""Background job: rule-based verification of HMO Studio items.

Deterministic, non-AI checks (``app.pipeline.rule_verify``) run over the
override-merged item scope and persist a per-item ``rule_verdict`` to the
owning override rows. Advisory only: nothing here blocks an upload, and
bulk approval treats a rule as blocking only when the curator opted in.

Three execution paths, one engine:

* **Heroku fallback / small scopes** — this module's single-process loop
  with chunked cooperative yields (Rule W-245) and cancel checks. CPU and
  API rules run inline; the probe budget is per 1 000-item chunk, which
  keeps the degraded dyno path bounded.
* **Modal (preferred)** — ``modal_jobs.py`` claims the same job and fans
  the scope out to shard containers via ``.starmap()``; each shard runs
  :func:`run_rule_verify_shard` (CPU rules only), and the orchestrator
  merges the partial summaries. A single dedicated container then runs
  :func:`run_rule_verify_api_pass` over the full scope so the Wikidata
  probes share one throttle domain and one budget (Rules W-139 / W-256).
  Dispatch failure degrades to the local path (Rule W-15).

Progress stays counter-only (Rule W-128); the job result carries the
per-rule summary, never per-entity payloads (R14 lesson) — the review
data lives in the override rows and the results endpoint.
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any

from app.db import session_scope
from app.models.run_job import (
    JOB_STATUS_CANCELLED,
    JOB_STATUS_FAILED,
    JOB_STATUS_SUCCEEDED,
    RunJob,
)
from app.pipeline.rule_verify.base import RULE_STATES, RuleResult
from app.pipeline.rule_verify.engine import RuleEngine
from app.pipeline.rule_verify.persist import (
    merge_api_rule_verdicts,
    persist_rule_verdicts,
    per_rule_tally,
    summary_from_results,
)
from app.pipeline.rule_verify.rules.hmo import build_hmo_rules
from app.pipeline.rule_verify.scope import (
    ItemBuildMissingError,
    build_context,
    load_rule_verify_scope,
)
from app.pipeline.run_job_service import (
    JobCancelledErrorError,
    cancel_watcher,
    finish_job,
    is_cancel_requested,
    update_job_progress,
)

logger = logging.getLogger(__name__)

CHUNK = 1000


def _budgets() -> dict[str, int]:
    return {
        "wd_probe_budget": _int_env("RULE_VERIFY_WD_PROBE_MAX", 300),
        "wd_qid_budget": _int_env("RULE_VERIFY_WD_QID_MAX", 2000),
    }


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


def merge_summaries(summaries: list[dict[str, Any]]) -> dict[str, Any]:
    """Combine shard summaries into one job-level per-rule summary."""
    overall: dict[str, int] = {state: 0 for state in RULE_STATES}
    per_rule: dict[str, dict[str, int]] = {}
    scope = 0
    for part in summaries:
        scope += int(part.get("scope") or 0)
        for state, count in (part.get("overall_counts") or {}).items():
            overall[state] = overall.get(state, 0) + int(count)
        for rule_id, tally in (part.get("per_rule") or {}).items():
            row = per_rule.setdefault(
                rule_id, {state: 0 for state in RULE_STATES},
            )
            for state, count in tally.items():
                row[state] = row.get(state, 0) + int(count)
    return {"scope": scope, "overall_counts": overall, "per_rule": per_rule}


async def _run_and_persist(
    *,
    run_id: uuid.UUID,
    items: list[dict[str, Any]],
    with_api: bool,
    job_id: str,
    db_factory: Any = session_scope,
) -> dict[str, Any]:
    """Engine over ``items`` + persistence. Shared by job and shard paths."""
    from app.pipeline.rule_verify.api_fetcher import production_fetcher

    wikibase_endpoint = ""
    try:
        from app.settings import get_settings  # noqa: PLC0415

        wikibase_endpoint = get_settings().wikibase_cloud_base_url
    except Exception:  # noqa: BLE001 — settings missing → API rules abstain
        pass
    ctx = build_context(
        run_id=str(run_id),
        items=items,
        api_enabled=with_api,
        fetcher=production_fetcher() if with_api else None,
        wikibase_endpoint=wikibase_endpoint,
    )
    ctx.counters.update(_budgets())
    engine = RuleEngine(build_hmo_rules(), ctx)

    results: dict[str, list[RuleResult]] = {}
    items_by_id = {
        str(i.get("_local_id") or i.get("local_id") or ""): i for i in items
    }
    total = len(items)
    for start in range(0, total, CHUNK):
        chunk = items[start : start + CHUNK]
        part = await _engine_chunk(engine, chunk, with_api)
        results.update(part)
        chunk_by_id = {
            lid: items_by_id[lid] for lid in part if lid in items_by_id
        }
        async with db_factory() as db:
            await persist_rule_verdicts(
                db, run_id=run_id, results_by_local_id=part, job_id=job_id,
                items_by_id=chunk_by_id,
            )
    return summary_from_results(results)


async def _engine_chunk(
    engine: RuleEngine,
    chunk: list[dict[str, Any]],
    with_api: bool,
) -> dict[str, list[RuleResult]]:
    """CPU + (optionally) API rules off the event loop, in one thread."""
    return await _to_thread(engine.run_scope, chunk, include_api=with_api)


async def _to_thread(fn: Any, *args: Any, **kwargs: Any) -> Any:
    import asyncio  # noqa: PLC0415

    return await asyncio.to_thread(lambda: fn(*args, **kwargs))


async def run_rule_verify_job(job_id: uuid.UUID) -> None:
    """Single-process execution (Heroku fallback). Also correct on Modal."""
    async with session_scope() as db:
        job = await db.get(RunJob, job_id)
        if job is None:
            return
        run_id = job.run_id
        params = dict(job.params or {})
    item_ids = [str(x) for x in (params.get("item_ids") or [])] or None
    with_api = bool(params.get("with_api", True))

    await update_job_progress(job_id, {
        "phase": "preparing", "processed": 0, "total": 0,
        "message": "Loading rule-verify scope…",
    })

    try:
        async with session_scope() as db:
            items = await load_rule_verify_scope(
                db, run_id, item_ids=item_ids,
                should_cancel=cancel_watcher(job_id),
            )
    except ItemBuildMissingError as exc:
        await finish_job(job_id, status=JOB_STATUS_FAILED, error=str(exc))
        return
    except JobCancelledErrorError:
        await finish_job(
            job_id, status=JOB_STATUS_CANCELLED, error="Cancelled by user",
            progress={
                "phase": "cancelled", "processed": 0, "total": 0,
                "message": "Cancelled by user",
            },
        )
        return
    if not items:
        await finish_job(
            job_id, status=JOB_STATUS_FAILED,
            error="no HMO items in scope (build items first)",
        )
        return

    total = len(items)
    summaries: list[dict[str, Any]] = []
    cancelled = False
    for start in range(0, total, CHUNK):
        if await is_cancel_requested(job_id):
            cancelled = True
            break
        chunk = items[start : start + CHUNK]
        summary = await _run_and_persist(
            run_id=run_id, items=chunk, with_api=with_api, job_id=str(job_id),
        )
        summaries.append(summary)
        await update_job_progress(job_id, {
            "phase": "running",
            "processed": min(start + CHUNK, total),
            "total": total,
            "message": f"Checked {min(start + CHUNK, total)} of {total} items…",
        })

    merged = merge_summaries(summaries)
    if cancelled:
        await finish_job(
            job_id, status=JOB_STATUS_CANCELLED, result=merged,
            error="Cancelled by user",
            progress={
                "phase": "cancelled", "processed": merged.get("scope", 0),
                "total": total, "message": "Cancelled by user",
            },
        )
        return
    await finish_job(
        job_id, status=JOB_STATUS_SUCCEEDED, result=merged,
        progress={
            "phase": "done", "processed": total, "total": total,
            "message": f"Rule check complete: {total} items",
        },
    )


async def run_rule_verify_shard(
    job_id: str,
    run_id: uuid.UUID,
    local_ids: list[str],
) -> dict[str, Any]:
    """One Modal shard: check a slice, persist, return a partial summary.

    Runs inside a shard container — never writes the job row (the
    orchestrator owns progress and terminal state). Shards run the CPU
    rules only (Rule W-256): every shard probing with its own fetcher
    split the probe budget four ways and stacked four request rates into
    Wikidata 429s (132 measured on run 45513a45). The dedicated
    :func:`run_rule_verify_api_pass` owns every API rule.
    """
    async with session_scope() as db:
        items = await load_rule_verify_scope(db, run_id, item_ids=local_ids)
    if not items:
        return {"scope": 0, "overall_counts": {}, "per_rule": {}}
    return await _run_and_persist(
        run_id=run_id, items=items, with_api=False, job_id=job_id,
    )


def _api_pass_budgets() -> dict[str, int]:
    """The API pass owns the whole scope: budgets sized to cover it.

    ~30k probes x ~1.15 s (1.1 s throttle + latency) ≈ 9.5 h worst case;
    the default fits the 6 h API-pass container for the current corpus
    (~13k eligible items ≈ 4.2 h). Raise the container timeout in
    ``modal/modal_jobs.py`` together with ``RULE_VERIFY_API_PASS_PROBE_MAX``.
    """
    return {
        "wd_probe_budget": _int_env("RULE_VERIFY_API_PASS_PROBE_MAX", 30000),
        "wd_qid_budget": _int_env("RULE_VERIFY_API_PASS_QID_MAX", 60000),
    }


async def run_rule_verify_api_pass(job_id: str, run_id: uuid.UUID) -> dict[str, Any]:
    """The single-container API pass over the full scope (Rule W-256).

    One container holds the whole probe budget and the one 1.1 s
    throttle domain (Rule W-139), so parallel shards never split the
    budget or stack their request rates into Wikidata 429s. Results
    merge into the verdict rows the shards wrote; the return carries
    per-rule tallies only — the shards already counted the entities.
    """
    async with session_scope() as db:
        items = await load_rule_verify_scope(db, run_id)
    if not items:
        return {"scope": 0, "overall_counts": {}, "per_rule": {}, "probed": 0}
    from app.pipeline.rule_verify.api_fetcher import production_fetcher  # noqa: PLC0415

    wikibase_endpoint = ""
    try:
        from app.settings import get_settings  # noqa: PLC0415

        wikibase_endpoint = get_settings().wikibase_cloud_base_url
    except Exception:  # noqa: BLE001 — settings missing → API rules abstain
        pass
    ctx = build_context(
        run_id=str(run_id),
        items=items,
        api_enabled=True,
        fetcher=production_fetcher(),
        wikibase_endpoint=wikibase_endpoint,
    )
    ctx.counters.update(_api_pass_budgets())
    engine = RuleEngine(build_hmo_rules(), ctx)

    job_uuid = uuid.UUID(str(job_id))
    items_by_id = {
        str(i.get("_local_id") or i.get("local_id") or ""): i for i in items
    }
    results: dict[str, list[RuleResult]] = {}
    total = len(items)
    done = 0
    cancelled = False
    for start in range(0, total, CHUNK):
        if await is_cancel_requested(job_uuid):
            cancelled = True
            break
        chunk = items[start : start + CHUNK]
        part = await _to_thread(engine.run_scope, chunk, api_only=True)
        results.update(part)
        chunk_by_id = {
            lid: items_by_id[lid] for lid in part if lid in items_by_id
        }
        async with session_scope() as db:
            await merge_api_rule_verdicts(
                db, run_id=run_id, results_by_local_id=part, job_id=str(job_id),
                items_by_id=chunk_by_id,
            )
        done += len(chunk)
        await update_job_progress(job_uuid, {
            "phase": "probing",
            "processed": done,
            "total": total,
            "message": f"Probing live Wikidata labels: {done} of {total}…",
        })
    return {
        "scope": 0,
        "overall_counts": {},
        "per_rule": per_rule_tally(results),
        "probed": len(results),
        "cancelled": cancelled,
    }
