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
from app.models.publication import (
    Publication,
    PublicationExecution,
    PublicationWriteIntent,
    PublicationWriteReceipt,
)
from app.models.run_job import (
    ACTIVE_JOB_STATUSES,
    JOB_KIND_WIKIDATA_GILA_CASE_REVIEW,
    JOB_KIND_WIKIDATA_GILA_REPAIR,
    JOB_KIND_WIKIDATA_PUBLICATION_EXECUTION,
    JOB_KIND_WIKIDATA_PUBLICATION_PREPARE,
    RunJob,
)
from app.pipeline.run_job_service import ActiveJobError, create_job, serialise_job
from app.pipeline.wikidata_gila_audit_job import KIND, gila_cases
from app.pipeline.wikidata_gila_case_review import reviewable
from app.pipeline.wikidata_publication_prepare_job import audited_batch_params
from app.pipeline.wikidata_reference_reader import ReferenceItemReader
from app.pipeline.wikidata_reference_repair_job import load_seed
from app.publication.reviewed_reference_scope import GILA_CREATOR_UID
from app.publication.credentials import SavedPublicationCredentialResolver, seal_execution_credential
from app.routers.admin_reference_repairs import latest as latest_reference_job
from app.routers.runs import _lookup_run_with_access
from app.schemas.publication import PreparePublicationRequest, RunPublicationSource

router = APIRouter(prefix="/admin/wikidata-gila-repairs", tags=["admin"])
Admin = Annotated[AuthContext, Depends(require_admin)]
Database = Annotated[AsyncSession, Depends(get_session)]
CaseStatus = Literal[
    "repair_required", "manual_review", "not_checked", "absent_in_source", "ready_to_prepare"
]


class AuditSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    audit_id: uuid.UUID


RepairQid = Annotated[str, Field(pattern=r"^Q[1-9][0-9]*$")]


class RepairAllRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    people_links: dict[RepairQid, RepairQid] = Field(default_factory=dict, max_length=50)
    requirement: Literal[5] | None = None
    scope: Literal["people_and_sources"] | None = None


class PrepareRepair(AuditSelection):
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
    repair = await db.scalar(select(RunJob).options(defer(RunJob.params)).where(
        RunJob.run_id == run.id, RunJob.kind == JOB_KIND_WIKIDATA_GILA_REPAIR,
    ).order_by(RunJob.created_at.desc(), RunJob.id.desc()).limit(1))
    return {
        "run_id": str(run.id),
        "job": _job_summary(job),
        "automatic_repair": {**_job_summary(repair), "result": repair.result} if repair else None,
        "report": {"id": str(report.id), "finished_at": report.finished_at, **report.result}
        if report
        else None,
    }


@router.post("/repair-all")
async def repair_all(auth: Admin, db: Database, body: RepairAllRequest | None = None):
    run = await _lookup_run_with_access(db, _run_id(), auth, write=True)
    if body and sum(bool(value) for value in (body.scope, body.requirement, body.people_links)) > 1:
        raise HTTPException(422, "Select one repair scope.")
    cycle = f"gila-repair:{uuid.uuid4()}"
    try:
        credential = await SavedPublicationCredentialResolver(db, auth.user.id, auth.kek).resolve(
            f"wikidata:production:{auth.user.id}",
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    try:
        job = await create_job(
            db, project_id=run.project_id, run_id=run.id,
            kind=JOB_KIND_WIKIDATA_GILA_REPAIR, created_by=auth.user.id,
            params={
                "_cycle": cycle,
                **({"_gila_people_links": body.people_links} if body and body.people_links else {}),
                **({"_gila_requirement": body.requirement} if body and body.requirement else {}),
                **({"_gila_scope": body.scope} if body and body.scope else {}),
                "_publication_credential": seal_execution_credential(
                    credential, publication_id=str(run.id), execution_id=cycle,
                ),
            },
        )
    except ActiveJobError as exc:
        job = await db.get(RunJob, exc.job_id)
        if job is None or job.created_by != auth.user.id:
            raise HTTPException(409, "Another administrator has an active repair job.") from exc
    return serialise_job(job)


@router.get("/preflight")
async def corpus_preflight(
    auth: Admin, db: Database, job_id: uuid.UUID,
    point: Literal["5", "8"] | None = None,
    status: Literal["eligible", "needs_evidence", "other_creator", "needs_review"] | None = None,
    offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100),
):
    run = await _lookup_run_with_access(db, _run_id(), auth)
    rows = await db.scalar(select(RunJob.params["_gila_corpus_preflight"]).where(
        RunJob.id == job_id, RunJob.run_id == run.id, RunJob.kind == JOB_KIND_WIKIDATA_GILA_REPAIR,
    ))
    if rows is None:
        raise HTTPException(404, "The corpus preflight is unavailable.")
    selected = [
        row for row in rows
        if (point is None or int(point) in row["points"]) and (status is None or status == row["status"])
    ]
    return {"items": selected[offset:offset + limit], "total": len(selected)}


@router.post("/start")
async def start(auth: Admin, db: Database):
    run = await _lookup_run_with_access(db, _run_id(), auth)
    try:
        job = await create_job(
            db,
            project_id=run.project_id,
            run_id=run.id,
            kind=KIND,
            params={"_gila_full_sources": True},
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
    execution_ids = await db.scalars(select(RunJob.params["execution_id"].as_string()).where(
        RunJob.run_id == run.id,
        RunJob.kind == JOB_KIND_WIKIDATA_PUBLICATION_EXECUTION,
        RunJob.params["_gila_cycle_parent"].as_string().is_not(None),
    ))
    manuscript_receipts = (await db.execute(select(
        PublicationExecution.publication_id, PublicationWriteReceipt.qid,
        PublicationWriteReceipt.fingerprint, PublicationWriteReceipt.created_at,
    ).join(PublicationWriteIntent, PublicationWriteIntent.execution_id == PublicationExecution.id)
        .join(PublicationWriteReceipt, PublicationWriteReceipt.intent_id == PublicationWriteIntent.id)
        .where(
            PublicationExecution.id.in_([uuid.UUID(value) for value in execution_ids if value]),
            PublicationWriteReceipt.status == "succeeded",
            PublicationWriteReceipt.created_at <= report.finished_at,
        ))).all() if report.finished_at else []
    return {
        "audit_id": str(report.id),
        "finished_at": report.finished_at,
        "requirements": report.result["email_points"],
        "all_requirements_verified": report.result["all_requirements_verified"],
        "checked_manuscripts": report.params.get("_gila_checked_manuscripts"),
        "reference_repairs": receipts,
        "manuscript_repairs": [
            {"publication_id": str(publication_id), "qid": qid, "fingerprint": fingerprint,
             "verified_at": verified_at}
            for publication_id, qid, fingerprint, verified_at in manuscript_receipts
        ],
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


@router.get("/case-review")
async def latest_case_review(
    auth: Admin,
    db: Database,
    audit_id: uuid.UUID,
    case_id: Annotated[str, Query(min_length=1, max_length=256)],
):
    run = await _lookup_run_with_access(db, _run_id(), auth)
    await _require_report(db, run.id, audit_id)
    job = await db.scalar(select(RunJob).where(
        RunJob.run_id == run.id,
        RunJob.kind == JOB_KIND_WIKIDATA_GILA_CASE_REVIEW,
        RunJob.params["_audit_id"].as_string() == str(audit_id),
        RunJob.params["_case_id"].as_string() == case_id,
    ).order_by(RunJob.created_at.desc(), RunJob.id.desc()).limit(1))
    return serialise_job(job) if job else None


@router.post("/case-review")
async def review_case(body: PrepareRepair, auth: Admin, db: Database):
    run = await _lookup_run_with_access(db, _run_id(), auth, write=True)
    report = await _require_report(db, run.id, body.audit_id)
    row = next((case for case in gila_cases(report) if case["case_id"] == body.case_id), None)
    if not row or not reviewable(row):
        raise HTTPException(409, "This case does not have a supported Kimi source check.")
    params = {"_audit_id": str(body.audit_id), "_case_id": body.case_id}
    try:
        job = await create_job(
            db, project_id=run.project_id, run_id=run.id,
            kind=JOB_KIND_WIKIDATA_GILA_CASE_REVIEW, params=params, created_by=auth.user.id,
        )
    except ActiveJobError as exc:
        job = await db.get(RunJob, exc.job_id)
        if job is None or job.params != params:
            raise HTTPException(409, "Wait for the current Kimi case review to finish.") from exc
    return serialise_job(job)


def _repair_jobs(run_id):
    return select(RunJob).where(
        RunJob.run_id == run_id,
        RunJob.kind == JOB_KIND_WIKIDATA_PUBLICATION_PREPARE,
        RunJob.params["_gila_source_fingerprint"].as_string().is_not(None),
        RunJob.params["_gila_build_snapshot"].as_string().is_not(None),
    )


def _repair_job_summary(job: RunJob):
    params = job.params
    result = job.result or {}
    targets = params.get("_selected_existing") or {}
    return {
        **_job_summary(job),
        "case_id": params.get("_gila_case_id"),
        "qids": result.get("prepared_qids", list(targets.values())),
        "publication_id": result.get("publication_id"),
        "batch_counts": result.get("batch_counts"),
        "exclusions": (result.get("exclusions") or [])[:50],
        "open_case_counts": params.get("_gila_open_case_counts"),
    }


@router.get("/preparations")
async def preparations(
    auth: Admin, db: Database,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
):
    run = await _lookup_run_with_access(db, _run_id(), auth)
    rows = (await db.scalars(_repair_jobs(run.id).order_by(
        RunJob.created_at.desc(), RunJob.id.desc(),
    ).offset(offset).limit(limit + 1))).all()
    return {"items": [_repair_job_summary(job) for job in rows[:limit]], "more": len(rows) > limit}


@router.post("/prepare-batch")
async def prepare_batch(body: AuditSelection, auth: Admin, db: Database):
    run = await _lookup_run_with_access(db, _run_id(), auth, write=True)
    report = await _require_report(db, run.id, body.audit_id)
    current = await db.scalar(_repair_jobs(run.id).where(RunJob.status.in_(ACTIVE_JOB_STATUSES)))
    if current:
        if (
            current.params.get("_gila_batch")
            and current.params.get("_gila_audit_id") == str(report.id)
        ):
            return serialise_job(current)
        raise HTTPException(409, "Wait for the active repair preparation to finish.")
    previous = await db.scalar(_repair_jobs(run.id).where(
        RunJob.params["_gila_audit_id"].as_string() == str(report.id),
        RunJob.params["_gila_batch"].as_string().is_not(None),
        RunJob.status == "succeeded",
    ).order_by(RunJob.created_at.desc(), RunJob.id.desc()).limit(1))
    publication_id = (previous.result or {}).get("publication_id") if previous else None
    if publication_id:
        publication = await db.get(Publication, uuid.UUID(publication_id))
        if publication is not None and publication.state not in {"failed", "cancelled"}:
            return serialise_job(previous)
    try:
        params = audited_batch_params(report, auth.user.id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if not params:
        return None
    try:
        job = await create_job(
            db, project_id=run.project_id, run_id=run.id,
            kind=JOB_KIND_WIKIDATA_PUBLICATION_PREPARE, created_by=auth.user.id,
            params=params,
        )
    except ActiveJobError as exc:
        job = await db.get(RunJob, exc.job_id)
        if (
            job is None or not job.params.get("_gila_batch")
            or job.params.get("_gila_audit_id") != str(body.audit_id)
        ):
            raise HTTPException(409, "Wait for the active repair preparation to finish.") from exc
    return serialise_job(job)


@router.post("/prepare-next")
async def prepare_next(body: AuditSelection, auth: Admin, db: Database):
    run = await _lookup_run_with_access(db, _run_id(), auth, write=True)
    report = await _require_report(db, run.id, body.audit_id)
    active = await db.scalar(_repair_jobs(run.id).where(RunJob.status.in_(ACTIVE_JOB_STATUSES)))
    if active:
        return serialise_job(active)
    ready = [case for case in gila_cases(report) if case.get("can_prepare")]
    if not ready:
        return None
    revision = report.params.get("_gila_build_revision")
    digest = report.params.get("_gila_build_digest")
    source = report.params.get("_gila_source_fingerprint")
    if not (revision and digest and source):
        raise HTTPException(409, "Run a fresh audit before preparation.")
    snapshot = RunJob.params["_gila_build_snapshot"]
    selected = RunJob.params["_selected_existing"]
    prepared_mappings = (await db.scalars(_repair_jobs(run.id).with_only_columns(selected).where(
        RunJob.status == "succeeded",
        snapshot[0].as_string() == revision,
        snapshot[1].as_string() == digest,
        RunJob.params["_gila_source_fingerprint"].as_string() == source,
    ))).all()
    prepared = {qid for mapping in prepared_mappings if mapping for qid in mapping.values()}
    row = next((case for case in ready if case.get("qid") not in prepared), None)
    return await _prepare_case(auth, db, run.id, body.audit_id, row) if row else None


@router.post("/prepare")
async def prepare(body: PrepareRepair, auth: Admin, db: Database):
    run = await _lookup_run_with_access(db, _run_id(), auth, write=True)
    report = await _require_report(db, run.id, body.audit_id)
    row = next((case for case in gila_cases(report) if case["case_id"] == body.case_id), None)
    if not row or not row.get("can_prepare"):
        raise HTTPException(409, "This case needs source or identity review before publication.")
    return await _prepare_case(auth, db, run.id, body.audit_id, row)


async def _prepare_case(auth, db, run_id, audit_id, row):
    await db.commit()
    try:
        creator_uid = await ReferenceItemReader().creator_uid(row["qid"])
    except ValueError as exc:
        raise HTTPException(409, "Wikidata did not confirm the item creator.") from exc
    if creator_uid != GILA_CREATOR_UID:
        raise HTTPException(403, "This repair requires first-revision creator UID 6935067.")
    run = await _lookup_run_with_access(db, run_id, auth, write=True)
    report = await _require_report(db, run.id, audit_id)
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
                "_gila_audit_id": str(audit_id),
                "_gila_case_id": row["case_id"],
                "_gila_build_snapshot": [
                    report.params["_gila_build_revision"], report.params["_gila_build_digest"],
                ],
                "_gila_source_fingerprint": report.params["_gila_source_fingerprint"],
                "_gila_troki_author": row.get("repair_kind") == "troki_author",
            },
            created_by=auth.user.id,
        )
    except ActiveJobError as exc:
        raise HTTPException(409, "Wait for the current Publication preparation to finish.") from exc
    return serialise_job(job)
