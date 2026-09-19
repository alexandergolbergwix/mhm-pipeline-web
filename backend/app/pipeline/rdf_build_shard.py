"""Distributed RDF build — shards + orchestrator (batch-build R23).

Heroku stays the orchestrator (Rule W-237): it (or the claimed Modal
container acting for it) owns the single claimed job row — admission,
heartbeat, cancel, progress, terminal state. Compute fans out to
parallel shard containers, each mapping one ``control_number`` slice
with the same streaming mapper the sequential path uses and returning
the serialized Turtle chunk.

The orchestrator appends shard chunks to the single artifact **in
control_number order**, updating the same byte-offset checkpoints the
sequential job writes. A Modal outage mid-build therefore falls back to
the sequential runner and resumes at the same record index instead of
restarting (Rule W-15: Modal is a compute target, never a dependency).

Memory profile: no process ever holds the corpus — shards hold one
slice, the orchestrator holds one chunk at a time plus the append-only
file.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.db import session_scope
from app.models.run_job import (
    JOB_STATUS_CANCELLED,
    JOB_STATUS_SUCCEEDED,
    RunJob,
)
from app.pipeline.rdf_build import (
    RdfBuildOptions,
    RdfBuildResult,
    _init_mapper_state,
    _map_batch_sync,
    _prepare_artifact_file,
    _run_coverage_reports_subprocess,
    rdf_build_signature,
    rdf_output_path_for_run,
)
from app.pipeline.rdf_build_batches import (
    load_rdf_triple_overrides,
    load_record_slice,
    run_record_bounds,
)
from app.pipeline.run_job_service import (
    finish_job,
    is_cancel_requested,
    update_job_progress,
)

logger = logging.getLogger(__name__)


def _append_shard_chunk(out_path: Path, chunk: str) -> int:
    """Append one shard chunk; return the artifact size after the append."""
    with open(out_path, "ab") as fh:
        fh.write(chunk.encode("utf-8"))
    return out_path.stat().st_size


def rdf_build_options_from_params(params: dict[str, Any]) -> RdfBuildOptions:
    return RdfBuildOptions(
        add_epistemological_status=bool(params.get("add_epistemological_status", True)),
        add_cataloging_view=bool(params.get("add_cataloging_view", True)),
        add_philological_overlay=bool(params.get("add_philological_overlay", True)),
    )


# ── Shard runner (executes inside a shard container) ────────────────────


async def run_rdf_build_shard(
    job_id: uuid.UUID,
    run_id: uuid.UUID,
    control_numbers: list[str],
) -> dict[str, Any]:
    """Map one CN slice and return its Turtle chunk.

    Runs inside a shard container — never writes the job row (the
    orchestrator owns progress and terminal state). Same mapper, same
    per-record streaming as the sequential build; only the output goes
    to a shard-local chunk instead of the shared artifact.
    """
    async with session_scope() as db:
        job = await db.get(RunJob, job_id)
        params = dict(job.params or {}) if job else {}
        batch = await load_record_slice(db, run_id, control_numbers)
        overrides = await load_rdf_triple_overrides(db, run_id)

    opts = rdf_build_options_from_params(params)
    import tempfile  # noqa: PLC0415

    with tempfile.TemporaryDirectory(prefix="rdf-shard-") as tmp:
        out_path = Path(tmp) / "shard.ttl"
        state = _init_mapper_state(
            opts, out_path, overrides, len(control_numbers), None, None, None,
        )
        _map_batch_sync(state, batch)
        turtle = out_path.read_text(encoding="utf-8")

    return {
        "control_numbers": list(control_numbers),
        "turtle": turtle,
        "triples_count": state.triples_count,
        "manuscripts": state.manuscripts,
        "errors": list(state.errors),
    }


# ── Orchestrator plan + consumer ────────────────────────────────────────


@dataclass
class RdfShardPlan:
    run_id: uuid.UUID
    total: int
    signature: str
    resume: dict[str, Any] | None
    checkpoint: dict[str, Any]
    resumed_manuscripts: int
    slices: list[tuple[int, list[str]]] = field(default_factory=list)


def plan_rdf_shards(
    control_numbers: list[str],
    shard_size: int,
    resume_index: int,
) -> list[tuple[int, list[str]]]:
    """CN slices per shard, honouring a resume record index.

    Each shard window is ``[start, start + shard_size)``. Windows fully
    below ``resume_index`` are skipped (already mapped + appended). A
    window straddling a sequential-path checkpoint runs only its
    unmapped suffix — record chunks are self-contained, so appending the
    suffix continues the artifact byte-for-byte.
    """
    if shard_size < 1:
        raise ValueError("shard_size must be >= 1")
    slices: list[tuple[int, list[str]]] = []
    for start in range(0, len(control_numbers), shard_size):
        end = min(start + shard_size, len(control_numbers))
        if end <= resume_index:
            continue
        effective_from = max(start, resume_index)
        slices.append((start, control_numbers[effective_from:end]))
    return slices


async def _list_control_numbers(run_id: uuid.UUID) -> list[str]:
    from sqlalchemy import select  # noqa: PLC0415

    from app.models.run import RunRecord  # noqa: PLC0415

    async with session_scope() as db:
        rows = (
            await db.execute(
                select(RunRecord.control_number)
                .where(RunRecord.run_id == run_id)
                .order_by(RunRecord.control_number.asc())
            )
        ).scalars().all()
    return [str(r) for r in rows]


async def load_rdf_shard_plan(
    job_id: uuid.UUID, shard_size: int,
) -> RdfShardPlan | None:
    """Load the fan-out plan for one claimed ``rdf_build`` job row.

    Raises on an empty run so the W-249 in-container handler fails the
    row instead of leaving it "running" forever.
    """
    async with session_scope() as db:
        job = await db.get(RunJob, job_id)
        if job is None or job.status != "running":
            return None
        run_id = job.run_id
        params = dict(job.params or {})
        total, first_cn, last_cn = await run_record_bounds(db, run_id)
        if total == 0:
            raise ValueError("run has no records")
        prev_checkpoint = (
            job.progress.get("checkpoint")
            if isinstance(job.progress, dict) else None
        ) or {}

    opts = rdf_build_options_from_params(params)
    signature = rdf_build_signature(run_id, total, first_cn, last_cn, opts)
    resume: dict[str, Any] | None = None
    if (
        prev_checkpoint.get("signature") == signature
        and int(prev_checkpoint.get("record_index") or 0) > 0
        and not bool(params.get("force_rebuild"))
    ):
        resume = {
            k: prev_checkpoint[k]
            for k in ("record_index", "file_bytes", "manuscripts")
        }
    checkpoint: dict[str, Any] = {"signature": signature}
    resumed_index = int((resume or {}).get("record_index") or 0)

    control_numbers = await _list_control_numbers(run_id)
    return RdfShardPlan(
        run_id=run_id,
        total=total,
        signature=signature,
        resume=resume,
        checkpoint=checkpoint,
        resumed_manuscripts=int((resume or {}).get("manuscripts") or 0),
        slices=plan_rdf_shards(control_numbers, shard_size, resumed_index),
    )


async def consume_rdf_shard_results(
    job_id: uuid.UUID,
    plan: RdfShardPlan,
    results: AsyncIterator[dict[str, Any]],
) -> None:
    """Append ordered shard chunks, checkpoint, and finalise the job.

    ``results`` must yield shard outcomes **in control_number order**
    (Modal ``starmap`` preserves input order). Every state transition —
    progress, checkpoint, cancel, terminal state — happens on the single
    claimed job row from this orchestrator.
    """
    started = datetime.now(UTC)
    out_path = rdf_output_path_for_run(str(plan.run_id))
    processed = _prepare_artifact_file(out_path, plan.resume)
    manuscripts = plan.resumed_manuscripts
    triples_running = 0
    mapping_errors: list[str] = []
    checkpoint = plan.checkpoint
    cancelled = False

    await update_job_progress(job_id, {
        "phase": "building",
        "processed": processed,
        "total": plan.total,
        "message": f"Building RDF for {plan.total} records (sharded)…",
        "checkpoint": dict(checkpoint),
    })

    async for res in results:
        if res.get("__cancelled__"):
            # The stream observed the cancel flag while waiting on starmap.
            cancelled = True
            break
        if res.get("__error__"):
            raise RuntimeError(f"rdf_build shard failed: {res['__error__']}")
        if await is_cancel_requested(job_id):
            cancelled = True
            break
        chunk = str(res.get("turtle") or "")
        count = len(res.get("control_numbers") or [])
        file_bytes = await asyncio.to_thread(_append_shard_chunk, out_path, chunk)
        processed += count
        manuscripts += int(res.get("manuscripts") or 0)
        triples_running += int(res.get("triples_count") or 0)
        mapping_errors.extend(res.get("errors") or [])
        checkpoint.update({
            "record_index": processed,
            "file_bytes": file_bytes,
            "manuscripts": manuscripts,
            "triples_count": triples_running,
        })
        await update_job_progress(job_id, {
            "phase": "building",
            "processed": processed,
            "total": plan.total,
            "message": f"Mapped {processed} of {plan.total} records…",
            "checkpoint": dict(checkpoint),
        })

    result = RdfBuildResult(
        triples_count=triples_running,
        manuscripts_count=manuscripts,
        output_path=out_path,
        started_at=started,
        finished_at=datetime.now(UTC),
        mapping_errors=mapping_errors,
    )
    try:
        # The coverage subprocess re-parses the merged artifact and
        # yields the authoritative distinct-triple count + both reports
        # (same contract as the sequential build).
        (
            coverage_path, unknown_count, ontology_coverage_path,
            ontology_class_count, ontology_property_count,
            ontology_missing_terms, artifact_triples,
        ) = await asyncio.to_thread(_run_coverage_reports_subprocess, out_path)
        result.triples_count = (
            artifact_triples if artifact_triples is not None else triples_running
        )
        result.coverage_path = coverage_path
        result.unknown_class_count = unknown_count
        result.ontology_coverage_path = ontology_coverage_path
        result.ontology_class_count = ontology_class_count
        result.ontology_property_count = ontology_property_count
        result.ontology_missing_terms = ontology_missing_terms
    except Exception as exc:  # noqa: BLE001 — reports are diagnostics (R9)
        from app.pipeline.rdf_build import logger as _rdf_logger  # noqa: PLC0415

        _rdf_logger.warning("RDF coverage subprocess failed: %s", exc)

    await persist_rdf_artifact_and_bust_caches(plan.run_id, out_path, result)
    await finish_job(
        job_id,
        status=JOB_STATUS_CANCELLED if cancelled else JOB_STATUS_SUCCEEDED,
        result=result.to_dict(),
        error="Cancelled by user" if cancelled else None,
        progress={
            "phase": "cancelled" if cancelled else "done",
            "processed": processed,
            "total": plan.total,
            "message": "Cancelled by user" if cancelled else "RDF build complete",
        },
    )


# ── Shared persistence tail (sequential job + orchestrator) ─────────────


async def persist_rdf_artifact_and_bust_caches(
    run_id: uuid.UUID,
    out_path: Path,
    result: RdfBuildResult,
) -> None:
    """Write-through the TTL to ``rdf_artifacts`` (R4) + bust caches (R7)."""
    from app.models.rdf_artifact import RdfArtifact  # noqa: PLC0415

    async with session_scope() as db:
        ttl_text = await asyncio.to_thread(out_path.read_text, encoding="utf-8")
        existing = await db.get(RdfArtifact, run_id)
        if existing:
            existing.ttl_content = ttl_text
            existing.triples_count = result.triples_count
            existing.manuscripts_count = result.manuscripts_count
        else:
            db.add(RdfArtifact(
                run_id=run_id,
                ttl_content=ttl_text,
                triples_count=result.triples_count,
                manuscripts_count=result.manuscripts_count,
            ))
        await db.commit()

    for cache_file in out_path.parent.glob("graph_*.json"):
        try:
            cache_file.unlink(missing_ok=True)
        except OSError:
            pass
    for cache_file in out_path.parent.glob("graph_viewport_*.json"):
        try:
            cache_file.unlink(missing_ok=True)
        except OSError:
            pass
    try:
        from app.pipeline.research_graph import invalidate_cache as _inval  # noqa: PLC0415
        _inval(str(run_id))
    except Exception:  # noqa: BLE001 — cache invalidation is best-effort
        logger.warning("research graph cache invalidation failed for %s", run_id)
