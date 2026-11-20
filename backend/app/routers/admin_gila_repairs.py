"""The administrator console for Gila's project-wide audit and repairs."""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from app.auth.admin import require_admin
from app.auth.session import AuthContext
from app.db import get_session
from app.models.run_job import JOB_KIND_WIKIDATA_PUBLICATION_PREPARE, RunJob
from app.pipeline.marc_verify_context import load_run_marc_records
from app.pipeline.run_job_service import ActiveJobError, create_job, serialise_job
from app.pipeline.wikidata_gila_audit_job import KIND, gila_cases, gila_record_fingerprint
from app.pipeline.wikidata_item_views import fetch_merged_wikidata_item
from app.pipeline.wikidata_reference_reader import ReferenceItemReader
from app.pipeline.wikidata_reference_repair_job import load_seed
from app.publication.reviewed_reference_scope import GILA_CREATOR_UID
from app.publication.runtime import PublicationSourceError, StudioCacheProjectionSource
from app.routers.admin_reference_repairs import latest as latest_reference_job
from app.routers.runs import _lookup_run_with_access
from app.schemas.publication import PreparePublicationRequest, RunPublicationSource

router = APIRouter(prefix="/admin/wikidata-gila-repairs", tags=["admin"])
Admin = Annotated[AuthContext, Depends(require_admin)]
Database = Annotated[AsyncSession, Depends(get_session)]
CaseStatus = Literal[
    "repair_required", "manual_review", "not_checked", "absent_in_source", "ready_to_prepare"
]


class PrepareRepair(BaseModel):
    model_config = ConfigDict(extra="forbid")
    audit_id: uuid.UUID
    case_id: str = Field(min_length=1, max_length=256)


def _run_id() -> uuid.UUID:
    seed, _ = load_seed()
    return uuid.UUID(seed["manifest"]["run_id"])


async def _latest(db, run_id, *, succeeded=False, thin=False):
    query = select(RunJob).where(RunJob.run_id == run_id, RunJob.kind == KIND)
    if succeeded:
        query = query.where(RunJob.status == "succeeded")
    if thin:
        query = query.options(defer(RunJob.params))
    return await db.scalar(query.order_by(RunJob.created_at.desc(), RunJob.id.desc()).limit(1))


def _job_summary(job):
    if not job:
        return None
    return {
        "id": str(job.id),
        "status": job.status,
        "progress": job.progress or {},
        "error": job.error,
        "finished_at": job.finished_at,
        "updated_at": job.updated_at,
    }


@router.get("")
async def status(auth: Admin, db: Database):
    run = await _lookup_run_with_access(db, _run_id(), auth)
    job = await _latest(db, run.id, thin=True)
    report = (
        job
        if job and job.status == "succeeded"
        else await _latest(db, run.id, succeeded=True, thin=True)
    )
    return {
        "run_id": str(run.id),
        "job": _job_summary(job),
        "report": {"id": str(report.id), "finished_at": report.finished_at, **report.result}
        if report
        else None,
    }


@router.post("/start")
async def start(auth: Admin, db: Database):
    run = await _lookup_run_with_access(db, _run_id(), auth)
    try:
        job = await create_job(
            db,
            project_id=run.project_id,
            run_id=run.id,
            kind=KIND,
            params={},
            created_by=auth.user.id,
        )
    except ActiveJobError as exc:
        job = await db.get(RunJob, exc.job_id)
    return _job_summary(job)


async def _require_report(db, run_id, audit_id):
    report = await _latest(db, run_id, succeeded=True)
    if report is None or report.id != audit_id:
        raise HTTPException(409, "The audit changed. Refresh the case list.")
    return report


@router.get("/evidence")
async def evidence(auth: Admin, db: Database, audit_id: uuid.UUID):
    run = await _lookup_run_with_access(db, _run_id(), auth)
    report = await _require_report(db, run.id, audit_id)
    seed, _ = load_seed()
    original_checkpoint = seed["checkpoint"] if seed["manifest"]["run_id"] == str(run.id) else {}
    receipts = []
    for scope in ("original", "remaining"):
        job = await latest_reference_job(db, run.id, scope)
        checkpoint = (
            job.params.get("_reference_checkpoint", {})
            if job
            else original_checkpoint if scope == "original" else {}
        )
        for row in checkpoint.get("results", []):
            if row.get("status") == "verified" and row.get("corrected") is True:
                receipts.append({
                    "scope": scope,
                    **{key: row.get(key) for key in (
                        "qid", "before_revision", "after_revision", "completed_at",
                    )},
                })
    return {
        "audit_id": str(report.id),
        "finished_at": report.finished_at,
        "requirements": report.result["email_points"],
        "all_requirements_verified": report.result["all_requirements_verified"],
        "checked_manuscripts": report.params.get("_gila_checked_manuscripts"),
        "reference_repairs": receipts,
        "unchecked_passages": report.result["requirement_coverage"]["unchecked_passages"],
        "review_cases": [
            {key: row[key] for key in (
                "case_id", "points", "qid", "qids", "control_number",
                "disposition", "classification", "reason", "completion_reason", "can_prepare",
            ) if key in row}
            for row in gila_cases(report)
            if not row["case_id"].startswith("passage:")
        ],
    }


@router.get("/cases")
async def cases(
    auth: Admin,
    db: Database,
    audit_id: uuid.UUID,
    point: Annotated[int | None, Query(ge=1, le=9)] = None,
    disposition: CaseStatus | None = None,
    query: Annotated[str, Query(max_length=120)] = "",
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=50)] = 25,
):
    run = await _lookup_run_with_access(db, _run_id(), auth)
    report = await _require_report(db, run.id, audit_id)
    term = query.strip().casefold()
    filtered = [
        row
        for row in gila_cases(report)
        if (point is None or point in row["points"])
        and (
            disposition is None
            or (disposition == "ready_to_prepare" and row.get("can_prepare") is True)
            or (
                row.get("classification") == "absent_in_source"
                if disposition == "absent_in_source"
                else row["disposition"] == disposition
                and row.get("classification") != "absent_in_source"
            )
        )
        and (
            not term
            or any(
                term in str(row.get(key, "")).casefold()
                for key in (
                    "qid",
                    "qids",
                    "control_number",
                    "source_value",
                    "expected_author",
                )
            )
        )
    ]
    return {"items": filtered[offset : offset + limit], "total": len(filtered)}


@router.post("/prepare")
async def prepare(body: PrepareRepair, auth: Admin, db: Database):
    run = await _lookup_run_with_access(db, _run_id(), auth, write=True)
    report = await _require_report(db, run.id, body.audit_id)
    row = next((case for case in gila_cases(report) if case["case_id"] == body.case_id), None)
    if not row or not row.get("can_prepare"):
        raise HTTPException(409, "This case needs source or identity review before publication.")
    run_id = run.id
    await db.commit()
    try:
        creator_uid = await ReferenceItemReader().creator_uid(row["qid"])
    except ValueError as exc:
        raise HTTPException(409, "Wikidata did not confirm the item creator.") from exc
    if creator_uid != GILA_CREATOR_UID:
        raise HTTPException(403, "This repair requires first-revision creator UID 6935067.")
    run = await _lookup_run_with_access(db, run_id, auth, write=True)
    report = await _require_report(db, run.id, body.audit_id)
    try:
        snapshot = await StudioCacheProjectionSource(db).current_snapshot(
            run_id=run.id,
            source="canonical",
            approved_only=True,
        )
    except PublicationSourceError as exc:
        raise HTTPException(409, "Build the canonical Studio source and audit it again.") from exc
    if (
        snapshot.revision != report.params["_gila_build_revision"]
        or snapshot.digest != report.params["_gila_build_digest"]
        or gila_record_fingerprint(await load_run_marc_records(db, run.id))
        != report.params["_gila_source_fingerprint"]
    ):
        raise HTTPException(409, "The source changed. Run a new audit before preparation.")
    item = await fetch_merged_wikidata_item(db, run.id, row["local_id"], source="canonical")
    if not item or item.get("rebuild_required") or item.get("existing_qid") != row["qid"]:
        raise HTTPException(409, "The selected item changed. Rebuild Studio and run a new audit.")
    payload = PreparePublicationRequest(
        profile_id="mhm-wikidata",
        profile_version="1-nodes",
        target="live",
        source=RunPublicationSource(projection_source="canonical"),
    )
    try:
        job = await create_job(
            db,
            project_id=run.project_id,
            run_id=run.id,
            kind=JOB_KIND_WIKIDATA_PUBLICATION_PREPARE,
            params={
                "actor_id": str(auth.user.id),
                "request": payload.model_dump(mode="json"),
                "_selected_existing": {row["local_id"]: row["qid"]},
                "_gila_build_snapshot": [snapshot.revision, snapshot.digest],
            },
            created_by=auth.user.id,
        )
    except ActiveJobError as exc:
        raise HTTPException(409, "Wait for the current Publication preparation to finish.") from exc
    return serialise_job(job)
