"""Wikidata Studio router — builds items + QuickStatements for a run.

The endpoint reads the run's MARC records and approved authority
matches, drives the *real* desktop ``WikidataItemBuilder`` +
``QuickStatementsExporter`` in a threadpool, and returns the structured
items (every label / description / claim / qualifier / reference) plus
the QuickStatements TSV blob ready for download.

Only the **approved** matches feed the builder — this is the curator
workflow's unit of truth (see Rule 54 in the desktop CLAUDE.md).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.session import AuthContext, current_auth
from app.db import get_session
from app.models.event import (
    ENTITY_TYPE_WIKIDATA_OVERRIDE,
    OP_CREATE,
    OP_PATCH,
    ProjectEvent,
)
from app.models.extraction_approval import ExtractionApproval
from app.models.item_override import WikidataItemOverride
from app.models.run import AuthorityMatch, Run, RunRecord
from app.models.wikidata_studio_cache import WikidataStudioCache
from app.pipeline import wikidata_studio, wikidata_upload
from app.routers.runs import _lookup_run_with_access  # noqa: PLF401 — module-internal
from app.versioning import apply_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/runs", tags=["wikidata-studio"])


class StudioSummary(BaseModel):
    total_items: int
    manuscripts: int
    persons: int
    works: int
    statements: int


class PropertyInfo(BaseModel):
    id: str
    label: str


class StudioBuildResponse(BaseModel):
    items: list[dict[str, Any]]
    quickstatements: str
    summary: StudioSummary
    approved_match_count: int     # how many of the feed are approved
    pending_match_count: int      # how many are still pending review
    used_match_count: int         # what we actually fed the builder
    approved_only: bool           # which mode was used
    record_count: int
    # Server-side slicing metadata
    total: int                    # total items matching current slice params
    page: int
    page_size: int
    # Precomputed aggregates to replace client-side scans
    approved_item_count: int      # items with approved==True in the full build
    properties: list[PropertyInfo]        # distinct P-ids in the full build
    property_labels: dict[str, str]       # P/Q id → label map for label-store seeding


@router.get("/{run_id}/wikidata-studio", response_model=StudioBuildResponse)
async def build_studio(
    run_id: uuid.UUID,
    approved_only: bool = Query(
        default=True,
        description="When true (default), only approved authority matches "
                    "and NER entities feed the item builder, matching the "
                    "'ship this in the final output' semantics of the "
                    "approval stores. Pass false to preview all candidates.",
    ),
    force_rebuild: bool = Query(
        default=False,
        description="When true, skip the fingerprint cache and rebuild from "
                    "scratch. The result is still written to cache so the next "
                    "normal GET is fast. Does not affect the inference cache "
                    "(VIAF / authority calls).",
    ),
    # ── server-side slicing params (applied AFTER cache load) ──────────
    entity_type: str | None = Query(
        default=None,
        description="Filter by entity_type (manuscript / person / work). "
                    "Omit or pass null for all types.",
    ),
    q: str | None = Query(
        default=None,
        description="Substring search across labels, descriptions, aliases, "
                    "existing_qid, and entity_type.",
    ),
    sort: str = Query(
        default="label",
        description="Sort key: label | statements | entity_type | wikidata.",
    ),
    sort_dir: str = Query(
        default="asc",
        description="Sort direction: asc | desc.",
    ),
    page: int = Query(default=1, ge=1, description="1-based page number."),
    page_size: int = Query(
        default=50, ge=1, le=500,
        description="Items per page. Max 500.",
    ),
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> StudioBuildResponse:
    await _lookup_run_with_access(db, run_id, auth)

    # Load all raw rows needed for fingerprinting + building.
    records = (
        await db.execute(
            select(RunRecord).where(RunRecord.run_id == run_id)
            .order_by(RunRecord.control_number.asc())
        )
    ).scalars().all()
    all_matches = (
        await db.execute(
            select(AuthorityMatch).where(AuthorityMatch.run_id == run_id)
        )
    ).scalars().all()
    entity_rows = (
        await db.execute(
            select(ExtractionApproval).where(ExtractionApproval.run_id == run_id)
        )
    ).scalars().all()
    override_rows = (
        await db.execute(
            select(WikidataItemOverride).where(WikidataItemOverride.run_id == run_id)
        )
    ).scalars().all()

    fingerprint = wikidata_studio.compute_build_fingerprint(
        records, list(all_matches), entity_rows, override_rows, approved_only,
    )

    # Check the build cache — return immediately if nothing changed.
    cached = (
        await db.execute(
            select(WikidataStudioCache).where(
                WikidataStudioCache.run_id == run_id,
                WikidataStudioCache.approved_only == approved_only,
            )
        )
    ).scalar_one_or_none()

    if not force_rebuild and cached is not None and cached.input_fingerprint == fingerprint:
        logger.debug("wikidata-studio cache hit for run %s (fp=%s)", run_id, fingerprint[:8])
        sliced, total, props, plabels, approved_item_count = _slice_items(
            cached.result_items, entity_type=entity_type, q=q,
            sort=sort, sort_dir=sort_dir, page=page, page_size=page_size,
        )
        return StudioBuildResponse(
            items=sliced,
            quickstatements=cached.quickstatements,
            summary=StudioSummary(**cached.summary),
            approved_match_count=cached.approved_match_count,
            pending_match_count=cached.pending_match_count,
            used_match_count=cached.used_match_count,
            approved_only=approved_only,
            record_count=cached.record_count,
            total=total,
            page=page,
            page_size=page_size,
            approved_item_count=approved_item_count,
            properties=props,
            property_labels=plabels,
        )

    # Cache miss — run the full build.
    logger.debug("wikidata-studio cache miss for run %s (fp=%s)", run_id, fingerprint[:8])

    approved_count = sum(1 for m in all_matches if m.approved)
    pending_count = len(all_matches) - approved_count
    matches = [m for m in all_matches if m.approved] if approved_only else list(all_matches)

    marc_records = [dict(r.marc) for r in records]
    approved_matches = [
        {
            "id": str(m.id),
            "control_number": m.control_number,
            "entity_text": m.entity_text,
            "role": m.role,
            "matched_name": m.matched_name,
            "mazal_id": m.mazal_id,
            "viaf_id": m.viaf_id,
            "wikidata_qid": m.wikidata_qid,
            "confidence": m.confidence,
            "source": m.source,
            "payload": m.payload or {},
        }
        for m in matches
    ]

    overrides = {
        r.local_id: {
            "labels":            r.labels,
            "descriptions":      r.descriptions,
            "aliases":           r.aliases,
            "add_statements":    r.add_statements,
            "remove_statements": r.remove_statements,
            "statement_edits":   r.statement_edits,
        }
        for r in override_rows
    }
    entities_by_cn = _group_entity_rows(entity_rows, approved_only)
    result = await wikidata_studio.build_items_for_run(
        marc_records=marc_records, approved_matches=approved_matches,
        entities_by_cn=entities_by_cn,
        overrides=overrides, return_native=True,
    )
    # Stamp local_id + curator approved flag onto each serialised item.
    overrides_approved = {r.local_id: r.approved for r in override_rows}
    if result.get("native_items"):
        for it_dict, it_native in zip(
            result["items"], result["native_items"], strict=True,
        ):
            lid = wikidata_studio.local_id_for_item(it_native)
            it_dict["local_id"] = lid
            it_dict["approved"] = overrides_approved.get(lid)

    summary_dict = result["summary"]

    # Upsert the cache row.
    await _upsert_studio_cache(
        db, run_id=run_id, approved_only=approved_only,
        fingerprint=fingerprint,
        items=result["items"],
        quickstatements=result["quickstatements"],
        summary=summary_dict,
        approved_match_count=approved_count,
        pending_match_count=pending_count,
        used_match_count=len(approved_matches),
        record_count=len(marc_records),
        existing=cached,
    )

    sliced, total, props, plabels, approved_item_count = _slice_items(
        result["items"], entity_type=entity_type, q=q,
        sort=sort, sort_dir=sort_dir, page=page, page_size=page_size,
    )
    return StudioBuildResponse(
        items=sliced,
        quickstatements=result["quickstatements"],
        summary=StudioSummary(**summary_dict),
        approved_match_count=approved_count,
        pending_match_count=pending_count,
        used_match_count=len(approved_matches),
        approved_only=approved_only,
        record_count=len(marc_records),
        total=total,
        page=page,
        page_size=page_size,
        approved_item_count=approved_item_count,
        properties=props,
        property_labels=plabels,
    )


@router.get("/{run_id}/wikidata-studio/quickstatements.txt", response_class=PlainTextResponse)
async def download_quickstatements(
    run_id: uuid.UUID,
    approved_only: bool = Query(default=True),
    item_approved_only: bool = Query(
        default=False,
        description="When true, only include items where the curator has "
                    "explicitly ticked 'Approved' in the Studio item overlay. "
                    "Independent of approved_only (which filters authority matches).",
    ),
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> PlainTextResponse:
    """Plain-text QuickStatements TSV — paste into
    https://quickstatements.toolforge.org."""
    if item_approved_only:
        native = await _build_native_items(db, run_id, auth, approved_only=approved_only)
        override_rows = (
            await db.execute(
                select(WikidataItemOverride).where(WikidataItemOverride.run_id == run_id)
            )
        ).scalars().all()
        approved_ids = {r.local_id for r in override_rows if r.approved}
        filtered = [it for it in native if wikidata_studio.local_id_for_item(it) in approved_ids]
        qs_text = await wikidata_studio.quickstatements_for_items(filtered)
    else:
        studio = await build_studio(
            run_id=run_id, approved_only=approved_only, force_rebuild=False,
            auth=auth, db=db,
        )
        qs_text = studio.quickstatements

    suffix = "approved" if item_approved_only else ("match-approved" if approved_only else "all")
    return PlainTextResponse(
        qs_text,
        headers={
            "Content-Disposition": (
                f'attachment; filename="run-{run_id}-{suffix}-quickstatements.txt"'
            ),
        },
    )


# ── Reconcile (SPARQL against Wikidata, no writes) ──────────────────────


class ReconcileOutcomeDto(BaseModel):
    local_id: str
    label: str
    entity_type: str
    existing_qid: str | None
    method: str
    message: str


class ReconcileResponse(BaseModel):
    reconciled: int
    matched: int
    outcomes: list[ReconcileOutcomeDto]


@router.post(
    "/{run_id}/wikidata-studio/reconcile", response_model=ReconcileResponse,
)
async def reconcile_against_wikidata(
    run_id: uuid.UUID,
    approved_only: bool = Query(default=True),
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> ReconcileResponse:
    """For every built item, SPARQL-query Wikidata to find an existing
    QID via VIAF / NLI / cluster IDs. Updates the in-memory items so the
    next download / upload knows to UPDATE instead of CREATE. **Never**
    writes to Wikidata."""
    native = await _build_native_items(db, run_id, auth, approved_only=approved_only)
    outcomes = await wikidata_upload.reconcile_items(native)
    return ReconcileResponse(
        reconciled=len(outcomes),
        matched=sum(1 for o in outcomes if o.existing_qid),
        outcomes=[ReconcileOutcomeDto(**o.__dict__) for o in outcomes],
    )


# ── Upload (dry-run or live, all 4 guards intact) ──────────────────────


class UploadOutcomeDto(BaseModel):
    local_id: str
    label: str
    entity_type: str
    qid: str | None
    status: str
    message: str
    added_properties: list[str]


class UploadResponse(BaseModel):
    dry_run: bool
    moratorium_lifted: bool
    test_mode: bool
    outcomes: list[UploadOutcomeDto]


@router.post("/{run_id}/wikidata-studio/upload", response_model=UploadResponse)
async def upload_to_wikidata(
    run_id: uuid.UUID,
    dry_run: bool = Query(
        default=True,
        description="Default True — describe what would happen without "
                    "writing. Set False for live; live also requires the "
                    "user to have a stored Wikidata token (Settings) AND "
                    "MORATORIUM_LIFTED=true in the env (or WIKIDATA_TEST_MODE=true).",
    ),
    approved_only: bool = Query(default=True),
    item_approved_only: bool = Query(
        default=False,
        description="When true, only upload items where the curator has "
                    "explicitly ticked 'Approved' in the Studio item overlay.",
    ),
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> UploadResponse:
    """Live uploads ALWAYS use the user's own Wikidata token (stored
    encrypted via the Settings page) and route through the real
    ``WikidataUploader`` with all four modification guards intact."""
    import os  # noqa: PLC0415

    # Build the items first (with the latest approval state).
    native = await _build_native_items(db, run_id, auth, approved_only=approved_only)

    if item_approved_only:
        override_rows = (
            await db.execute(
                select(WikidataItemOverride).where(WikidataItemOverride.run_id == run_id)
            )
        ).scalars().all()
        approved_ids = {r.local_id for r in override_rows if r.approved}
        native = [it for it in native if wikidata_studio.local_id_for_item(it) in approved_ids]

    token: str | None = None
    if not dry_run:
        token = await _unwrap_user_secret(db, auth, "wikidata")

    if not dry_run and not token:
        raise_msg = (
            "Live upload requires a Wikidata token in Settings. "
            "Add one (User@Bot:hex or OAuth secret) and retry."
        )
        return UploadResponse(
            dry_run=False, moratorium_lifted=False, test_mode=False,
            outcomes=[UploadOutcomeDto(
                local_id="*", label="(token missing)", entity_type="",
                qid=None, status="failed", message=raise_msg, added_properties=[],
            )],
        )

    outcomes = await wikidata_upload.upload_items(
        native, token=token or "", dry_run=dry_run,
    )
    return UploadResponse(
        dry_run=dry_run,
        moratorium_lifted=os.environ.get("MORATORIUM_LIFTED", "").lower() == "true",
        test_mode=os.environ.get("WIKIDATA_TEST_MODE", "").lower() == "true",
        outcomes=[UploadOutcomeDto(**o.__dict__) for o in outcomes],
    )


# ── Per-item editing (curator overrides) ───────────────────────────────


class ItemOverridePayload(BaseModel):
    """Partial update — every field optional. The persisted row is
    merged with the existing override, so the UI can PATCH one tab
    at a time."""
    labels:            dict[str, str | None] | None = None
    descriptions:      dict[str, str | None] | None = None
    aliases:           dict[str, list[str] | None] | None = None
    add_statements:    list[dict[str, Any]] | None = None
    remove_statements: list[int] | None = None
    statement_edits:   dict[str, dict[str, Any]] | None = None
    approved:          bool | None = None


class ItemOverrideResponse(BaseModel):
    run_id: uuid.UUID
    local_id: str
    labels: dict[str, Any]
    descriptions: dict[str, Any]
    aliases: dict[str, Any]
    add_statements: list[dict[str, Any]]
    remove_statements: list[int]
    statement_edits: dict[str, Any]
    approved: bool | None = None


@router.patch(
    "/{run_id}/wikidata-studio/items/{local_id:path}",
    response_model=ItemOverrideResponse,
)
async def patch_item_override(
    run_id: uuid.UUID, local_id: str,
    payload: ItemOverridePayload,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> ItemOverrideResponse:
    """Persist a curator override for one Studio item.

    All fields optional. Sending ``labels: {"he": null}`` clears the
    Hebrew label override (reverts to whatever the builder produced).
    Statement edits use ``{"<index>": {"value": "Q5"}}`` — index is
    relative to the builder output AFTER removals are applied.
    """
    run = await _lookup_run_with_access(db, run_id, auth, write=True)

    row = (
        await db.execute(
            select(WikidataItemOverride).where(
                WikidataItemOverride.run_id == run_id,
                WikidataItemOverride.local_id == local_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = WikidataItemOverride(
            run_id=run_id, local_id=local_id, updated_by=auth.user.id,
        )
        db.add(row)
        # Flush so the python-side UUID default is materialised before
        # we use ``row.id`` as the versioning entity id.
        await db.flush()

    if payload.labels is not None:
        # Merge non-null, drop keys set to null. Force a new dict so
        # SQLAlchemy notices the change.
        new = dict(row.labels or {})
        for k, v in payload.labels.items():
            if v is None: new.pop(k, None)
            else:         new[k] = v
        row.labels = new
    if payload.descriptions is not None:
        new = dict(row.descriptions or {})
        for k, v in payload.descriptions.items():
            if v is None: new.pop(k, None)
            else:         new[k] = v
        row.descriptions = new
    if payload.aliases is not None:
        new = dict(row.aliases or {})
        for lang, vals in payload.aliases.items():
            if vals is None: new.pop(lang, None)
            else:            new[lang] = list(vals)
        row.aliases = new
    if payload.add_statements is not None:
        row.add_statements = list(payload.add_statements)
    if payload.remove_statements is not None:
        row.remove_statements = list(payload.remove_statements)
    if payload.statement_edits is not None:
        new_edits = dict(row.statement_edits or {})
        for k, v in payload.statement_edits.items():
            if v is None: new_edits.pop(k, None)
            else:         new_edits[k] = v
        row.statement_edits = new_edits
    if payload.approved is not None:
        row.approved = payload.approved

    row.updated_by = auth.user.id

    # Versioning event — audit the override edit on the same transaction
    # as the row write. Failure must NEVER 500 the request.
    entity_id_str = str(row.id)
    try:
        has_history = (
            await db.execute(
                select(ProjectEvent.id)
                .where(
                    ProjectEvent.entity_type == ENTITY_TYPE_WIKIDATA_OVERRIDE,
                    ProjectEvent.entity_id == entity_id_str,
                )
                .limit(1)
            )
        ).scalar_one_or_none() is not None
        op_kind = OP_PATCH if has_history else OP_CREATE
        new_state = {
            "labels":            dict(row.labels or {}),
            "descriptions":      dict(row.descriptions or {}),
            "aliases":           dict(row.aliases or {}),
            "add_statements":    list(row.add_statements or []),
            "remove_statements": list(row.remove_statements or []),
            "statement_edits":   dict(row.statement_edits or {}),
            "approved":          row.approved,
        }
        await apply_event(
            db,
            project_id=run.project_id,
            entity_type=ENTITY_TYPE_WIKIDATA_OVERRIDE,
            entity_id=entity_id_str,
            op=op_kind,
            new_state=new_state,
            actor_id=auth.user.id,
            message=f"wikidata override edit ({local_id})",
        )
    except Exception as exc:  # noqa: BLE001 — versioning must never 500
        logger.warning(
            "apply_event failed for wikidata_override %s: %s", entity_id_str, exc,
        )

    await db.commit()
    return ItemOverrideResponse(
        run_id=run_id, local_id=local_id,
        labels=row.labels or {}, descriptions=row.descriptions or {},
        aliases=row.aliases or {}, add_statements=row.add_statements or [],
        remove_statements=row.remove_statements or [],
        statement_edits=row.statement_edits or {},
        approved=row.approved,
    )


@router.delete(
    "/{run_id}/wikidata-studio/items/{local_id:path}/overrides",
    status_code=204,
)
async def clear_item_override(
    run_id: uuid.UUID, local_id: str,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> None:
    """Drop every curator edit for this item — next rebuild returns to
    what the builder + matchers produced."""
    run = await _lookup_run_with_access(db, run_id, auth, write=True)

    # Fetch the row first so we can record the tombstone against its
    # stable UUID — the bulk DELETE below loses the id otherwise.
    row = (
        await db.execute(
            select(WikidataItemOverride).where(
                WikidataItemOverride.run_id == run_id,
                WikidataItemOverride.local_id == local_id,
            )
        )
    ).scalar_one_or_none()

    if row is not None:
        entity_id_str = str(row.id)
        try:
            has_history = (
                await db.execute(
                    select(ProjectEvent.id)
                    .where(
                        ProjectEvent.entity_type == ENTITY_TYPE_WIKIDATA_OVERRIDE,
                        ProjectEvent.entity_id == entity_id_str,
                    )
                    .limit(1)
                )
            ).scalar_one_or_none() is not None
            op_kind = OP_PATCH if has_history else OP_CREATE
            await apply_event(
                db,
                project_id=run.project_id,
                entity_type=ENTITY_TYPE_WIKIDATA_OVERRIDE,
                entity_id=entity_id_str,
                op=op_kind,
                new_state={"_deleted": True},
                actor_id=auth.user.id,
                message=f"wikidata override cleared ({local_id})",
            )
        except Exception as exc:  # noqa: BLE001 — versioning must never 500
            logger.warning(
                "apply_event failed for wikidata_override tombstone %s: %s",
                entity_id_str, exc,
            )

    await db.execute(
        WikidataItemOverride.__table__.delete().where(
            (WikidataItemOverride.run_id == run_id)
            & (WikidataItemOverride.local_id == local_id)
        )
    )
    await db.commit()


# ── helpers ─────────────────────────────────────────────────────────────


def _slice_items(
    all_items: list[dict[str, Any]],
    *,
    entity_type: str | None,
    q: str | None,
    sort: str,
    sort_dir: str,
    page: int,
    page_size: int,
) -> tuple[list[dict[str, Any]], int, list[PropertyInfo], dict[str, str], int]:
    """Filter, sort, and paginate a flat list of serialised StudioItem dicts.

    Returns ``(page_items, total_matching, properties, property_labels, approved_item_count)``
    where:
    - ``page_items``          — the slice for the requested page
    - ``total_matching``      — count AFTER filter, BEFORE pagination
    - ``properties``          — distinct P-ids in the *full* build (unfiltered)
    - ``property_labels``     — P/Q id → label map for the *full* build
    - ``approved_item_count`` — items with approved==True in the *full* build

    Slicing never re-builds — it operates purely on the in-memory dicts
    returned from cache or from a fresh build. The cache invariant
    (Rule W-26) is untouched.
    """
    from converter.wikidata.property_labels import PROPERTY_LABELS, QID_LABELS  # noqa: PLC0415

    # ── precomputed aggregates (full unfiltered build) ──────────────────
    approved_item_count = sum(1 for it in all_items if it.get("approved") is True)

    prop_seen: dict[str, str] = {}   # p_id → label
    for it in all_items:
        for stmt in it.get("statements") or []:
            p = stmt.get("property") or stmt.get("property_id")
            if p and p not in prop_seen:
                plabel = (
                    stmt.get("property_label")
                    or PROPERTY_LABELS.get(p, "")
                )
                prop_seen[p] = plabel

    properties: list[PropertyInfo] = sorted(
        [PropertyInfo(id=pid, label=lbl) for pid, lbl in prop_seen.items()],
        key=lambda x: (x.label or x.id).lower(),
    )

    # Build property_labels: covers all P-ids + all Q-ids appearing as
    # statement values so the frontend label store can be seeded in one shot.
    property_labels: dict[str, str] = {}
    for it in all_items:
        for stmt in it.get("statements") or []:
            p = stmt.get("property") or stmt.get("property_id")
            if p:
                lbl = stmt.get("property_label") or PROPERTY_LABELS.get(p)
                if lbl:
                    property_labels[p] = lbl
            vid = stmt.get("value_id")
            val = stmt.get("value")
            qid = (
                vid if isinstance(vid, str) and vid.startswith("Q")
                else (val if isinstance(val, str) and val.startswith("Q") and val[1:].isdigit() else None)
            )
            if qid:
                vlbl = stmt.get("value_label") or QID_LABELS.get(qid)
                if vlbl:
                    property_labels[qid] = vlbl

    # ── filter ──────────────────────────────────────────────────────────
    filtered = all_items
    if entity_type and entity_type != "all":
        filtered = [it for it in filtered if it.get("entity_type") == entity_type]
    if q:
        q_lower = q.strip().lower()
        def _matches(it: dict[str, Any]) -> bool:
            parts: list[str] = [
                *it.get("labels", {}).values(),
                *it.get("descriptions", {}).values(),
                it.get("existing_qid") or "",
                it.get("entity_type") or "",
            ]
            for alias_list in (it.get("aliases") or {}).values():
                if isinstance(alias_list, list):
                    parts.extend(alias_list)
                elif isinstance(alias_list, str):
                    parts.append(alias_list)
            return q_lower in " ".join(parts).lower()
        filtered = [it for it in filtered if _matches(it)]

    # ── sort ─────────────────────────────────────────────────────────────
    reverse = sort_dir == "desc"

    def _label(it: dict[str, Any]) -> str:
        lbs = it.get("labels") or {}
        return (lbs.get("en") or lbs.get("he") or next(iter(lbs.values()), "")).lower()

    if sort == "statements":
        filtered.sort(key=lambda it: len(it.get("statements") or []), reverse=reverse)
    elif sort == "entity_type":
        filtered.sort(key=lambda it: it.get("entity_type") or "", reverse=reverse)
    elif sort == "wikidata":
        # items with a QID first (ascending), or last (descending)
        filtered.sort(
            key=lambda it: (0 if it.get("existing_qid") else 1),
            reverse=reverse,
        )
    else:  # "label" default
        try:
            filtered.sort(key=_label, reverse=reverse)
        except Exception:
            filtered.sort(key=_label, reverse=reverse)

    total = len(filtered)

    # ── paginate ─────────────────────────────────────────────────────────
    start = (page - 1) * page_size
    page_items = filtered[start: start + page_size]

    return page_items, total, properties, property_labels, approved_item_count


async def _build_native_items(
    db: AsyncSession, run_id: uuid.UUID, auth: AuthContext,
    *, approved_only: bool,
) -> list[Any]:
    """Re-run the builder and return the *native* WikidataItem objects
    (not the JSON dicts) so reconcile/upload can mutate them in place."""
    await _lookup_run_with_access(db, run_id, auth)
    records = (
        await db.execute(
            select(RunRecord).where(RunRecord.run_id == run_id)
            .order_by(RunRecord.control_number.asc())
        )
    ).scalars().all()
    all_matches = (
        await db.execute(select(AuthorityMatch).where(AuthorityMatch.run_id == run_id))
    ).scalars().all()
    entity_rows = (
        await db.execute(
            select(ExtractionApproval).where(ExtractionApproval.run_id == run_id)
        )
    ).scalars().all()
    override_rows = (
        await db.execute(
            select(WikidataItemOverride).where(WikidataItemOverride.run_id == run_id)
        )
    ).scalars().all()
    matches = [m for m in all_matches if m.approved] if approved_only else list(all_matches)

    overrides = {
        r.local_id: {
            "labels":            r.labels,
            "descriptions":      r.descriptions,
            "aliases":           r.aliases,
            "add_statements":    r.add_statements,
            "remove_statements": r.remove_statements,
            "statement_edits":   r.statement_edits,
        }
        for r in override_rows
    }
    entities_by_cn = _group_entity_rows(entity_rows, approved_only)
    result = await wikidata_studio.build_items_for_run(
        marc_records=[dict(r.marc) for r in records],
        approved_matches=[
            {
                "id": str(m.id),
                "control_number": m.control_number,
                "entity_text": m.entity_text,
                "role": m.role,
                "matched_name": m.matched_name,
                "mazal_id": m.mazal_id,
                "viaf_id": m.viaf_id,
                "wikidata_qid": m.wikidata_qid,
                "confidence": m.confidence,
                "source": m.source,
                "payload": m.payload or {},
            }
            for m in matches
        ],
        entities_by_cn=entities_by_cn,
        overrides=overrides,
        return_native=True,
    )
    return result.get("native_items") or []


# Hebrew common-noun strings that the NER sometimes extracts as person names.
# These are definitely not names and must be filtered out before building Wikidata items.
_NON_PERSON_STRINGS: frozenset[str] = frozenset({
    "הסוחר",    # the merchant
    "נפש",      # soul
    "גוי",      # gentile
    "כותי",     # Samaritan
    "ישמעאל",   # Ishmael (used generically for Muslim/Arab, not as a person name)
    "עמוד",     # page/column
    "דף",       # folio
    "פרשה",     # Torah portion
    "פסוק",     # verse
})


def _group_entity_rows(
    rows: list[Any], approved_only: bool,
) -> dict[str, list[dict[str, Any]]]:
    """Group ExtractionApproval rows into the per-control-number dict the
    desktop WikidataItemBuilder expects on ``record["entities"]``.

    Curator ``override_type`` / ``override_role`` / ``override_text``
    take precedence over the model's prediction (Rule W-24).
    """
    grouped: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        if approved_only and not r.approved:
            continue
        ent_dict = {
            "text":             r.override_text or r.text,
            "type":             (r.override_type or r.type or "").upper(),
            "role":             (r.override_role or r.role or "").upper(),
            "source":           r.source,
            "start":            int(r.start or 0),
            "end":              int(r.end or 0),
            "confidence":       r.confidence,
            "model_confidence": r.model_confidence,
            "approved":         bool(r.approved),
        }
        if ent_dict["type"] == "PERSON":
            text_lo = ent_dict["text"].strip().lower()
            if text_lo in _NON_PERSON_STRINGS:
                continue
            if (
                len(ent_dict["text"].split()) == 1
                and (r.confidence or 1.0) < 0.40
            ):
                continue
        grouped.setdefault(r.control_number, []).append(ent_dict)

    for cn, ents in grouped.items():
        seen_keys: set[tuple[str, str, str]] = set()
        deduped: list[dict[str, Any]] = []
        for ent in ents:
            key = (
                ent["text"].strip().lower(),
                ent["type"],
                ent["role"] or "",
            )
            if key not in seen_keys:
                seen_keys.add(key)
                deduped.append(ent)
        grouped[cn] = deduped
    return grouped


async def _upsert_studio_cache(
    db: AsyncSession,
    *,
    run_id: uuid.UUID,
    approved_only: bool,
    fingerprint: str,
    items: list[dict[str, Any]],
    quickstatements: str,
    summary: dict[str, Any],
    approved_match_count: int,
    pending_match_count: int,
    used_match_count: int,
    record_count: int,
    existing: WikidataStudioCache | None,
) -> None:
    """Write (insert or update) the build cache row.  Errors are swallowed
    so a cache write failure never degrades the user-facing response."""
    try:
        from datetime import datetime, timezone  # noqa: PLC0415

        if existing is None:
            row = WikidataStudioCache(
                run_id=run_id,
                approved_only=approved_only,
                input_fingerprint=fingerprint,
                result_items=items,
                quickstatements=quickstatements,
                summary=summary,
                approved_match_count=approved_match_count,
                pending_match_count=pending_match_count,
                used_match_count=used_match_count,
                record_count=record_count,
            )
            db.add(row)
        else:
            existing.input_fingerprint = fingerprint
            existing.result_items = items
            existing.quickstatements = quickstatements
            existing.summary = summary
            existing.approved_match_count = approved_match_count
            existing.pending_match_count = pending_match_count
            existing.used_match_count = used_match_count
            existing.record_count = record_count
            existing.built_at = datetime.now(timezone.utc)
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("wikidata-studio cache write failed for run %s: %s", run_id, exc)
        await db.rollback()


async def _unwrap_user_secret(db: AsyncSession, auth: AuthContext, key_name: str) -> str | None:
    """Unwrap the user's stored Wikidata token (or any named secret)
    using the request's KEK. Returns None when the user hasn't saved one."""
    from cryptography.exceptions import InvalidTag  # noqa: PLC0415

    from app.crypto import secrets as secrets_mod  # noqa: PLC0415
    from app.models.api_key import ApiKey  # noqa: PLC0415

    row = (
        await db.execute(
            select(ApiKey).where(ApiKey.user_id == auth.user.id, ApiKey.key_name == key_name)
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    try:
        return secrets_mod.unwrap_secret(
            secrets_mod.WrappedSecret(
                ciphertext=row.ciphertext,
                ciphertext_nonce=row.ciphertext_nonce,
                dek_wrapped=row.dek_wrapped,
                dek_wrap_nonce=row.dek_wrap_nonce,
            ),
            kek=auth.kek,
        )
    except InvalidTag:
        return None
