"""Run lifecycle + approvals.

Endpoints (RBAC notes):

* ``POST   /projects/{id}/runs``            — editor+
* ``GET    /projects/{id}/runs``            — viewer+
* ``GET    /runs/{id}``                     — viewer+ (project-scoped via lookup)
* ``GET    /runs/{id}/matches``             — viewer+
* ``GET    /runs/{id}/records/{cn}``        — viewer+ (popup with full MARC)
* ``PATCH  /runs/{id}/matches/{mid}``       — editor+ (toggle approval)
* ``POST   /runs/{id}/matches/bulk-approve``— editor+
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.project_perms import (
    ProjectContext,
    require_editor,
    require_viewer,
)
from app.auth.session import AuthContext, current_auth
from app.db import get_session
from app.events import append_event
from app.models.event import (
    ENTITY_TYPE_AUTHORITY_MATCH,
    ENTITY_TYPE_MARC_RECORD,
    OP_CREATE,
    OP_PATCH,
    ProjectEvent,
)
from app.models.project import (
    PROJECT_ROLE_EDITOR,
    PROJECT_ROLE_OWNER,
    PROJECT_ROLE_VIEWER,
    Membership,
    Project,
)
from app.models.run import AuthorityMatch, Run, RunRecord
from app.pipeline import ai_verifier
from app.pipeline.run import execute_run, serialise_match
from app.schemas.runs import (
    AiVerdictResponse,
    ApprovalBatch,
    ApprovalUpdate,
    AuthorityMatchEdit,
    AuthorityMatchResponse,
    RunDetail,
    RunListItem,
    RecordEdit,
    RunMarcRecord,
)
from app.versioning import apply_event


logger = logging.getLogger(__name__)


router = APIRouter(tags=["runs"])


# ── Per-project: list + create ─────────────────────────────────────────


@router.get("/projects/{project_id}/runs", response_model=list[RunListItem])
async def list_runs(
    ctx: ProjectContext = Depends(require_viewer),
    db: AsyncSession = Depends(get_session),
) -> list[RunListItem]:
    rows = (
        await db.execute(
            select(Run).where(Run.project_id == ctx.project.id).order_by(desc(Run.created_at))
        )
    ).scalars().all()
    return [_to_list_item(r) for r in rows]


@router.post(
    "/projects/{project_id}/runs", response_model=RunListItem,
    status_code=status.HTTP_201_CREATED,
)
async def create_run(
    file: UploadFile = File(..., description="MARC JSON or JSONL upload"),
    ctx: ProjectContext = Depends(require_editor),
    db: AsyncSession = Depends(get_session),
) -> RunListItem:
    raw = await file.read()
    if len(raw) > 25 * 1024 * 1024:  # 25 MB hard ceiling
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Upload exceeds 25 MB",
        )
    run = Run(
        project_id=ctx.project.id,
        created_by=ctx.user_id,
        name=(file.filename or "Untitled run").rsplit(".", 1)[0][:200],
    )
    db.add(run)
    await db.flush()
    await execute_run(db, run=run, upload=raw, filename=file.filename)

    # Emit OP_CREATE events for every MARC record persisted in this run.
    # Versioning is best-effort; a failure here must NEVER block the upload.
    inserted_records = (
        await db.execute(
            select(RunRecord).where(RunRecord.run_id == run.id)
        )
    ).scalars().all()
    for rec in inserted_records:
        try:
            await apply_event(
                db,
                project_id=ctx.project.id,
                entity_type=ENTITY_TYPE_MARC_RECORD,
                entity_id=f"{run.id}:{rec.control_number}",
                op=OP_CREATE,
                new_state=rec.marc,
                actor_id=ctx.user_id,
                message="MARC upload",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "apply_event failed for marc_record %s: %s", rec.control_number, exc,
            )

    await append_event(
        db, project_id=ctx.project.id, actor_id=ctx.user_id, type="run.created",
        payload={"run_id": str(run.id), "name": run.name,
                 "records": run.record_count, "matches": run.match_count,
                 "status": run.status},
    )
    await db.commit()
    return _to_list_item(run)


# ── Per-run: detail + matches + per-record MARC popup ───────────────────


@router.get("/runs/{run_id}", response_model=RunDetail)
async def get_run(
    run_id: uuid.UUID,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> RunDetail:
    run = await _lookup_run_with_access(db, run_id, auth)
    matches = (
        await db.execute(
            select(AuthorityMatch)
            .where(AuthorityMatch.run_id == run.id)
            .order_by(AuthorityMatch.control_number.asc(), AuthorityMatch.entity_text.asc())
        )
    ).scalars().all()
    payload = _to_list_item(run).model_dump()
    payload["matches"] = [serialise_match(m) for m in matches]
    return RunDetail(**payload)


@router.get("/runs/{run_id}/matches", response_model=list[AuthorityMatchResponse])
async def list_matches(
    run_id: uuid.UUID,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> list[AuthorityMatchResponse]:
    await _lookup_run_with_access(db, run_id, auth)
    matches = (
        await db.execute(
            select(AuthorityMatch)
            .where(AuthorityMatch.run_id == run_id)
            .order_by(AuthorityMatch.control_number.asc(), AuthorityMatch.entity_text.asc())
        )
    ).scalars().all()
    return [AuthorityMatchResponse(**serialise_match(m)) for m in matches]


@router.get(
    "/runs/{run_id}/records/{control_number}",
    response_model=RunMarcRecord,
)
async def get_record(
    run_id: uuid.UUID, control_number: str,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> RunMarcRecord:
    await _lookup_run_with_access(db, run_id, auth)
    rec = (
        await db.execute(
            select(RunRecord).where(
                RunRecord.run_id == run_id, RunRecord.control_number == control_number,
            )
        )
    ).scalar_one_or_none()
    if rec is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Record not found")
    return RunMarcRecord(control_number=rec.control_number, marc=rec.marc)


# ── Approvals (editor+) ────────────────────────────────────────────────


@router.patch(
    "/runs/{run_id}/matches/{match_id}", response_model=AuthorityMatchResponse,
)
async def update_approval(
    run_id: uuid.UUID, match_id: uuid.UUID,
    payload: ApprovalUpdate,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> AuthorityMatchResponse:
    await _lookup_run_with_access(db, run_id, auth, write=True)
    m = (
        await db.execute(
            select(AuthorityMatch).where(
                AuthorityMatch.id == match_id, AuthorityMatch.run_id == run_id,
            )
        )
    ).scalar_one_or_none()
    if m is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Match not found")
    _apply_approval(m, payload.approved, auth.user.id)
    # Look up the project_id for the event log.
    run_for_pid = (await db.execute(select(Run).where(Run.id == run_id))).scalar_one()
    await _record_match_event(
        db, project_id=run_for_pid.project_id, actor_id=auth.user.id, row=m,
    )
    await db.commit()
    return AuthorityMatchResponse(**serialise_match(m))


@router.post(
    "/runs/{run_id}/matches/bulk-approve", response_model=list[AuthorityMatchResponse],
)
async def bulk_approve(
    run_id: uuid.UUID, payload: ApprovalBatch,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> list[AuthorityMatchResponse]:
    await _lookup_run_with_access(db, run_id, auth, write=True)
    if not payload.match_ids:
        return []
    rows = (
        await db.execute(
            select(AuthorityMatch).where(
                AuthorityMatch.run_id == run_id,
                AuthorityMatch.id.in_(payload.match_ids),
            )
        )
    ).scalars().all()
    for m in rows:
        _apply_approval(m, payload.approved, auth.user.id)
    if rows:
        run_for_pid = (await db.execute(select(Run).where(Run.id == run_id))).scalar_one()
        for m in rows:
            await _record_match_event(
                db, project_id=run_for_pid.project_id,
                actor_id=auth.user.id, row=m,
            )
    await db.commit()
    return [AuthorityMatchResponse(**serialise_match(m)) for m in rows]


# ── Backfill: enrich existing matches with birth/death years ──────────


@router.post("/runs/{run_id}/matches/backfill-dates")
async def backfill_dates(
    run_id: uuid.UUID,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> dict:
    """Patch payload.birth_year / payload.death_year on every match in
    the run, in place, using the IDs already stored (mazal_id, viaf_id,
    wikidata_qid). Doesn't re-match — preserves approvals, sources, the
    confidence verdict. Designed for runs that were created before the
    matchers started populating years.

    Returns how many rows were updated and how many years were filled.
    """
    await _lookup_run_with_access(db, run_id, auth, write=True)

    # Lazy-import so this endpoint doesn't pull the converter tree on
    # cold start.
    from app.pipeline.authority import get_default_matcher  # noqa: PLC0415
    from converter.transformer.date_resolver import (        # noqa: PLC0415
        resolve_person_dates,
    )

    rows = (
        await db.execute(
            select(AuthorityMatch).where(AuthorityMatch.run_id == run_id)
        )
    ).scalars().all()

    matcher = get_default_matcher()
    mazal    = matcher._mazal    if matcher else None     # type: ignore[attr-defined]
    viaf     = matcher._viaf     if matcher else None     # type: ignore[attr-defined]
    wikidata = matcher._wikidata if matcher else None     # type: ignore[attr-defined]

    touched_rows = 0
    births_filled = 0
    deaths_filled = 0

    for m in rows:
        payload = dict(m.payload or {})
        birth = payload.get("birth_year")
        death = payload.get("death_year")
        if birth and death:
            continue   # nothing to do — already enriched

        new_b: int | None = birth if isinstance(birth, int) else None
        new_d: int | None = death if isinstance(death, int) else None

        # — Mazal: cheap, in-process sqlite. Try first because for
        # medieval Hebrew it's the most authoritative source.
        if (new_b is None or new_d is None) and m.mazal_id and mazal is not None:
            try:
                details = mazal.get_person_details(m.mazal_id) or {}
                dstr = (details.get("dates") or "").strip()
                if dstr:
                    parsed = resolve_person_dates(dstr)
                    if new_b is None and parsed.get("birth_year"):
                        new_b = parsed["birth_year"]
                    if new_d is None and parsed.get("death_year"):
                        new_d = parsed["death_year"]
            except Exception:  # noqa: BLE001
                pass

        # — VIAF: each call hits the network; honour rate limits.
        if (new_b is None or new_d is None) and m.viaf_id and viaf is not None:
            try:
                cluster = viaf.get_cluster_identifiers(m.viaf_id) or {}
                # _year_from is private; re-derive defensively.
                from converter.authority.viaf_matcher import _year_from   # noqa: PLC0415
                b = _year_from(cluster.get("birth_date"))
                d = _year_from(cluster.get("death_date"))
                if new_b is None and b is not None:
                    new_b = int(b)
                if new_d is None and d is not None:
                    new_d = int(d)
            except Exception:  # noqa: BLE001
                pass

        # — Wikidata: SPARQL probe; on-disk cache amortises repeat calls.
        if (new_b is None or new_d is None) and m.wikidata_qid and wikidata is not None:
            try:
                b, d = wikidata.find_dates_by_qid(m.wikidata_qid)
                if new_b is None and b is not None:
                    new_b = int(b)
                if new_d is None and d is not None:
                    new_d = int(d)
            except Exception:  # noqa: BLE001
                pass

        changed = False
        if new_b is not None and new_b != payload.get("birth_year"):
            payload["birth_year"] = new_b; changed = True
            if not birth: births_filled += 1
        if new_d is not None and new_d != payload.get("death_year"):
            payload["death_year"] = new_d; changed = True
            if not death: deaths_filled += 1
        if changed:
            # JSON column needs a fresh dict to register the mutation.
            m.payload = payload
            touched_rows += 1

    await db.commit()
    return {
        "checked":       len(rows),
        "updated":       touched_rows,
        "births_filled": births_filled,
        "deaths_filled": deaths_filled,
    }


# ── Authority hardening rebuild ───────────────────────────────────────


@router.post("/runs/{run_id}/authority/rebuild")
async def rebuild_authority_guards(
    run_id: uuid.UUID,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> dict:
    """Re-run the seven hardening guards on every persisted match.

    Walks every ``AuthorityMatch`` row for the run, groups by control
    number so the cross-row guards (cluster collapse, mazal-pair
    collision) have full sibling context, and re-applies the
    hardening orchestrator. Updates ``payload.guard_flags`` and
    downgrades ``confidence`` when any guard fires. Does NOT re-match
    against external APIs — this is a pure hardening pass over the
    data already in the DB.

    Returns counts: ``checked``, ``downgraded``, ``flags_added``.
    """
    await _lookup_run_with_access(db, run_id, auth, write=True)

    from app.pipeline import authority_hardening  # noqa: PLC0415

    rows = (
        await db.execute(
            select(AuthorityMatch).where(AuthorityMatch.run_id == run_id)
        )
    ).scalars().all()

    # Bucket rows by control number so siblings travel with the candidate
    # through the cross-row guards.
    by_cn: dict[str, list[AuthorityMatch]] = {}
    for m in rows:
        by_cn.setdefault(m.control_number, []).append(m)

    checked = 0
    downgraded = 0
    flags_added = 0
    confidence_rank = {"low": 0, "medium": 1, "high": 2}

    for cn_rows in by_cn.values():
        # Serialise each row into the shape the orchestrator expects.
        snapshots: list[dict[str, Any]] = [
            {
                "matched_name": m.matched_name,
                "entity_text": m.entity_text,
                "entity_kind": m.entity_kind,
                "role": m.role,
                "confidence": m.confidence,
                "mazal_id": m.mazal_id,
                "viaf_id": m.viaf_id,
                "wikidata_qid": m.wikidata_qid,
                "payload": dict(m.payload or {}),
                "_db_row": m,  # private — popped before passing to guards
            }
            for m in cn_rows
        ]

        for snap in snapshots:
            m = snap.pop("_db_row")
            checked += 1
            payload_pre = dict(snap.get("payload") or {})
            old_flags = set(payload_pre.get("guard_flags") or [])
            old_conf = snap["confidence"]

            siblings = [
                {k: v for k, v in other.items() if k != "_db_row"}
                for other in snapshots if other is not snap
            ]
            ctx = authority_hardening.HardeningContext(
                siblings=siblings,
                preferred_name_lat=payload_pre.get("preferred_name_lat"),
                biographical_dates_in_marc=bool(
                    payload_pre.get("birth_year") or payload_pre.get("death_year")
                ),
                entity_kind=str(snap.get("entity_kind") or "person"),
                enable_wikidata_crosscheck=False,
            )
            hardened = authority_hardening.apply_hardening_guards(snap, context=ctx)

            new_flags = set(hardened["payload"].get("guard_flags") or [])
            added = new_flags - old_flags
            flags_added += len(added)

            new_conf = str(hardened["confidence"])
            conf_changed = (
                confidence_rank.get(new_conf, 1) < confidence_rank.get(old_conf, 1)
            )
            if conf_changed:
                downgraded += 1

            # Re-derive source from the IDs that survived hardening.
            sources = [
                s for s, val in (
                    ("mazal", hardened["mazal_id"]),
                    ("viaf", hardened["viaf_id"]),
                    ("wikidata", hardened["wikidata_qid"]),
                ) if val
            ]
            if len(sources) >= 2:
                new_source = "cross_source"
            elif sources:
                new_source = sources[0]
            else:
                new_source = m.source or ""

            # Persist back. JSON column needs a fresh dict to register.
            payload_post = dict(hardened["payload"])
            payload_post["sources"] = sources
            payload_post["source_count"] = len(sources)
            m.confidence = new_conf
            m.mazal_id = hardened["mazal_id"] or ""
            m.viaf_id = hardened["viaf_id"] or ""
            m.wikidata_qid = hardened["wikidata_qid"] or ""
            m.source = new_source
            m.payload = payload_post

    await db.commit()
    return {
        "checked": checked,
        "downgraded": downgraded,
        "flags_added": flags_added,
    }


# ── Editable fields (curator overrides on matches + records) ──────────


@router.patch(
    "/runs/{run_id}/matches/{match_id}/edit", response_model=AuthorityMatchResponse,
)
async def edit_match(
    run_id: uuid.UUID, match_id: uuid.UUID,
    payload: AuthorityMatchEdit,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> AuthorityMatchResponse:
    """Hand-edit any of the authority match fields the curator can fix
    in the Match Detail dialog (matched_name, IDs, confidence, role,
    entity_text). Approval state is *not* touched here — use
    :func:`update_approval` for that."""
    await _lookup_run_with_access(db, run_id, auth, write=True)
    m = (
        await db.execute(
            select(AuthorityMatch).where(
                AuthorityMatch.id == match_id, AuthorityMatch.run_id == run_id,
            )
        )
    ).scalar_one_or_none()
    if m is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Match not found")

    changes: dict[str, dict[str, Any]] = {}
    fields = ("matched_name", "mazal_id", "viaf_id", "wikidata_qid",
              "confidence", "role", "entity_text")
    for field in fields:
        new = getattr(payload, field, None)
        if new is None:
            continue
        old = getattr(m, field)
        if new != old:
            changes[field] = {"from": old, "to": new}
            setattr(m, field, new)

    if changes:
        run_for_pid = (await db.execute(select(Run).where(Run.id == run_id))).scalar_one()
        await append_event(
            db, project_id=run_for_pid.project_id, actor_id=auth.user.id,
            type="match.edited",
            payload={"run_id": str(run_id), "match_id": str(m.id), "changes": changes},
        )
    await db.commit()
    return AuthorityMatchResponse(**serialise_match(m))


@router.patch(
    "/runs/{run_id}/records/{control_number}", response_model=RunMarcRecord,
)
async def edit_record(
    run_id: uuid.UUID, control_number: str,
    payload: RecordEdit,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> RunMarcRecord:
    """Hand-edit the MARC JSON for one record in this run.

    The replacement payload becomes the new ``run_records.marc`` —
    every downstream stage that reads the record (authority rerun,
    Wikidata Studio build) picks up the edit immediately.
    """
    await _lookup_run_with_access(db, run_id, auth, write=True)
    rec = (
        await db.execute(
            select(RunRecord).where(
                RunRecord.run_id == run_id, RunRecord.control_number == control_number,
            )
        )
    ).scalar_one_or_none()
    if rec is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Record not found")

    rec.marc = payload.marc
    # Audit
    run_for_pid = (await db.execute(select(Run).where(Run.id == run_id))).scalar_one()
    await append_event(
        db, project_id=run_for_pid.project_id, actor_id=auth.user.id,
        type="record.edited",
        payload={"run_id": str(run_id), "control_number": control_number},
    )
    await db.commit()
    return RunMarcRecord(control_number=rec.control_number, marc=rec.marc)


# ── AI verification ────────────────────────────────────────────────────


@router.post(
    "/runs/{run_id}/matches/{match_id}/ai-verify",
    response_model=AiVerdictResponse,
)
async def ai_verify_match(
    run_id: uuid.UUID, match_id: uuid.UUID,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> AiVerdictResponse:
    """Ask Gemini (or the heuristic fallback) whether this candidate is
    the correct authority match. Stores the verdict in payload[ai_verdict]
    and returns it inline."""
    await _lookup_run_with_access(db, run_id, auth, write=True)
    m = (
        await db.execute(
            select(AuthorityMatch).where(
                AuthorityMatch.id == match_id, AuthorityMatch.run_id == run_id,
            )
        )
    ).scalar_one_or_none()
    if m is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Match not found")

    # Pull the full MARC record so the prompt has context.
    rec = (
        await db.execute(
            select(RunRecord).where(
                RunRecord.run_id == run_id, RunRecord.control_number == m.control_number,
            )
        )
    ).scalar_one_or_none()

    # Try the user's Gemini key. Falls back to the heuristic verdict.
    key = await ai_verifier.unwrap_user_gemini_key(
        db, user_id=auth.user.id, kek=auth.kek,
    )
    verdict = await ai_verifier.verify_match(
        m, marc_record=rec.marc if rec else None, gemini_key=key,
    )

    payload = dict(m.payload or {})
    payload["ai_verdict"] = {
        "overall": verdict.overall,
        "reasoning": verdict.reasoning,
        "model": verdict.model,
        "judged_at": verdict.judged_at,
    }
    m.payload = payload
    await db.commit()

    return AiVerdictResponse(
        overall=verdict.overall,  # type: ignore[arg-type]
        reasoning=verdict.reasoning,
        model=verdict.model,
        judged_at=verdict.judged_at,
        fallback=(verdict.model == "heuristic"),
    )


# ── helpers ────────────────────────────────────────────────────────────


def _to_list_item(r: Run) -> RunListItem:
    return RunListItem(
        id=r.id, project_id=r.project_id, name=r.name, status=r.status,  # type: ignore[arg-type]
        record_count=r.record_count, match_count=r.match_count, error=r.error,
        created_at=r.created_at, completed_at=r.completed_at,
    )


def _apply_approval(m: AuthorityMatch, approved: bool, user_id: uuid.UUID) -> None:
    m.approved = approved
    m.approved_by = user_id if approved else None
    m.approved_at = datetime.now(timezone.utc) if approved else None


async def _record_match_event(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    actor_id: uuid.UUID,
    row: AuthorityMatch,
) -> None:
    """Route an AuthorityMatch mutation through the versioned event log.

    First touch on an entity gets ``op=create`` (carries full state).
    Every subsequent touch gets ``op=patch`` (the diff against the
    prior state). The caller commits the surrounding transaction.

    Versioning failure does NOT 500 the request — log and continue so
    the read-model write the curator just made is still persisted.
    """

    new_state: dict[str, Any] = {
        "approved":     row.approved,
        "approved_by":  str(row.approved_by) if row.approved_by else None,
        "approved_at":  row.approved_at.isoformat() if row.approved_at else None,
        "matched_name": row.matched_name,
        "mazal_id":     row.mazal_id,
        "viaf_id":      row.viaf_id,
        "wikidata_qid": row.wikidata_qid,
        "confidence":   row.confidence,
        "source":       row.source,
        "role":         row.role,
        "entity_text":  row.entity_text,
        "entity_kind":  row.entity_kind,
        "payload":      dict(row.payload or {}),
    }
    entity_id_str = str(row.id)
    try:
        has_history = (
            await db.execute(
                select(ProjectEvent.id)
                .where(
                    ProjectEvent.entity_type == ENTITY_TYPE_AUTHORITY_MATCH,
                    ProjectEvent.entity_id == entity_id_str,
                )
                .limit(1)
            )
        ).scalar_one_or_none() is not None
        op_kind = OP_PATCH if has_history else OP_CREATE
        await apply_event(
            db,
            project_id=project_id,
            entity_type=ENTITY_TYPE_AUTHORITY_MATCH,
            entity_id=entity_id_str,
            op=op_kind,
            new_state=new_state,
            actor_id=actor_id,
            message="authority approve" if row.approved else "authority unapprove",
        )
    except Exception as exc:  # noqa: BLE001 — versioning never fails the request
        logger.warning(
            "apply_event failed for authority_match %s: %s", row.id, exc,
        )


async def _lookup_run_with_access(
    db: AsyncSession, run_id: uuid.UUID, auth: AuthContext, *, write: bool = False,
) -> Run:
    """Resolve the run + check the user has project access. Mirrors the
    project_perms RBAC but works off a run-id input (no project_id in
    the URL)."""
    run = (await db.execute(select(Run).where(Run.id == run_id))).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    proj = (
        await db.execute(select(Project).where(Project.id == run.project_id))
    ).scalar_one_or_none()
    if proj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    if proj.owner_id == auth.user.id:
        return run
    m = (
        await db.execute(
            select(Membership).where(
                Membership.project_id == proj.id, Membership.user_id == auth.user.id,
            )
        )
    ).scalar_one_or_none()
    if m is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Not a member of this project",
        )
    if write and m.role not in (PROJECT_ROLE_OWNER, PROJECT_ROLE_EDITOR):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Editor role required",
        )
    if not write and m.role not in (
        PROJECT_ROLE_OWNER, PROJECT_ROLE_EDITOR, PROJECT_ROLE_VIEWER,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Viewer role required",
        )
    return run
