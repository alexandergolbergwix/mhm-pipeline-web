"""Background AI Extraction job with per-record progress + cancel."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from sqlalchemy import select

from app.db import session_scope
from app.models.run import RunRecord
from app.models.run_job import (
    JOB_STATUS_CANCELLED,
    JOB_STATUS_FAILED,
    JOB_STATUS_SUCCEEDED,
    RunJob,
)
from app.pipeline.extraction import ExtractionEvent, extract_entities_stream
from app.pipeline.run_job_service import (
    finish_job,
    is_cancel_requested,
    update_job_progress,
)
from app.routers.extraction import (
    _bulk_persist_entities,
    _results_path,
    _run_output_dir,
)

logger = logging.getLogger(__name__)


def _enabled_models_from_params(params: dict[str, Any]) -> set[str] | None:
    raw = params.get("models")
    if raw is None:
        return None
    if isinstance(raw, list):
        picked = {str(x).strip() for x in raw if str(x).strip()}
    else:
        picked = {p.strip() for p in str(raw).split(",") if p.strip()}
    allowed = {"person", "provenance", "contents", "genre"}
    enabled = picked & allowed
    return enabled or None


async def run_extraction_job(job_id: uuid.UUID) -> None:
    async with session_scope() as db:
        job = await db.get(RunJob, job_id)
        if job is None:
            return
        run_id = job.run_id
        params = job.params or {}
        hf_token = params.get("_hf_token")
        if not hf_token:
            await finish_job(job_id, status=JOB_STATUS_FAILED, error="missing HF token")
            return

        rows = (
            await db.execute(
                select(RunRecord)
                .where(RunRecord.run_id == run_id)
                .order_by(RunRecord.control_number.asc())
            )
        ).scalars().all()
        if not rows:
            await finish_job(job_id, status=JOB_STATUS_FAILED, error="run has no records")
            return

        from app.pipeline.marc_ingest import _collapse_marc_subfields  # noqa: PLC0415

        marc_records: list[dict[str, Any]] = []
        for r in rows:
            rec = dict(r.marc) if r.marc else {}
            if any("$" in k for k in rec):
                _collapse_marc_subfields(rec)
            marc_records.append(rec)

        user_id = job.created_by
        skip_cache = bool(params.get("skip_cache"))
        mode = params.get("mode")
        enabled = _enabled_models_from_params(params)
        output_dir = _run_output_dir(run_id)

    total = len(marc_records)
    await update_job_progress(job_id, {
        "phase": "warming",
        "processed": 0,
        "total": total,
        "message": "Starting extraction…",
    })

    entity_total = 0
    records_processed = 0
    cancelled = False
    error_message: str | None = None

    async def cancel_check() -> bool:
        return await is_cancel_requested(job_id)

    async with session_scope() as db:
        stream = extract_entities_stream(
            marc_records=marc_records,
            output_dir=output_dir,
            hf_token=str(hf_token),
            mode=str(mode) if mode else None,
            enabled_models=enabled,
            db_session=db,
            user_id=user_id,
            skip_cache=skip_cache,
            cancel_check=cancel_check,
        )

        async for ev in stream:
            if isinstance(ev, ExtractionEvent):
                etype = ev.type
                payload = ev.payload
            else:
                etype = getattr(ev, "type", "")
                payload = getattr(ev, "payload", {})

            if etype == "extraction.start":
                await update_job_progress(job_id, {
                    "phase": "running",
                    "processed": 0,
                    "total": int(payload.get("total") or total),
                    "message": "Extraction started",
                    "mode": payload.get("mode"),
                })
            elif etype == "extraction.step":
                await update_job_progress(job_id, {
                    "phase": str(payload.get("phase") or "running"),
                    "processed": records_processed,
                    "total": total,
                    "message": str(payload.get("message") or ""),
                })
            elif etype == "extraction.record.done":
                records_processed = int(payload.get("index", records_processed)) + 1
                cn = str(payload.get("control_number") or "")
                await update_job_progress(job_id, {
                    "phase": "running",
                    "processed": records_processed,
                    "total": total,
                    "message": cn,
                    "current_control_number": cn,
                })
            elif etype == "extraction.end":
                entity_total = int(payload.get("entity_total") or 0)
                records_processed = int(payload.get("records_processed") or total)
            elif etype == "extraction.cancelled":
                cancelled = True
                records_processed = int(payload.get("records_processed") or records_processed)
                entity_total = int(payload.get("entity_total") or entity_total)
                break
            elif etype == "extraction.error":
                error_message = str(payload.get("message") or "extraction failed")
                break

    path = _results_path(run_id)
    if path.exists():
        try:
            async with session_scope() as db:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    await _bulk_persist_entities(db, run_id, data)
                    await db.commit()
        except Exception:  # noqa: BLE001
            logger.exception("extraction job: entity persistence failed for %s", run_id)

    if error_message:
        await finish_job(job_id, status=JOB_STATUS_FAILED, error=error_message)
        return

    if cancelled:
        await finish_job(
            job_id,
            status=JOB_STATUS_CANCELLED,
            result={
                "records_processed": records_processed,
                "entity_total": entity_total,
            },
            progress={
                "phase": "cancelled",
                "processed": records_processed,
                "total": total,
                "message": "Cancelled by user",
            },
        )
        return

    await finish_job(
        job_id,
        status=JOB_STATUS_SUCCEEDED,
        result={
            "records_processed": records_processed,
            "entity_total": entity_total,
        },
        progress={
            "phase": "done",
            "processed": total,
            "total": total,
            "message": "Complete",
        },
    )
