"""Background RDF build job."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import select

from app.db import session_scope
from app.models.run import AuthorityMatch, RdfTripleOverride, RunRecord
from app.models.rdf_artifact import RdfArtifact
from app.models.extraction_approval import ExtractionApproval
from app.models.run_job import (
    JOB_STATUS_CANCELLED,
    JOB_STATUS_FAILED,
    JOB_STATUS_SUCCEEDED,
    RunJob,
)
from app.pipeline.rdf_build import (
    RdfBuildOptions,
    build_rdf_graph,
    normalise_matches,
    rdf_output_path_for_run,
)
from app.pipeline.run_job_service import (
    finish_job,
    is_cancel_requested,
    update_job_progress,
)

logger = logging.getLogger(__name__)


async def run_rdf_build_job(job_id: uuid.UUID) -> None:
    async with session_scope() as db:
        job = await db.get(RunJob, job_id)
        if job is None:
            return
        if await is_cancel_requested(job_id):
            await finish_job(job_id, status=JOB_STATUS_CANCELLED)
            return

        run_id = job.run_id
        params = job.params or {}
        records = (
            await db.execute(
                select(RunRecord)
                .where(RunRecord.run_id == run_id)
                .order_by(RunRecord.control_number.asc())
            )
        ).scalars().all()
        if not records:
            await finish_job(job_id, status=JOB_STATUS_FAILED, error="run has no records")
            return

        matches = (
            await db.execute(
                select(AuthorityMatch)
                .where(AuthorityMatch.run_id == run_id)
                .where(AuthorityMatch.approved.is_(True))
            )
        ).scalars().all()
        ner_rows = (
            await db.execute(
                select(ExtractionApproval)
                .where(ExtractionApproval.run_id == run_id)
                .where(ExtractionApproval.approved.is_(True))
            )
        ).scalars().all()
        overrides_rows = (
            await db.execute(
                select(RdfTripleOverride).where(RdfTripleOverride.run_id == run_id)
            )
        ).scalars().all()

        marc_records = [dict(r.marc) for r in records]
        authority_matches = normalise_matches(matches)
        entities_by_cn: dict[str, list[dict[str, Any]]] = {}
        for r in ner_rows:
            entities_by_cn.setdefault(r.control_number, []).append({
                "text":             r.override_text or r.text,
                "type":             (r.override_type or r.type or "").upper(),
                "role":             (r.override_role or r.role or "").upper(),
                "source":           r.source,
                "start":            int(r.start or 0),
                "end":              int(r.end or 0),
                "confidence":       r.confidence,
                "model_confidence": r.model_confidence,
            })
        kima_places_by_cn: dict[str, dict[str, str]] = {}
        for rec in marc_records:
            cn = str(rec.get("_control_number") or rec.get("control_number") or "")
            kp = rec.get("kima_places")
            if cn and isinstance(kp, dict) and kp:
                kima_places_by_cn[cn.strip("\"'")] = kp
        overrides = [
            {
                "subject_uri": r.subject_uri,
                "predicate_uri": r.predicate_uri,
                "new_value": r.new_value,
                "new_datatype": r.new_datatype,
                "new_lang": r.new_lang,
            }
            for r in overrides_rows
        ]
        opts = RdfBuildOptions(
            add_epistemological_status=bool(params.get("add_epistemological_status", True)),
            add_cataloging_view=bool(params.get("add_cataloging_view", True)),
            add_philological_overlay=bool(params.get("add_philological_overlay", True)),
        )
        total = len(marc_records)

    await update_job_progress(job_id, {
        "phase": "building",
        "processed": 0,
        "total": total,
        "message": f"Building RDF for {total} records…",
    })

    if await is_cancel_requested(job_id):
        await finish_job(job_id, status=JOB_STATUS_CANCELLED)
        return

    out_path = rdf_output_path_for_run(str(run_id))
    try:
        async def _report_progress(payload: dict[str, Any]) -> None:
            await update_job_progress(job_id, payload)

        result = await build_rdf_graph(
            marc_records=marc_records,
            authority_matches=authority_matches,
            entities_by_cn=entities_by_cn,
            output_path=out_path,
            overrides=overrides,
            kima_places_by_cn=kima_places_by_cn,
            build_options=opts,
            on_progress=_report_progress,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("RDF build job failed for run %s", run_id)
        await finish_job(job_id, status=JOB_STATUS_FAILED, error=str(exc))
        return

    if await is_cancel_requested(job_id):
        await finish_job(job_id, status=JOB_STATUS_CANCELLED)
        return

    async with session_scope() as db:
        ttl_text = out_path.read_text(encoding="utf-8")
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
    except Exception:  # noqa: BLE001
        pass

    await finish_job(
        job_id,
        status=JOB_STATUS_SUCCEEDED,
        result=result.to_dict(),
        progress={
            "phase": "done",
            "processed": total,
            "total": total,
            "message": "RDF build complete",
        },
    )
