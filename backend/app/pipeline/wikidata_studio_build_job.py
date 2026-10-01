"""Background Wikidata Studio build job."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from sqlalchemy import text

from app.db import session_scope
from app.models.run_job import (
    JOB_STATUS_CANCELLED,
    JOB_STATUS_FAILED,
    JOB_STATUS_SUCCEEDED,
    RunJob,
)
from app.pipeline.run_job_service import (
    JobCancelledError,
    cancel_watcher,
    finish_job,
    is_cancel_requested,
    update_job_progress,
)

logger = logging.getLogger(__name__)

PROGRESS_INTERVAL_SECONDS = 1.5
PROGRESS_WRITE_TIMEOUT_SECONDS = 15.0
# One run, then at most three resumes from the last finished batch.
MAX_BUILD_RETRIES = 3
BUILD_RETRY_SLEEP_SECONDS = 5.0

# Ordered build phases. The item loop is only one of them — reporting just that
# loop left the bar on 0/1 for every slow stage that precedes it (Rule W-112).
BUILD_PHASES: tuple[str, ...] = (
    "loading records",
    "loading canonical entities",
    "fingerprinting canonical entities",
    "preparing transliterations",
    "building items",
    "assembling canonical projection",
    "mining provenance prose",
)
# unit, then the sentence the tray shows for that step.
PHASE_GUIDE: dict[str, tuple[str, str]] = {
    "loading records": (
        "records",
        "Load the MARC records and the authority matches",
    ),
    "loading canonical entities": (
        "entities",
        "Load the saved HMO entities",
    ),
    "fingerprinting canonical entities": (
        "entities",
        "Hash each entity for the cache check",
    ),
    "preparing transliterations": (
        "names",
        "Prepare a Latin name for each Hebrew label",
    ),
    "building items": (
        "records",
        "Build one Wikidata item for each record",
    ),
    "assembling canonical projection": (
        "items",
        "Merge records, claims, and local references",
    ),
    "mining provenance prose": (
        "manuscripts",
        "Read provenance prose for each manuscript",
    ),
}
_UNIT_NOUN = {
    "records": "record",
    "entities": "entity",
    "names": "name",
    "items": "item",
    "manuscripts": "manuscript",
}
# Whole canonical build for run 3494ebf5, job 536c0b8c: 18524 entities, 1 h 26 min.
_MEASURED_ENTITIES = 18524
_MEASURED_PROCESS_SECONDS = 85 * 60 + 56
_ETA_MIN_SAMPLES = 3
_PROCESS_TOTAL_PHASES = (
    "loading canonical entities",
    "fingerprinting canonical entities",
)
_LEGACY_ONLY_PHASES = (
    "loading canonical entities",
    "fingerprinting canonical entities",
)


def _phase_unit(label: str) -> str:
    guide = PHASE_GUIDE.get(label)
    return guide[0] if guide else "records"


def _phase_description(label: str) -> str:
    guide = PHASE_GUIDE.get(label)
    return guide[1] if guide else ""


def _phase_plan(source: str) -> tuple[str, ...]:
    """The phases this build will actually pass through."""
    if source == "canonical":
        return BUILD_PHASES
    return tuple(
        phase
        for phase in BUILD_PHASES
        if phase not in (*_LEGACY_ONLY_PHASES, "assembling canonical projection")
    )


def _format_build_eta(seconds: int) -> str:
    late = seconds < 0
    whole = abs(int(seconds))
    tail = "over" if late else "left"
    if whole < 60:
        return f"{whole}s {tail}"
    minutes = max(1, round(whole / 60))
    if minutes < 60:
        return f"{minutes} min {tail}"
    hours = max(1, round(minutes / 60))
    return f"{hours} h {tail}"


def _refresh_eta(state: dict[str, object], now: float) -> tuple[int | None, int | None]:
    """Lock a step deadline from the first samples. Later work may run past it.

    The whole-build deadline scales the measured 1 h 26 min run by the entity total.
    """
    phase = str(state.get("phase") or "")
    detail = str(state.get("detail") or "")
    key = f"{phase}\n{detail}"
    done = int(state.get("done") or 0)
    total = int(state.get("records") or 0)
    if state.get("activity_key") != key or done < int(state.get("activity_done") or 0):
        state["activity_key"] = key
        state["activity_started"] = now
        state["activity_origin"] = done
        state["activity_deadline"] = None
    state["activity_done"] = done
    started = float(state.get("activity_started") or now)
    origin = int(state.get("activity_origin") or 0)
    elapsed = max(0.0, now - started)
    samples = done - origin
    deadline = state.get("activity_deadline")
    if deadline is None and samples >= _ETA_MIN_SAMPLES and total > 0 and elapsed > 0:
        rate = elapsed / samples
        deadline = now + max(0, total - done) * rate
        state["activity_deadline"] = deadline
    step_left = int(float(deadline) - now) if isinstance(deadline, (int, float)) else None

    if (
        state.get("process_budget") is None
        and phase in _PROCESS_TOTAL_PHASES
        and total > 0
    ):
        state["process_budget"] = _MEASURED_PROCESS_SECONDS * (total / _MEASURED_ENTITIES)
    process_budget = state.get("process_budget")
    job_started = state.get("job_started")
    process_left = None
    if isinstance(process_budget, (int, float)) and isinstance(job_started, (int, float)):
        process_left = int(float(process_budget) - (now - float(job_started)))
    return step_left, process_left


def _build_progress(state: dict[str, object], phases: tuple[str, ...]) -> dict[str, object]:
    """Outer progress is 1-based phases; the record loop nests underneath.

    ``steps`` carries the full phase plan so the UI can render a per-step
    bar. Done phases show a tick. The running phase shows its count.
    Assembly reports one rising item count across its passes. A phase with
    no callback leaves the record fields empty, and the strip shows a pulse.
    Pending phases stay hollow.
    """
    label = str(state.get("phase") or phases[0])
    step = (phases.index(label) + 1) if label in phases else 1
    total = len(phases)
    done, records = int(state.get("done") or 0), int(state.get("records") or 0)
    unit = str(state.get("unit") or "records")
    detail = str(state.get("detail") or "")
    retry = int(state.get("retry") or 0)
    retry_max = int(state.get("retry_max") or 0)
    retry_note = f" (retry {retry} of {retry_max})" if retry else ""
    finished = min(done, records) if records else 0
    now = float(state["now"]) if isinstance(state.get("now"), (int, float)) else time.monotonic()
    step_left, process_left = _refresh_eta(state, now)
    steps: list[dict[str, object]] = []
    for index, phase in enumerate(phases, start=1):
        entry: dict[str, object] = {
            "id": f"phase-{index}",
            "label": phase,
            "description": _phase_description(phase),
        }
        if index < step:
            entry["status"] = "done"
        elif index == step:
            entry["status"] = "running"
            if retry_note:
                entry["label"] = f"{phase}{retry_note}"
            if records:
                entry["processed"] = finished
                entry["total"] = records
                entry["unit"] = unit
            if detail:
                entry["current_label"] = detail
            if step_left is not None:
                entry["eta_seconds"] = step_left
        else:
            entry["status"] = "pending"
        steps.append(entry)
    message = f"Step {step} of {total}: {label}{retry_note}"
    clock: list[str] = []
    if step_left is not None:
        clock.append(f"step {_format_build_eta(step_left)}")
    if process_left is not None:
        clock.append(f"all {_format_build_eta(process_left)}")
    if clock:
        message = f"{message} · {' · '.join(clock)}"
    progress: dict[str, object] = {
        "phase": label,
        "processed": step,
        "total": total,
        "unit": "steps",
        "message": message,
        "steps": steps,
        "eta_seconds": step_left,
        "process_eta_seconds": process_left,
    }
    if records:
        if detail:
            sub_message = f"{detail}: {finished} of {records}{retry_note}"
        else:
            noun = _UNIT_NOUN.get(unit, "record")
            sub_message = f"{noun} {finished} of {records}{retry_note}"
        progress.update(
            sub_processed=finished,
            sub_total=records,
            sub_unit=unit,
            sub_message=sub_message,
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
    last: tuple[object, int, int] | None = None
    while True:
        await asyncio.sleep(PROGRESS_INTERVAL_SECONDS)
        now = time.monotonic()
        state["now"] = now
        progress = _build_progress(state, phases)
        fingerprint = (
            state.get("phase"),
            int(state.get("done") or 0),
            int(state.get("retry") or 0),
            state.get("detail"),
            None if progress.get("eta_seconds") is None else int(progress["eta_seconds"]) // 5,
            None if progress.get("process_eta_seconds") is None else int(progress["process_eta_seconds"]) // 5,
        )
        if fingerprint == last:
            continue
        try:
            await asyncio.wait_for(
                update_job_progress(job_id, progress),
                timeout=PROGRESS_WRITE_TIMEOUT_SECONDS,
            )
        except Exception:  # noqa: BLE001 — a dropped progress write must not freeze the counter
            logger.warning(
                "wikidata studio build progress write failed for %s", job_id, exc_info=True,
            )
            continue
        last = fingerprint


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
    should_cancel: Callable[[], Awaitable[None]] | None = None,
) -> None:
    """Attach span-grounded LLM proposals to the built items (Rule W-140).

    Runs here rather than on the verify path: one model call per manuscript kept
    "Loading Studio scope…" spinning for minutes before the judge could start.
    Never fatal — a build must not fail because an optional enrichment did.
    Cancel is the exception: a JobCancelledError propagates so the caller can
    finalize the row as cancelled instead of succeeded.
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
    state["unit"] = _phase_unit("mining provenance prose")

    def on_progress(done: int, total: int) -> None:
        state["done"], state["records"] = done, total

    try:
        # Built items carry no MARC — that is attached on the verify path only.
        # Without it every manuscript had zero prose to read, so the whole phase
        # was a 0.4 s no-op and every export reported `not_run` (Rule W-140).
        await _attach_prose_context(run_id, items, list(SOURCE_SLICES))
        if should_cancel is not None:
            await should_cancel()
        try:
            stats = await attach_llm_proposals(
                session_scope, items, on_progress=on_progress,
                should_cancel=should_cancel,
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
    except JobCancelledError:
        raise
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
    # Survives a failed attempt so the next attempt continues the entity
    # cursor, the transliteration cache, and a finished item build.
    resume: dict[str, object] = {}
    state: dict[str, object] = {
        "phase": phases[0], "done": 0, "records": 0,
        "retry": 0, "retry_max": MAX_BUILD_RETRIES,
        "job_started": time.monotonic(),
    }
    await update_job_progress(job_id, _build_progress(state, phases))

    if await is_cancel_requested(job_id):
        await finish_job(job_id, status=JOB_STATUS_CANCELLED)
        return

    should_cancel = cancel_watcher(job_id)

    def on_record(done: int, total: int, detail: str = "") -> None:
        state["done"], state["records"] = done, total
        if detail:
            state["detail"] = detail
        else:
            state.pop("detail", None)
        state["unit"] = _phase_unit(str(state.get("phase") or ""))
        _refresh_eta(state, time.monotonic())

    def on_phase(label: str) -> None:
        current = str(state.get("phase") or "")
        # A retry re-enters the build at "loading records". Keep the step
        # that already failed so the bar does not jump backwards.
        if (
            int(state.get("retry") or 0) > 0
            and current in phases
            and label in phases
            and phases.index(label) < phases.index(current)
        ):
            return
        state["phase"] = label
        saved = resume.get("entities")
        # A retry of the entity read must keep the last finished count.
        # Resetting it to 0 reads as lost work (Rule W-259).
        if (
            label == "loading canonical entities"
            and isinstance(saved, list)
            and saved
            and not resume.get("entities_complete")
        ):
            state["done"] = len(saved)
            state["records"] = int(resume.get("entity_total") or 0)
            state["unit"] = _phase_unit(label)
            _refresh_eta(state, time.monotonic())
            return
        # The record counters belong to the phase that emitted them.
        state["done"], state["records"] = 0, 0
        state.pop("detail", None)
        state["unit"] = _phase_unit(label)
        _refresh_eta(state, time.monotonic())

    publisher = asyncio.create_task(_publish_build_progress(job_id, state, phases))

    async def _heartbeat_updated_at() -> None:
        while True:
            try:
                from app.db import session_scope as _ss  # noqa: PLC0415

                async with _ss() as hb:
                    await hb.execute(text(
                        "UPDATE run_jobs SET updated_at = now() "
                        "WHERE id = :jid AND status = 'running'"
                    ), {"jid": job_id})
            except Exception:  # noqa: BLE001 — keep-alive is best-effort
                pass
            await asyncio.sleep(60)

    hb_task = asyncio.create_task(_heartbeat_updated_at())
    try:
        from app.routers.wikidata_studio import execute_studio_build  # noqa: PLC0415

        cached = None
        for attempt in range(MAX_BUILD_RETRIES + 1):
            state["retry"] = attempt
            try:
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
                        should_cancel=should_cancel,
                        resume=resume,
                    )
                break
            except JobCancelledError:
                # Rule R28: finalize at the record/phase boundary where the flag
                # was seen — not after the whole build crawled to its end.
                await finish_job(
                    job_id,
                    status=JOB_STATUS_CANCELLED,
                    error="Cancelled by user",
                    progress={
                        "phase": "cancelled",
                        "processed": min(int(state.get("done") or 0) + 1, len(phases)),
                        "total": len(phases),
                        "unit": "steps",
                        "message": "Cancelled by user",
                    },
                )
                return
            except ValueError as exc:
                # A missing canonical corpus is a curator error, not a dropped
                # connection. Another attempt cannot invent the rows.
                await finish_job(job_id, status=JOB_STATUS_FAILED, error=str(exc)[:2000])
                return
            except Exception as exc:  # noqa: BLE001
                if attempt >= MAX_BUILD_RETRIES or await is_cancel_requested(job_id):
                    logger.exception("wikidata studio build job failed for %s", run_id)
                    note = f" (stopped after {attempt} retries)" if attempt else ""
                    await finish_job(
                        job_id, status=JOB_STATUS_FAILED, error=f"{exc}{note}"[:2000],
                    )
                    return
                logger.warning(
                    "wikidata studio build %s failed (%s) — retry %s of %s from the saved cursor",
                    run_id, str(exc)[:160], attempt + 1, MAX_BUILD_RETRIES,
                )
                await asyncio.sleep(BUILD_RETRY_SLEEP_SECONDS)
                if await is_cancel_requested(job_id):
                    await finish_job(job_id, status=JOB_STATUS_CANCELLED, error="Cancelled by user")
                    return
        if cached is None:
            return
    finally:
        publisher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await publisher
        hb_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await hb_task

    if await is_cancel_requested(job_id):
        await finish_job(job_id, status=JOB_STATUS_CANCELLED)
        return

    try:
        await _mine_provenance_prose(
            job_id, cached, state, phases,
            run_id=run_id, approved_only=approved_only, source=source,
            should_cancel=should_cancel,
        )
    except JobCancelledError:
        await finish_job(
            job_id,
            status=JOB_STATUS_CANCELLED,
            error="Cancelled by user",
            progress={
                "phase": "cancelled",
                "processed": len(phases),
                "total": len(phases),
                "unit": "steps",
                "message": "Cancelled by user",
            },
        )
        return

    if await is_cancel_requested(job_id):
        # A cancel that landed mid-mining must not be overridden by a
        # succeeded finish — mining is optional, cancel is not.
        await finish_job(job_id, status=JOB_STATUS_CANCELLED)
        return

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
