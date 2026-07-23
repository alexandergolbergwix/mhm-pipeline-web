"""Run lifecycle + approvals.

Endpoints (RBAC notes):

* ``POST   /projects/{id}/runs``            — editor+
* ``GET    /projects/{id}/runs``            — viewer+
* ``GET    /runs/{id}``                     — viewer+ (project-scoped via lookup)
* ``GET    /runs/{id}/matches``             — viewer+
* ``GET    /runs/{id}/records/{cn}``        — viewer+ (popup with full MARC)
* ``PATCH  /runs/{id}/matches/{mid}``       — editor+ (toggle approval)
* ``POST   /runs/{id}/matches/bulk-approve``— editor+
* ``POST   /runs/{id}/authority/re-enrich`` — editor+ (re-run full matching, skip_cache param)
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse
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
from app.pipeline.marc_structured_index import MarcStructuredIndex
from app.pipeline.run import execute_run, serialise_match
from app.settings import get_settings
from app.schemas.runs import (
    AiVerdictResponse,
    ApprovalBatch,
    ApprovalUpdate,
    AuthorityAutoApproveRule,
    AuthorityMatchEdit,
    AuthorityMatchResponse,
    MazalCandidatePick,
    RunDetail,
    RunListItem,
    RecordEdit,
    RunMarcRecord,
)
from app.versioning import apply_event


logger = logging.getLogger(__name__)


router = APIRouter(tags=["runs"])


def _ensure_legacy_authority_mutations_enabled() -> None:
    """Fail closed for the retired standalone Authority editor surface."""
    if get_settings().legacy_authority_mutations_enabled:
        return
    logger.warning("legacy_authority_mutation_retired", extra={"surface": "runs"})
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="Standalone Authority mutations are retired; use HMO Wikibase Studio.",
    )


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
    matches_result, marc_result = await asyncio.gather(
        db.execute(
            select(AuthorityMatch)
            .where(AuthorityMatch.run_id == run_id)
            .order_by(AuthorityMatch.control_number.asc(), AuthorityMatch.entity_text.asc())
        ),
        db.execute(
            select(RunRecord).where(RunRecord.run_id == run_id)
        ),
    )
    matches = matches_result.scalars().all()
    marc_rows = marc_result.scalars().all()
    marc_by_cn = {
        str(r.control_number): dict(r.marc or {"_control_number": r.control_number})
        for r in marc_rows
    }
    marc_index = MarcStructuredIndex.from_records(
        dict(r.marc or {}) for r in marc_rows
    )
    result: list[AuthorityMatchResponse] = []
    for m in matches:
        candidate_type = m.entity_kind or m.role or None
        ei = marc_index.classify(
            m.control_number,
            m.entity_text or "",
            candidate_type=str(candidate_type) if candidate_type else None,
        )
        result.append(AuthorityMatchResponse(**serialise_match(
            m,
            exists_in=ei,
            marc_record=marc_by_cn.get(str(m.control_number)),
        )))
    return result


@router.get("/runs/{run_id}/records", response_model=list[str])
async def list_records(
    run_id: uuid.UUID,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> list[str]:
    """Return every control_number in this run (for MARC editor pickers)."""
    await _lookup_run_with_access(db, run_id, auth)
    rows = (
        await db.execute(
            select(RunRecord.control_number)
            .where(RunRecord.run_id == run_id)
            .order_by(RunRecord.control_number.asc())
        )
    ).scalars().all()
    return list(rows)


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
    _ensure_legacy_authority_mutations_enabled()
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
    _ensure_legacy_authority_mutations_enabled()
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


# ── Auto-approve by rule ───────────────────────────────────────────────


@router.post("/runs/{run_id}/matches/auto-approve/preview")
async def preview_authority_auto_approve(
    run_id: uuid.UUID,
    rule: AuthorityAutoApproveRule,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> dict:
    """Return how many matches the rule would approve without changing data."""
    _ensure_legacy_authority_mutations_enabled()
    await _lookup_run_with_access(db, run_id, auth, write=False)
    rows = (
        await db.execute(
            select(AuthorityMatch).where(AuthorityMatch.run_id == run_id)
        )
    ).scalars().all()
    matched = _apply_auto_approve_rule(rows, rule)
    return {"matched": len(matched)}


@router.post("/runs/{run_id}/matches/auto-approve")
async def apply_authority_auto_approve(
    run_id: uuid.UUID,
    rule: AuthorityAutoApproveRule,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> dict:
    """Approve all matches satisfying the rule. Returns count approved."""
    _ensure_legacy_authority_mutations_enabled()
    run = await _lookup_run_with_access(db, run_id, auth, write=True)
    rows = (
        await db.execute(
            select(AuthorityMatch).where(AuthorityMatch.run_id == run_id)
        )
    ).scalars().all()
    matched = _apply_auto_approve_rule(rows, rule)
    for m in matched:
        _apply_approval(m, True, auth.user.id)
        await _record_match_event(
            db, project_id=run.project_id,
            actor_id=auth.user.id, row=m,
        )
    if matched:
        await db.commit()
    return {"matched": len(matched), "approved": len(matched)}


def _match_source_count(m: AuthorityMatch, payload: dict) -> int:
    """Derive cross-source count; older rows may lack payload.source_count."""
    raw = payload.get("source_count")
    if raw is not None:
        try:
            return int(raw)
        except (TypeError, ValueError):
            pass
    sources = payload.get("sources")
    if isinstance(sources, list) and sources:
        return len(sources)
    return 1 if m.source else 0


AUTO_APPROVE_BLOCKED_GUARDS = frozenset({
    "homonym_unresolved",
    "short_name_homonym",
    "mazal_subject_not_personality",
    "viaf_date_mismatch",
    "cross_source_conflict",
    "wikidata_disagrees",
    "wikidata_crosscheck_fail",
})


def _apply_auto_approve_rule(
    rows: list[AuthorityMatch],
    rule: AuthorityAutoApproveRule,
) -> list[AuthorityMatch]:
    """Filter matches to those satisfying every rule condition."""
    scope_ids = {str(mid) for mid in rule.match_ids} if rule.match_ids else None
    out: list[AuthorityMatch] = []
    for m in rows:
        if m.approved:
            continue
        if scope_ids is not None and str(m.id) not in scope_ids:
            continue
        p = m.payload or {}
        guard_flags = set(p.get("guard_flags") or [])
        if guard_flags.intersection(AUTO_APPROVE_BLOCKED_GUARDS):
            continue
        # Confidence level
        if rule.confidence_levels and m.confidence not in rule.confidence_levels:
            continue
        # Source filter — match if ANY of the match's sources is in the rule
        if rule.sources:
            match_sources = set(p.get("sources") or [])
            if not match_sources.intersection(rule.sources):
                continue
        # Entity kind
        if rule.entity_kinds and m.entity_kind not in rule.entity_kinds:
            continue
        # Min source count
        if _match_source_count(m, p) < rule.min_source_count:
            continue
        # AI verdict gates
        ai_verdict = p.get("ai_verdict") or {}
        ai_overall = (ai_verdict.get("overall") or "") if isinstance(ai_verdict, dict) else ""
        if rule.require_ai_pass and ai_overall not in ("full", "pass"):
            continue
        if rule.respect_ai_fail and ai_overall in ("fail", "partial"):
            continue
        out.append(m)
    return out


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
    _ensure_legacy_authority_mutations_enabled()
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
    _ensure_legacy_authority_mutations_enabled()
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


@router.post("/runs/{run_id}/authority/re-enrich")
async def re_enrich_authority(
    run_id: uuid.UUID,
    skip_cache: bool = Query(
        False,
        description="When true, bypass the shared inference cache and call "
                    "Mazal / VIAF / Wikidata / KIMA fresh. Use to recover from "
                    "stale cached results (e.g., missing birth/death years).",
    ),
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> dict:
    """Re-run the full authority matching pipeline for every entity in the
    run, updating match fields in-place while **preserving** the curator's
    approval decisions (``approved``, ``approved_by``, ``approved_at``).

    Unlike ``/authority/rebuild`` (which only re-applies hardening guards
    over data already in the DB), this endpoint calls the live matchers —
    Mazal, VIAF, Wikidata, KIMA — so it picks up updated authority records
    and can fill in birth/death years that were missing on the original run.

    ``skip_cache=true`` bypasses the 30-day shared inference cache so every
    external API is called fresh.  Use this when you see "—" in the Dates
    tab or when authority data may have changed upstream.

    Returns: ``checked``, ``updated``, ``newly_matched``, ``skip_cache``.
    """
    _ensure_legacy_authority_mutations_enabled()
    run = await _lookup_run_with_access(db, run_id, auth, write=True)

    from app.pipeline import authority as auth_pipeline  # noqa: PLC0415
    from app.pipeline.authority_re_enrich import re_enrich_run  # noqa: PLC0415

    matcher = auth_pipeline.get_default_matcher()

    records = (
        await db.execute(
            select(RunRecord).where(RunRecord.run_id == run_id)
        )
    ).scalars().all()

    existing_rows = (
        await db.execute(
            select(AuthorityMatch).where(AuthorityMatch.run_id == run_id)
        )
    ).scalars().all()

    stats = await re_enrich_run(
        db, run, matcher,
        skip_cache=skip_cache,
        records=list(records),
        existing_rows=list(existing_rows),
    )
    return {**stats, "skip_cache": skip_cache}


@router.get("/runs/{run_id}/authority/note-index")
async def authority_note_index(
    run_id: uuid.UUID,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    """Per-record searchable note text for the Authority table notes filter."""
    await _lookup_run_with_access(db, run_id, auth, write=False)
    from app.pipeline.marc_ingest import build_record_note_blob, prepare_record_for_pipeline  # noqa: PLC0415

    records = (
        await db.execute(select(RunRecord).where(RunRecord.run_id == run_id))
    ).scalars().all()
    out: dict[str, str] = {}
    for rec in records:
        marc = prepare_record_for_pipeline(dict(rec.marc or {}))
        blob = build_record_note_blob(marc)
        if blob:
            out[rec.control_number] = blob
    return out


@router.post("/runs/{run_id}/authority/re-enrich/stream")
async def re_enrich_authority_stream(
    run_id: uuid.UUID,
    skip_cache: bool = Query(False),
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    """SSE version of re-enrich.  Emits one event per entity so the
    frontend can show a live progress bar.

    Event types (``data:`` is JSON):
        authority.start      { total_records, total_entities }
        authority.entity     { index, total, control_number, entity_text,
                               entity_kind, matched, matched_name, source,
                               confidence, is_new }
        authority.done       { checked, updated, newly_matched, skip_cache }
        authority.error      { message }
    """
    _ensure_legacy_authority_mutations_enabled()
    run = await _lookup_run_with_access(db, run_id, auth, write=True)

    from app.pipeline import authority as auth_pipeline  # noqa: PLC0415
    from app.pipeline.marc_ingest import extract_named_entities, prepare_record_for_pipeline  # noqa: PLC0415

    matcher = auth_pipeline.get_default_matcher()

    records = (
        await db.execute(select(RunRecord).where(RunRecord.run_id == run_id))
    ).scalars().all()

    existing_rows = (
        await db.execute(select(AuthorityMatch).where(AuthorityMatch.run_id == run_id))
    ).scalars().all()

    from app.pipeline.authority_re_enrich import match_key  # noqa: PLC0415
    from app.pipeline.entity_normalize import (  # noqa: PLC0415
        normalize_entity_text,
        normalize_role,
    )
    from collections import defaultdict

    existing_idx: dict[tuple[str, str, str, str], list[AuthorityMatch]] = defaultdict(list)
    for m in existing_rows:
        existing_idx[match_key(
            m.control_number, m.entity_text, m.entity_kind, m.role or "",
        )].append(m)

    # Pre-count total entities so the frontend can show X of N.
    all_entities: list[tuple[RunRecord, dict]] = []
    for rec in records:
        marc = prepare_record_for_pipeline(dict(rec.marc or {}))
        for entity in extract_named_entities(marc):
            all_entities.append((rec, entity))

    total_entities = len(all_entities)

    async def _stream() -> AsyncIterator[str]:
        def _sse(event_type: str, payload: dict) -> str:
            return f"event: {event_type}\ndata: {json.dumps(payload)}\n\n"

        yield _sse("authority.start", {
            "total_records": len(records),
            "total_entities": total_entities,
        })

        checked = 0
        updated = 0
        newly_matched = 0
        matched_count = 0
        source_counts: dict[str, int] = {}

        orphans_removed = 0
        produced_keys: set[tuple[str, str, str, str]] = set()

        for idx, (rec, entity) in enumerate(all_entities):
            checked += 1
            clean_text = normalize_entity_text(entity.get("text", ""))
            clean_role = normalize_role(entity.get("role", ""))
            kind = entity.get("kind", "person")
            produced_keys.add(match_key(rec.control_number, clean_text, kind, clean_role))
            yield _sse("authority.entity_start", {
                "index": idx,
                "total": total_entities,
                "control_number": rec.control_number,
                "entity_text": entity.get("text", ""),
                "entity_kind": entity.get("kind", "person"),
            })
            candidates = []
            # Re-use the already-prepared marc from all_entities (the entity
            # was extracted from it, so the same prepared dict is the right
            # context for _looks_like_place and other marc-reading helpers).
            # We stored only (rec, entity) so prepare again here; it's cheap.
            prepared_marc = prepare_record_for_pipeline(dict(rec.marc or {}))
            try:
                candidates = await matcher.match(
                    entity, prepared_marc,
                    db_session=db,
                    user_id=run.created_by,
                    skip_cache=skip_cache,
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "re-enrich stream: authority match failed for %r",
                    entity.get("text"),
                )

            matched = bool(candidates)
            c = candidates[0] if candidates else None
            is_new = False
            if matched:
                matched_count += 1
            if c:
                payload_sources = c.payload.get("sources") if isinstance(c.payload, dict) else None
                sources = payload_sources if isinstance(payload_sources, list) else [c.source]
                for source in sources:
                    source_key = str(source or "").strip()
                    if source_key:
                        source_counts[source_key] = source_counts.get(source_key, 0) + 1

            if c:
                key = match_key(rec.control_number, clean_text, kind, clean_role)
                if key in existing_idx:
                    matches = existing_idx[key]
                    m = matches[0]
                    for dup in matches[1:]:
                        await db.delete(dup)
                        orphans_removed += 1
                    existing_idx[key] = [m]
                    m.entity_text = clean_text
                    m.role = clean_role
                    m.entity_kind = kind
                    m.matched_name = c.matched_name
                    m.mazal_id     = c.mazal_id
                    m.viaf_id      = c.viaf_id
                    m.wikidata_qid = c.wikidata_qid
                    m.confidence   = c.confidence
                    m.source       = c.source
                    m.payload      = c.payload
                    updated += 1
                else:
                    row = AuthorityMatch(
                        run_id=run_id,
                        control_number=rec.control_number,
                        entity_text=clean_text,
                        entity_kind=kind,
                        role=clean_role,
                        matched_name=c.matched_name,
                        mazal_id=c.mazal_id,
                        viaf_id=c.viaf_id,
                        wikidata_qid=c.wikidata_qid,
                        confidence=c.confidence,
                        source=c.source,
                        payload=c.payload,
                    )
                    db.add(row)
                    await db.flush()
                    existing_idx[key] = [row]
                    newly_matched += 1
                    is_new = True

            yield _sse("authority.entity", {
                "index": idx,
                "total": total_entities,
                "control_number": rec.control_number,
                "entity_text": entity.get("text", ""),
                "entity_kind": entity.get("kind", "person"),
                "matched": matched,
                "matched_name": c.matched_name if c else None,
                "source": c.source if c else None,
                "confidence": c.confidence if c else None,
                "is_new": is_new,
            })

        for m in existing_rows:
            k = match_key(m.control_number, m.entity_text, m.entity_kind, m.role or "")
            if k not in produced_keys:
                await db.delete(m)
                orphans_removed += 1

        from sqlalchemy import func  # noqa: PLC0415

        remaining_count = await db.scalar(
            select(func.count())
            .select_from(AuthorityMatch)
            .where(AuthorityMatch.run_id == run_id)
        )
        run.match_count = int(remaining_count or 0)
        await db.commit()

        yield _sse("authority.done", {
            "checked": checked,
            "matched": matched_count,
            "updated": updated,
            "newly_matched": newly_matched,
            "orphans_removed": orphans_removed,
            "source_counts": source_counts,
            "skip_cache": skip_cache,
        })

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


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
    _ensure_legacy_authority_mutations_enabled()
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


@router.get("/runs/{run_id}/matches/{match_id}/candidates")
async def list_match_candidates(
    run_id: uuid.UUID,
    match_id: uuid.UUID,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Return Mazal homonym candidates for curator resolution."""
    await _lookup_run_with_access(db, run_id, auth, write=False)
    m = (
        await db.execute(
            select(AuthorityMatch).where(
                AuthorityMatch.id == match_id,
                AuthorityMatch.run_id == run_id,
            )
        )
    ).scalar_one_or_none()
    if m is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Match not found")

    payload = dict(m.payload or {})
    cached = payload.get("homonym_candidates")
    if isinstance(cached, list) and cached:
        return {"candidates": cached, "source": "payload"}

    from app.pipeline.authority import get_default_matcher  # noqa: PLC0415

    matcher = get_default_matcher()
    backend = matcher._authority_backend  # type: ignore[attr-defined]
    live = await backend.match_person_candidates(str(m.entity_text or ""))
    return {"candidates": live, "source": "live"}


@router.post(
    "/runs/{run_id}/matches/{match_id}/pick-candidate",
    response_model=AuthorityMatchResponse,
)
async def pick_match_candidate(
    run_id: uuid.UUID,
    match_id: uuid.UUID,
    body: MazalCandidatePick,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> AuthorityMatchResponse:
    """Apply curator homonym pick — sets mazal_id and clears abstain flags."""
    _ensure_legacy_authority_mutations_enabled()
    await _lookup_run_with_access(db, run_id, auth, write=True)
    m = (
        await db.execute(
            select(AuthorityMatch).where(
                AuthorityMatch.id == match_id,
                AuthorityMatch.run_id == run_id,
            )
        )
    ).scalar_one_or_none()
    if m is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Match not found")

    picked = body.mazal_id.strip()
    if not picked:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="mazal_id required")

    payload = dict(m.payload or {})
    allowed = {
        str(c.get("mazal_id"))
        for c in (payload.get("homonym_candidates") or [])
        if isinstance(c, dict) and c.get("mazal_id")
    }
    if allowed and picked not in allowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="mazal_id not in homonym_candidates",
        )

    old_mazal = m.mazal_id
    m.mazal_id = picked
    m.confidence = "medium"
    flags = [f for f in (payload.get("guard_flags") or []) if f != "homonym_unresolved"]
    payload["guard_flags"] = flags
    payload.pop("homonym_abstain", None)
    payload.pop("homonym_abstain_reason", None)
    payload["main_marc_tag"] = "100"
    payload["personality_picked_by_curator"] = True
    m.payload = payload
    if "mazal" not in (payload.get("sources") or []):
        sources = list(payload.get("sources") or [])
        sources.append("mazal")
        payload["sources"] = sources
        m.payload = payload
    if not m.source or m.source == "unresolved":
        m.source = "mazal"

    run_for_pid = (await db.execute(select(Run).where(Run.id == run_id))).scalar_one()
    await append_event(
        db,
        project_id=run_for_pid.project_id,
        actor_id=auth.user.id,
        type="match.edited",
        payload={
            "run_id": str(run_id),
            "match_id": str(m.id),
            "changes": {"mazal_id": {"from": old_mazal, "to": picked}, "pick_candidate": True},
        },
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
    _ensure_legacy_authority_mutations_enabled()
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
