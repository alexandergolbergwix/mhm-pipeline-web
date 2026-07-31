"""Background Wikidata Studio build job."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid

from app.db import session_scope
from app.models.run_job import (
    JOB_STATUS_CANCELLED,
    JOB_STATUS_FAILED,
    JOB_STATUS_SUCCEEDED,
    RunJob,
)
from app.pipeline.run_job_service import (
    finish_job,
    is_cancel_requested,
    update_job_progress,
)

logger = logging.getLogger(__name__)

PROGRESS_INTERVAL_SECONDS = 1.5

# Ordered build phases. The item loop is only one of them — reporting just that
# loop left the bar on 0/1 for every slow stage that precedes it (Rule W-112).
BUILD_PHASES: tuple[str, ...] = (
    "loading records",
    "loading canonical entities",
    "preparing transliterations",
    "building items",
    "assembling canonical projection",
    "mining provenance prose",
)
_LEGACY_ONLY_PHASE = "loading canonical entities"


def _phase_plan(source: str) -> tuple[str, ...]:
    """The phases this build will actually pass through."""
    if source == "canonical":
        return BUILD_PHASES
    return tuple(
        phase
        for phase in BUILD_PHASES
        if phase not in (_LEGACY_ONLY_PHASE, "assembling canonical projection")
    )


def _build_progress(state: dict[str, object], phases: tuple[str, ...]) -> dict[str, object]:
    """Outer progress is 1-based phases; the record loop nests underneath."""
    label = str(state.get("phase") or phases[0])
    step = (phases.index(label) + 1) if label in phases else 1
    total = len(phases)
    progress: dict[str, object] = {
        "phase": label,
        "processed": step,
        "total": total,
        "unit": "steps",
        "message": f"Step {step} of {total}: {label}",
    }
    done, records = int(state.get("done") or 0), int(state.get("records") or 0)
    if records:
        progress.update(
            sub_processed=min(done + 1, records),
            sub_total=records,
            sub_unit="records",
            sub_message=f"record {min(done + 1, records)} of {records}",
        )
    return progress


async def _publish_build_progress(
    job_id: uuid.UUID,
    state: dict[str, object],
    phases: tuple[str, ...],
) -> None:
    """Publish phase + nested record progress while the build runs.

    ``builder.build_all`` reports from a ``run_in_threadpool`` worker, which
    cannot touch the async session — so the callbacks only mutate *state* and
    this task owns every DB write (Rule W-112 outer steps, Rule W-113 nested
    sub-progress, Rule W-128 light polls on the web dyno).
    """
    last: tuple[object, int] | None = None
    while True:
        await asyncio.sleep(PROGRESS_INTERVAL_SECONDS)
        fingerprint = (state.get("phase"), int(state.get("done") or 0))
        if fingerprint == last:
            continue
        last = fingerprint
        await update_job_progress(job_id, _build_progress(state, phases))


async def _persist_mined_items(
    run_id: uuid.UUID,
    approved_only: bool,
    source: str,
    items: list[dict[str, object]],
) -> None:
    """Write the mined `_llm_proposals` back onto the durable Studio cache row."""
    from app.db import session_scope  # noqa: PLC0415
    from app.routers.wikidata_studio import _get_studio_cache_row  # noqa: PLC0415

    async with session_scope() as db:
        row = await _get_studio_cache_row(db, run_id, approved_only, source)
        if row is None:
            logger.warning("no Studio cache row to persist proposals for run %s", run_id)
            return
        row.result_items = items
        await db.commit()


async def _attach_prose_context(
    run_id: uuid.UUID,
    items: list[dict[str, object]],
    keys: list[str],
) -> None:
    """Stamp `_marc_context` with the prose slices the extractor reads.

    Scoped to the manuscripts actually in the build and to the three prose keys
    — the full verify pack is far too heavy for a Basic dyno (Rule W-132).
    """
    from app.db import session_scope  # noqa: PLC0415
    from app.pipeline.marc_verify_context import (  # noqa: PLC0415
        canonical_control_number,
        index_marc_records,
        load_run_marc_records_scoped,
        marc_context_for_item,
        primary_control_number_for,
    )

    manuscripts = [it for it in items if str(it.get("entity_type") or "") == "manuscript"]
    if not manuscripts:
        return
    own_cns: dict[int, list[str]] = {}
    wanted: set[str] = set()
    for item in manuscripts:
        # Studio items name their records `record_ids`/`records`; only the verify
        # path renames that to `control_numbers`.
        stored = item.get("record_ids") or item.get("records") or []
        cns = [
            canonical_control_number(cn)
            for cn in (stored if isinstance(stored, list) else [])
        ]
        cns = [cn for cn in cns if cn]
        own_cns[id(item)] = cns
        wanted.update(cns)
    if not wanted:
        return
    async with session_scope() as db:
        records = await load_run_marc_records_scoped(db, run_id, wanted)
    marc_index = index_marc_records(records)
    for item in manuscripts:
        cns = own_cns[id(item)]
        primary = primary_control_number_for(
            cns, item.get("source_uri"), item.get("local_id"),
        )
        item["_marc_context"] = marc_context_for_item(
            {
                "control_numbers": cns,
                "source_uri": item.get("source_uri"),
                "local_id": item.get("local_id"),
            },
            marc_index,
            keys=keys,
        )
        item["_primary_control_number"] = primary


async def _mine_provenance_prose(
    job_id: uuid.UUID,
    cached: object,
    state: dict[str, object],
    phases: tuple[str, ...],
    run_id: uuid.UUID,
    approved_only: bool,
    source: str,
) -> None:
    """Attach span-grounded LLM proposals to the built items (Rule W-140).

    Runs here rather than on the verify path: one model call per manuscript kept
    "Loading Studio scope…" spinning for minutes before the judge could start.
    Never fatal — a build must not fail because an optional enrichment did.
    """
    items = list(getattr(cached, "result_items", None) or [])
    if not items:
        return
    from app.db import session_scope  # noqa: PLC0415
    from app.pipeline.marc_llm_extract import (  # noqa: PLC0415
        SOURCE_SLICES,
        attach_llm_proposals,
    )

    state["phase"] = "mining provenance prose"
    state["done"], state["records"] = 0, 0

    def on_progress(done: int, total: int) -> None:
        state["done"], state["records"] = done, total

    try:
        # Built items carry no MARC — that is attached on the verify path only.
        # Without it every manuscript had zero prose to read, so the whole phase
        # was a 0.4 s no-op and every export reported `not_run` (Rule W-140).
        await _attach_prose_context(run_id, items, list(SOURCE_SLICES))
        try:
            stats = await attach_llm_proposals(
                session_scope, items, on_progress=on_progress,
            )
        finally:
            # The prose slice is a mining input, not curator data — never let it
            # ride into the persisted cache row (Rule W-131 heap budget).
            for item in items:
                item.pop("_marc_context", None)
                item.pop("_primary_control_number", None)
        logger.info("marc llm extract: %s", stats)
        if stats.get("proposals"):
            # execute_studio_build already wrote the cache row, so mining in
            # memory alone left every export reading `not_run`. Persist the
            # enriched items or the proposals never reach a curator.
            await _persist_mined_items(run_id, approved_only, source, items)
    except Exception as exc:  # noqa: BLE001 — enrichment must not fail the build
        logger.warning("marc llm extract skipped for job %s: %s", job_id, exc)


async def run_wikidata_studio_build_job(job_id: uuid.UUID) -> None:
    async with session_scope() as db:
        job = await db.get(RunJob, job_id)
        if job is None:
            return
        run_id = job.run_id
        params = job.params or {}
        approved_only = bool(params.get("approved_only", True))
        force_rebuild = bool(params.get("force_rebuild", False))
        run_user_id = job.created_by
        source = str(params.get("source") or "legacy")

    phases = _phase_plan(source)
    state: dict[str, object] = {"phase": phases[0], "done": 0, "records": 0}
    await update_job_progress(job_id, _build_progress(state, phases))

    if await is_cancel_requested(job_id):
        await finish_job(job_id, status=JOB_STATUS_CANCELLED)
        return

    def on_record(done: int, total: int) -> None:
        state["done"], state["records"] = done, total

    def on_phase(label: str) -> None:
        state["phase"] = label

    publisher = asyncio.create_task(_publish_build_progress(job_id, state, phases))
    try:
        from app.routers.wikidata_studio import execute_studio_build  # noqa: PLC0415

        async with session_scope() as db:
            cached = await execute_studio_build(
                db,
                run_id=run_id,
                approved_only=approved_only,
                force_rebuild=force_rebuild,
                run_user_id=run_user_id,
                source=source,
                # Never WDQS-reconcile the full corpus on the build path (Rule W-119).
                # Reconcile runs on upload / gated QS / the preview endpoint only.
                reconcile=False,
                progress_cb=on_record,
                phase_cb=on_phase,
            )
    except Exception as exc:  # noqa: BLE001
        logger.exception("wikidata studio build job failed for %s", run_id)
        await finish_job(job_id, status=JOB_STATUS_FAILED, error=str(exc))
        return
    finally:
        publisher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await publisher

    if await is_cancel_requested(job_id):
        await finish_job(job_id, status=JOB_STATUS_CANCELLED)
        return

    await _mine_provenance_prose(
        job_id, cached, state, phases,
        run_id=run_id, approved_only=approved_only, source=source,
    )

    total = len(cached.result_items or [])
    summary = cached.summary or {}
    await finish_job(
        job_id,
        status=JOB_STATUS_SUCCEEDED,
        result={
            "total": total,
            "record_count": cached.record_count,
            "approved_match_count": cached.approved_match_count,
            "summary": summary,
        },
        progress={
            "phase": "done",
            "processed": total,
            "total": max(total, 1),
            "unit": "items",
            "message": f"Built {total} items from {cached.record_count} records",
        },
    )
