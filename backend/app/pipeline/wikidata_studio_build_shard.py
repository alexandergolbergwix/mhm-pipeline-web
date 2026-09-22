"""Distributed Wikidata Studio item build — shard + orchestrator.

Heroku stays the orchestrator (Rule W-237): the web process owns the
claimed job row (admission, heartbeat, cancel) and the Modal container
acting for it orchestrates the fan-out. Compute runs on parallel shard
containers — each maps one ``control_number`` slice with the same
desktop builder the sequential path uses and returns lossless native
item payloads.

The orchestrator merges the shards (persons/works dedupe by the
builder's own ``local_id`` key), runs the corpus-wide finish pipeline
once, gates the result through the export quality gate, and upserts
the same Postgres cache row the sequential build writes — so reads,
overrides, verdicts, and exports behave identically either way.

Memory profile: no process holds the raw corpus twice — shards hold
one slice, the orchestrator holds merged native items (the build
result, same as the sequential path) but never the MARC/authority
inputs.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.db import session_scope
from app.models.run_job import (
    JOB_STATUS_CANCELLED,
    JOB_STATUS_SUCCEEDED,
    RunJob,
)
from app.pipeline.run_job_service import (
    JobCancelledErrorError,
    finish_job,
    is_cancel_requested,
    update_job_progress,
)
from app.pipeline.wikidata_studio_batches import (
    compute_build_fingerprint_streamed,
    list_run_control_numbers,
    load_wikidata_build_slice,
    shard_slices,
)

logger = logging.getLogger(__name__)

# Manuscript slices per shard container. Items are heavier than RDF
# records (persons + works per record), so slices stay well below the
# rdf_build shard size of 1000.
_SHARD_CNS = 500


@dataclass
class WikidataShardPlan:
    job_id: uuid.UUID
    run_id: uuid.UUID
    total: int
    fingerprint: str
    approved_only: bool
    source: str
    force_rebuild: bool
    run_user_id: uuid.UUID | None
    #: Fresh cache hit — no fan-out needed, just finalise the row.
    cached_summary: dict[str, Any] | None = None
    cached_result_items: list[dict[str, Any]] = field(default_factory=list)
    cached_record_count: int = 0
    slices: list[list[str]] = field(default_factory=list)


# ── Shard runner (executes inside a shard container) ────────────────────


async def run_wikidata_studio_build_shard(
    job_id: uuid.UUID,
    run_id: uuid.UUID,
    control_numbers: list[str],
) -> dict[str, Any]:
    """Build one CN slice and return lossless native item payloads.

    Runs inside a shard container — never writes the job row (the
    orchestrator owns progress and terminal state).
    """
    from app.routers.wikidata_studio import _prewarm_transliterations  # noqa: PLC0415
    from converter.wikidata import hebrew_translit  # noqa: PLC0415

    async with session_scope() as db:
        job = await db.get(RunJob, job_id)
        params = dict(job.params or {}) if job else {}
        approved_only = bool(params.get("approved_only", True))
        slice_inputs = await load_wikidata_build_slice(
            db, run_id, control_numbers, approved_only=approved_only,
        )
        run_user_id = job.created_by if job else None

    from app.pipeline.wikidata_studio import build_shard_items  # noqa: PLC0415

    phase = "preparing transliterations"
    try:
        prewarmed = await _prewarm_transliterations(
            marc_records=slice_inputs["marc_records"], user_id=run_user_id,
        )
        hebrew_translit.set_prewarmed_labels(prewarmed)
        hebrew_translit.set_sync_network_disabled(True)
        phase = "building items"
        payloads = await build_shard_items(
            marc_records=slice_inputs["marc_records"],
            approved_matches=slice_inputs["approved_matches"],
            entities_by_cn=slice_inputs["entities_by_cn"],
            overrides=slice_inputs["overrides"],
            hmo_instance_qids=slice_inputs["hmo_instance_qids"],
        )
    finally:
        hebrew_translit.set_sync_network_disabled(False)
        hebrew_translit.clear_prewarmed_labels()

    return {
        "control_numbers": list(control_numbers),
        "items": payloads,
        "phase": phase,
    }


# ── Orchestrator plan + consumer ────────────────────────────────────────


async def load_wikidata_shard_plan(
    job_id: uuid.UUID,
    shard_size: int = _SHARD_CNS,
    should_cancel: Callable[[], Awaitable[None]] | None = None,
) -> WikidataShardPlan | None:
    """Load the fan-out plan for one claimed ``wikidata_studio_build`` job.

    Raises on an empty run so the W-249 in-container handler fails the
    row instead of leaving it "running" forever. A fresh cache hit
    yields an empty slice list — the orchestrator finalises the row
    without any fan-out.

    ``should_cancel`` is an awaitable that raises ``JobCancelledErrorError`` when
    the curator cancelled; the fingerprint + cache read can run for
    minutes on big runs, so it is tested between stages (Rule R28).
    """
    from app.pipeline.wikidata_studio import (  # noqa: PLC0415
        studio_cache_has_stale_validation,
    )
    from app.routers.wikidata_studio import _get_studio_cache_row  # noqa: PLC0415

    async with session_scope() as db:
        job = await db.get(RunJob, job_id)
        if job is None or job.status != "running":
            return None
        run_id = job.run_id
        params = dict(job.params or {})
        approved_only = bool(params.get("approved_only", True))
        force_rebuild = bool(params.get("force_rebuild", False))
        source = str(params.get("source") or "legacy")
        run_user_id = job.created_by

        control_numbers = await list_run_control_numbers(db, run_id)
        if not control_numbers:
            raise ValueError(f"run {run_id} has no records")

        if should_cancel is not None:
            await should_cancel()
        fingerprint = await compute_build_fingerprint_streamed(
            db, run_id, approved_only=approved_only,
        )
        if should_cancel is not None:
            await should_cancel()
        cached = await _get_studio_cache_row(db, run_id, approved_only, source)

    plan = WikidataShardPlan(
        job_id=job_id,
        run_id=run_id,
        total=len(control_numbers),
        fingerprint=fingerprint,
        approved_only=approved_only,
        source=source,
        force_rebuild=force_rebuild,
        run_user_id=run_user_id,
    )
    if (
        not force_rebuild
        and cached is not None
        and cached.input_fingerprint == fingerprint
        and not studio_cache_has_stale_validation(cached.result_items)
    ):
        plan.cached_summary = dict(cached.summary or {})
        plan.cached_result_items = list(cached.result_items or [])
        plan.cached_record_count = int(cached.record_count or 0)
        return plan
    plan.slices = shard_slices(control_numbers, shard_size)
    return plan


async def _update_build_progress(
    job_id: uuid.UUID, done: int, total: int, *, cancelled: bool = False,
) -> None:
    await update_job_progress(job_id, {
        "phase": "cancelled" if cancelled else "building items",
        "processed": done,
        "total": max(total, 1),
        "unit": "records",
        "message": f"Mapped {done} of {total} records (sharded)…",
    })


async def consume_wikidata_shard_results(
    plan: WikidataShardPlan,
    results: AsyncIterator[dict[str, Any]],
    should_cancel: Callable[[], Awaitable[None]] | None = None,
) -> None:
    """Merge shard payloads, finish the corpus, and finalise the job.

    ``results`` yields shard outcomes in input (control_number) order —
    Modal ``starmap`` preserves input order, and the merge's
    first-occurrence-wins rule depends on it.
    """
    from app.pipeline.wikidata_studio import (
        finish_native_items,
        merge_shard_items,
        native_item_from_payload,
    )

    job_id = plan.job_id

    if plan.cached_summary is not None:
        await finish_job(
            job_id,
            status=JOB_STATUS_SUCCEEDED,
            result={
                "total": len(plan.cached_result_items),
                "record_count": plan.cached_record_count,
                "summary": plan.cached_summary,
            },
            progress={
                "phase": "done",
                "processed": len(plan.cached_result_items),
                "total": max(len(plan.cached_result_items), 1),
                "unit": "items",
                "message": "Studio build cache already fresh",
            },
        )
        return

    logger.info(
        "wikidata studio sharded build for run %s: %d records in %d shards%s",
        plan.run_id, plan.total, len(plan.slices),
        " (cache hit)" if plan.cached_summary is not None else "",
    )

    shards: list[list[Any]] = []
    processed = 0
    cancelled = False
    await _update_build_progress(job_id, 0, plan.total)
    async for res in results:
        if res.get("__cancelled__"):
            cancelled = True
            break
        if res.get("__error__"):
            raise RuntimeError(f"wikidata studio shard failed: {res['__error__']}")
        if await is_cancel_requested(job_id):
            cancelled = True
            break
        shards.append([
            native_item_from_payload(p) for p in (res.get("items") or [])
        ])
        processed += len(res.get("control_numbers") or [])
        await _update_build_progress(job_id, processed, plan.total)

    if cancelled:
        await finish_job(
            job_id,
            status=JOB_STATUS_CANCELLED,
            error="Cancelled by user",
            progress={
                "phase": "cancelled",
                "processed": processed,
                "total": plan.total,
                "message": "Cancelled by user",
            },
        )
        return

    merged = merge_shard_items(shards)
    result = finish_native_items(merged)
    await _persist_build_result(plan, merged, result, should_cancel=should_cancel)


async def _persist_build_result(
    plan: WikidataShardPlan,
    native_items: list[Any],
    result: dict[str, Any],
    should_cancel: Callable[[], Awaitable[None]] | None = None,
) -> None:
    """Quality-gate, cache upsert, and finalise — shared tail of the fan-out.

    Mirrors the sequential tail in ``execute_studio_build``: gate first
    (a build bug must not reach the curator, Rule W-163), then the same
    cache-row upsert the Studio read path serves from.
    """
    from sqlalchemy import select as _select  # noqa: PLC0415

    from app.models.item_override import WikidataItemOverride  # noqa: PLC0415
    from app.pipeline import wikidata_studio as _ws  # noqa: PLC0415
    from app.pipeline.wikidata_export_quality_gate import (  # noqa: PLC0415
        assert_wikidata_export_quality,
    )
    from app.routers.wikidata_studio import (  # noqa: PLC0415
        _get_studio_cache_row,
        _replace_item_rows,
        _upsert_studio_cache,
    )

    # Gate per shard happened in the shard worker with the slice's MARC
    # (evidence-level checks). The merged pass re-checks everything that
    # does not need MARC — duplicate local ids, ordering, validators.
    assert_wikidata_export_quality(
        native_items, serialised_items=result["items"],
    )

    items = result["items"]
    from app.db import session_scope as _ss  # noqa: PLC0415

    async with _ss() as db:
        existing = await _get_studio_cache_row(
            db, plan.run_id, plan.approved_only, plan.source,
        )
        # Stamp stable handles + curator approval state — the sequential
        # path does this in the router right after the build; without it
        # manuscripts carry no local_id and overrides/verdicts never merge.
        override_rows = (
            await db.execute(
                _select(WikidataItemOverride).where(
                    WikidataItemOverride.run_id == plan.run_id,
                )
            )
        ).scalars().all()
        overrides_approved = {r.local_id: r.approved for r in override_rows}
        for it_dict, it_native in zip(
            result["items"], native_items, strict=True,
        ):
            lid = _ws.local_id_for_item(it_native)
            it_dict["local_id"] = lid
            it_dict["approved"] = overrides_approved.get(lid)
        await _upsert_studio_cache(
            run_id=plan.run_id,
            approved_only=plan.approved_only,
            source=plan.source,
            fingerprint=plan.fingerprint,
            items=items,
            quickstatements=result["quickstatements"],
            summary=result["summary"],
            approved_match_count=0,
            pending_match_count=0,
            used_match_count=0,
            record_count=plan.total,
            existing=existing,
        )
        await db.commit()
        await _replace_item_rows(
            db,
            run_id=plan.run_id,
            approved_only=plan.approved_only,
            source=plan.source,
            items=items,
        )

    await _mine_and_finalise(plan, items, result, should_cancel=should_cancel)


async def _mine_and_finalise(
    plan: WikidataShardPlan,
    items: list[dict[str, Any]],
    result: dict[str, Any],
    should_cancel: Callable[[], Awaitable[None]] | None = None,
) -> None:
    """Prose mining + terminal state — same tail as the sequential job."""
    from app.db import session_scope as _ss  # noqa: PLC0415
    from app.pipeline.wikidata_studio_build_job import (  # noqa: PLC0415
        _mine_provenance_prose,
    )
    from app.routers.wikidata_studio import _get_studio_cache_row  # noqa: PLC0415

    async with _ss() as db:
        cached = await _get_studio_cache_row(
            db, plan.run_id, plan.approved_only, plan.source,
        )
    if cached is not None:
        state: dict[str, object] = {
            "phase": "mining provenance prose", "done": 0, "records": 0,
        }
        phases = ("mining provenance prose",)
        try:
            await _mine_provenance_prose(
                plan.job_id, cached, state, phases,
                run_id=plan.run_id,
                approved_only=plan.approved_only,
                source=plan.source,
                should_cancel=should_cancel,
            )
        except JobCancelledErrorError:
            pass  # mining is optional; the cancel re-check below finalises

    if should_cancel is not None:
        await should_cancel()
    if await is_cancel_requested(plan.job_id):
        await finish_job(
            plan.job_id,
            status=JOB_STATUS_CANCELLED,
            error="Cancelled by user",
            progress={
                "phase": "cancelled",
                "processed": len(items),
                "total": max(plan.total, 1),
                "unit": "items",
                "message": "Cancelled by user",
            },
        )
        return

    total = len(items)
    await finish_job(
        plan.job_id,
        status=JOB_STATUS_SUCCEEDED,
        result={
            "total": total,
            "record_count": plan.total,
            "summary": result["summary"],
        },
        progress={
            "phase": "done",
            "processed": total,
            "total": max(total, 1),
            "unit": "items",
            "message": f"Built {total} items from {plan.total} records",
        },
    )


